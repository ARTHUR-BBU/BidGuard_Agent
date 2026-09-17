import asyncio
import inspect
import os
import threading
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import UniqueConstraint, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker
from starlette import formparsers
from starlette.types import Message, Scope

from app.api import documents as document_api
from app.db import Base, GuardedSession, build_engine, get_db
from app.documents import storage
from app.documents.storage import MAX_UPLOAD_BYTES
from app.domain.enums import DocumentRole
from app.main import create_app
from app.middleware import body_limit
from app.persistence.models import BidProject, Document, DocumentVersion
from app.services import ingestion
from app.services.ingestion import (
    IngestionResult,
    ingest_company_document,
    ingest_project_document,
    safe_display_name,
)
from app.settings import get_settings

MULTIPART_OVERHEAD_BYTES = 1024 * 1024
RAW_UPLOAD_BODY_LIMIT = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES


@pytest.fixture
def hardened_app(
    db_session: GuardedSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        class_=GuardedSession,
        expire_on_commit=False,
    )

    def get_request_db() -> Iterator[GuardedSession]:
        with session_factory() as request_session:
            try:
                yield request_session
            except Exception:
                request_session.rollback()
                raise

    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setattr("app.main.engine", db_session.get_bind())
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_db] = get_request_db
    yield application
    application.dependency_overrides.clear()
    get_settings.cache_clear()


async def _body_chunks(total_size: int) -> AsyncIterator[bytes]:
    chunk = b"x" * (1024 * 1024)
    remaining = total_size
    while remaining:
        part = chunk[: min(len(chunk), remaining)]
        remaining -= len(part)
        yield part


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"content-length": "1"}])
@pytest.mark.parametrize("path", ["/api/company-evidence", "/api/projects/1/documents"])
async def test_raw_upload_body_limit_rejects_stream_without_trusted_length(
    hardened_app: FastAPI,
    db_session: GuardedSession,
    tmp_path: Path,
    headers: dict[str, str],
    path: str,
) -> None:
    request_headers = {"content-type": "application/octet-stream", **headers}
    transport = httpx.ASGITransport(app=hardened_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            path,
            content=_body_chunks(RAW_UPLOAD_BODY_LIMIT + 1),
            headers=request_headers,
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


@pytest.mark.asyncio
async def test_raw_upload_body_limit_rejects_large_declared_length_before_route(
    hardened_app: FastAPI,
    db_session: GuardedSession,
    tmp_path: Path,
) -> None:
    transport = httpx.ASGITransport(app=hardened_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/company-evidence",
            content=b"small body",
            headers={
                "content-type": "application/octet-stream",
                "content-length": str(RAW_UPLOAD_BODY_LIMIT + 1),
            },
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


def test_upload_body_limit_does_not_affect_normal_json_posts(
    hardened_app: FastAPI,
) -> None:
    with TestClient(hardened_app) as client:
        response = client.post(
            "/api/projects",
            json={"name": "正常项目"},
            headers={"content-length": str(RAW_UPLOAD_BODY_LIMIT + 1)},
        )

    assert response.status_code == 201


def test_upload_routes_are_sync_threadpool_endpoints() -> None:
    assert not inspect.iscoroutinefunction(document_api.upload_project_document)
    assert not inspect.iscoroutinefunction(document_api.upload_company_evidence)


@pytest.mark.asyncio
async def test_slow_upload_work_does_not_block_health_request(
    hardened_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    timed_out = threading.Event()
    original = document_api.ingest_company_document

    def slow_ingestion(*args: Any, **kwargs: Any) -> IngestionResult:
        entered.set()
        if not release.wait(10):
            timed_out.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(document_api, "ingest_company_document", slow_ingestion)
    transport = httpx.ASGITransport(app=hardened_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        upload = asyncio.create_task(
            client.post(
                "/api/company-evidence",
                files={"file": ("evidence.pdf", b"evidence", "application/pdf")},
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            health = await asyncio.wait_for(client.get("/api/health"), timeout=5)
            assert health.status_code == 200
            # The health reply must arrive while ingestion is still waiting.
            assert not timed_out.is_set()
            assert not upload.done()
        finally:
            release.set()
        assert (await upload).status_code == 201


class RecordingReader(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.largest_requested_read = 0
        self.total_read = 0

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None and 0 < size <= 1024 * 1024, "all reads must be bounded"
        self.largest_requested_read = max(self.largest_requested_read, size)
        data = super().read(size)
        self.total_read += len(data)
        return data


def test_upload_hashing_reads_bounded_chunks_and_rewinds() -> None:
    hash_upload = getattr(storage, "hash_upload", None)
    assert callable(hash_upload), "streaming hash_upload is required"
    reader = RecordingReader(b"x" * (3 * 1024 * 1024 + 17))

    digest, size = hash_upload(reader)

    assert digest == storage.sha256_bytes(b"x" * (3 * 1024 * 1024 + 17))
    assert size == 3 * 1024 * 1024 + 17
    assert reader.largest_requested_read <= 1024 * 1024
    assert reader.tell() == 0


def _create_project(client: TestClient) -> int:
    response = client.post("/api/projects", json={"name": "hardening"})
    assert response.status_code == 201
    return int(response.json()["id"])


def _upload(
    client: TestClient, project_id: int, content: bytes, filename: str = "a.pdf"
) -> Response:
    return client.post(
        f"/api/projects/{project_id}/documents",
        params={"role": "tender"},
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_database_hit_restores_missing_content(
    hardened_app: FastAPI,
    db_session: GuardedSession,
) -> None:
    with TestClient(hardened_app) as client:
        project_id = _create_project(client)
        first = _upload(client, project_id, b"recover me")
        version = db_session.get(DocumentVersion, first.json()["version_id"])
        assert version is not None
        stored_path = Path(version.storage_path)
        stored_path.unlink()

        repeated = _upload(client, project_id, b"recover me", "renamed.docx")

    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert stored_path.read_bytes() == b"recover me"
    assert stored_path.suffix == ".pdf"


def test_database_hit_rejects_corrupt_content_without_overwriting(
    hardened_app: FastAPI,
    db_session: GuardedSession,
) -> None:
    with TestClient(hardened_app) as client:
        project_id = _create_project(client)
        first = _upload(client, project_id, b"original")
        version = db_session.get(DocumentVersion, first.json()["version_id"])
        assert version is not None
        stored_path = Path(version.storage_path)
        stored_path.write_bytes(b"corrupt!")

        repeated = _upload(client, project_id, b"original")

    assert repeated.status_code == 500
    assert repeated.json() == {
        "detail": "Stored document failed integrity verification"
    }
    assert stored_path.read_bytes() == b"corrupt!"


def test_database_hit_rejects_storage_path_outside_root(
    hardened_app: FastAPI,
    db_session: GuardedSession,
    tmp_path: Path,
) -> None:
    with TestClient(hardened_app) as client:
        project_id = _create_project(client)
        first = _upload(client, project_id, b"outside")
        version = db_session.get(DocumentVersion, first.json()["version_id"])
        assert version is not None
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"outside")
        version.storage_path = str(outside)
        db_session.commit()

        repeated = _upload(client, project_id, b"outside")

    assert repeated.status_code == 500
    assert repeated.json() == {
        "detail": "Stored document failed integrity verification"
    }
    assert outside.read_bytes() == b"outside"


def test_link_capability_failure_returns_503_and_cleans_temporary_file(
    hardened_app: FastAPI,
    db_session: GuardedSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported_link(_source: os.PathLike[str], _target: os.PathLike[str]) -> None:
        raise OSError("hard links unavailable")

    monkeypatch.setattr(storage.os, "link", unsupported_link)
    with TestClient(hardened_app, raise_server_exceptions=False) as client:
        project_id = _create_project(client)
        response = _upload(client, project_id, b"cannot publish")

    assert response.status_code == 503
    assert response.json() == {"detail": "Document storage is unavailable"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not [
        path for path in uploads.rglob("*") if path.is_file()
    ]


def test_display_name_removes_controls_normalizes_and_limits_length(
    hardened_app: FastAPI,
    db_session: GuardedSession,
) -> None:
    dangerous_name = "../\x00\u202e\u2066" + "Ａ" * 600 + "中文👩\u200d💻.PDF"
    with TestClient(hardened_app) as client:
        project_id = _create_project(client)
        response = _upload(client, project_id, b"name", dangerous_name)

    assert response.status_code == 201
    document = db_session.get(Document, response.json()["document_id"])
    assert document is not None
    assert len(document.display_name) <= 500
    assert document.display_name.endswith(".PDF")
    assert "\x00" not in document.display_name
    assert "\u202e" not in document.display_name
    assert "\u2066" not in document.display_name
    assert "Ａ" not in document.display_name
    assert "中文👩\u200d💻" in document.display_name


def test_same_digest_with_different_suffix_reuses_first_path(
    hardened_app: FastAPI,
    db_session: GuardedSession,
    tmp_path: Path,
) -> None:
    with TestClient(hardened_app) as client:
        project_id = _create_project(client)
        first = _upload(client, project_id, b"same format identity", "first.pdf")
        repeated = _upload(client, project_id, b"same format identity", "second.docx")

    assert repeated.status_code == 200
    assert repeated.json()["version_id"] == first.json()["version_id"]
    version = db_session.get(DocumentVersion, first.json()["version_id"])
    assert version is not None
    assert Path(version.storage_path).suffix == ".pdf"
    assert (
        len([path for path in (tmp_path / "uploads").rglob("*") if path.is_file()]) == 1
    )


def test_document_model_has_cross_worker_identity_constraints() -> None:
    assert "company_content_sha256" in Document.__table__.c
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["documents"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("project_id", "role") in unique_column_sets
    assert ("company_content_sha256",) in unique_column_sets
    version_unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["document_versions"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("document_id", "sha256") in version_unique_sets


def _run_concurrent_uploads(
    tmp_path: Path,
    *,
    company: bool,
    contents: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, list[int], int, int]:
    # Separate DB connections with the process-local optimization disabled.
    monkeypatch.setattr(ingestion, "_INGESTION_LOCK", nullcontext())
    database_path = tmp_path / "concurrency.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        class_=GuardedSession,
        expire_on_commit=False,
    )
    project_id: int | None = None
    if not company:
        with session_factory() as session:
            project = BidProject(name="concurrent", deadline_at=None)
            session.add(project)
            session.commit()
            project_id = project.id
    barrier = threading.Barrier(2)
    insert_barrier = threading.Barrier(2)
    worker_state = threading.local()
    storage_root = tmp_path / "concurrent-uploads"

    def race_document_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "INSERT INTO documents " in statement and not getattr(
            worker_state, "waited", False
        ):
            worker_state.waited = True
            insert_barrier.wait(timeout=10)

    # Both real connections must attempt the same identity before either commits.
    event.listen(engine, "before_cursor_execute", race_document_insert)

    def worker(index: int) -> int:
        with session_factory() as session:
            barrier.wait()
            if company:
                result = ingest_company_document(
                    session,
                    storage_root,
                    f"company-{index}.pdf",
                    BytesIO(contents[index]),
                    digest=storage.sha256_bytes(contents[index]),
                    size=len(contents[index]),
                )
            else:
                assert project_id is not None
                result = ingest_project_document(
                    session,
                    storage_root,
                    project_id,
                    DocumentRole.TENDER,
                    f"tender-{index}.pdf",
                    BytesIO(contents[index]),
                    digest=storage.sha256_bytes(contents[index]),
                    size=len(contents[index]),
                )
            return result.version.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        version_ids = list(executor.map(worker, range(2)))
    with session_factory() as session:
        document_count = session.scalar(select(func.count()).select_from(Document)) or 0
        version_numbers = list(
            session.scalars(
                select(DocumentVersion.version_number).order_by(
                    DocumentVersion.version_number
                )
            )
        )
        version_count = (
            session.scalar(select(func.count()).select_from(DocumentVersion)) or 0
        )
    engine.dispose()
    return (
        int(document_count),
        version_numbers,
        int(version_count),
        len(set(version_ids)),
    )


def test_concurrent_same_project_role_and_content_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_count, version_numbers, version_count, distinct_result_ids = (
        _run_concurrent_uploads(
            tmp_path,
            company=False,
            contents=(b"same", b"same"),
            monkeypatch=monkeypatch,
        )
    )
    assert (document_count, version_numbers, version_count, distinct_result_ids) == (
        1,
        [1],
        1,
        1,
    )


def test_concurrent_same_project_role_changed_content_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_count, version_numbers, version_count, distinct_result_ids = (
        _run_concurrent_uploads(
            tmp_path,
            company=False,
            contents=(b"first", b"second"),
            monkeypatch=monkeypatch,
        )
    )
    assert (document_count, version_numbers, version_count, distinct_result_ids) == (
        1,
        [1, 2],
        2,
        2,
    )


def test_concurrent_company_content_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_count, version_numbers, version_count, distinct_result_ids = (
        _run_concurrent_uploads(
            tmp_path,
            company=True,
            contents=(b"same company", b"same company"),
            monkeypatch=monkeypatch,
        )
    )
    assert (document_count, version_numbers, version_count, distinct_result_ids) == (
        1,
        [1],
        1,
        1,
    )


@pytest.mark.parametrize(
    "identity", ["project", "company", "version_digest", "version_number"]
)
def test_two_sessions_cannot_commit_duplicate_identities(
    tmp_path: Path, identity: str
) -> None:
    assert "company_content_sha256" in Document.__table__.c
    engine = build_engine(f"sqlite:///{(tmp_path / 'unique.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with GuardedSession(engine) as first, GuardedSession(engine) as second:
            project = BidProject(name="constraints")
            first.add(project)
            first.commit()
            project_id = project.id
            if identity in {"project", "company"}:
                kwargs = {
                    "project_id": None if identity == "company" else project_id,
                    "role": "company" if identity == "company" else "tender",
                    "company_content_sha256": "a" * 64
                    if identity == "company"
                    else None,
                    "display_name": "test.pdf",
                }
                first.add(Document(**kwargs))
                second.add(Document(**kwargs))
            else:
                document = Document(
                    project_id=project_id, role="tender", display_name="test.pdf"
                )
                first.add(document)
                first.commit()
                document_id = document.id
                first.add(
                    DocumentVersion(
                        document_id=document_id,
                        version_number=1,
                        sha256="a" * 64,
                        storage_path="test.pdf",
                    )
                )
                second.add(
                    DocumentVersion(
                        document_id=document_id,
                        version_number=2 if identity == "version_digest" else 1,
                        sha256="a" * 64 if identity == "version_digest" else "b" * 64,
                        storage_path="other.pdf",
                    )
                )
            first.commit()
            with pytest.raises(IntegrityError):
                second.commit()
            second.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("release_on_retry", [True, False])
def test_real_sqlite_lock_retries_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_on_retry: bool
) -> None:
    engine = build_engine(f"sqlite:///{(tmp_path / 'locked.db').as_posix()}")
    Base.metadata.create_all(engine)
    delays: list[float] = []
    try:
        with GuardedSession(engine, expire_on_commit=False) as session:
            session.connection().exec_driver_sql("PRAGMA busy_timeout=1")
            # Keep this configured connection checked out while acquiring the
            # competing lock; otherwise the pool can swap the two connections.
            with engine.connect() as holder:
                holder.exec_driver_sql("BEGIN EXCLUSIVE")

                def retry_pause(delay: float) -> None:
                    delays.append(delay)
                    if release_on_retry:
                        holder.rollback()

                monkeypatch.setattr(ingestion.time, "sleep", retry_pause)

                def upload() -> IngestionResult:
                    return ingest_company_document(
                        session,
                        tmp_path / "uploads",
                        "busy.pdf",
                        BytesIO(b"busy"),
                        digest=storage.sha256_bytes(b"busy"),
                        size=4,
                    )

                if release_on_retry:
                    assert upload().created
                    assert len(delays) == 1
                else:
                    with pytest.raises(OperationalError, match="locked"):
                        upload()
                    assert len(delays) == 4
                holder.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "role,has_project,digest",
    [
        ("company", True, "a" * 64),
        ("company", False, None),
        ("tender", True, "a" * 64),
        ("proposal", False, None),
        ("other", True, None),
    ],
)
def test_database_rejects_invalid_document_identity(
    db_session: GuardedSession, role: str, has_project: bool, digest: str | None
) -> None:
    assert "company_content_sha256" in Document.__table__.c
    project = BidProject(name="identity")
    db_session.add(project)
    db_session.commit()
    db_session.add(
        Document(
            project_id=project.id if has_project else None,
            role=role,
            display_name="test.pdf",
            company_content_sha256=digest,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_display_name_filters_actual_controls_and_preserves_language() -> None:
    assert (
        safe_display_name("../\x00\x7f\x85\u202e\u2066Ａ中文👩\u200d💻.PDF", ".pdf")
        == "A中文👩\u200d💻.PDF"
    )
    assert safe_display_name("\x00\u202e", ".pdf") == "upload.pdf"


def test_hash_stops_at_limit_and_rewinds(monkeypatch: pytest.MonkeyPatch) -> None:
    hash_upload = getattr(storage, "hash_upload", None)
    assert callable(hash_upload)
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 1024 * 1024)
    reader = RecordingReader(b"x" * (4 * 1024 * 1024))
    with pytest.raises(ValueError, match="50 MiB"):
        hash_upload(reader)
    assert reader.tell() == 0
    assert reader.total_read <= 2 * 1024 * 1024


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path namespace")
def test_windows_extended_resolved_path_is_not_a_false_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_resolve = Path.resolve

    def extended_file_resolve(path: Path, strict: bool = False) -> Path:
        resolved = original_resolve(path, strict=strict)
        if path.suffix == ".pdf" and not str(resolved).startswith("\\\\?\\"):
            return Path("\\\\?\\" + str(resolved))
        return resolved

    monkeypatch.setattr(Path, "resolve", extended_file_resolve)
    content = b"windows path race"
    digest = storage.sha256_bytes(content)
    stored, created = storage.store_content(
        tmp_path / "uploads", digest, "file.pdf", RecordingReader(content), len(content)
    )
    assert created
    assert stored.read_bytes() == content


@pytest.mark.asyncio
async def test_multipart_limit_cleans_parser_temporary_files(
    hardened_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(body_limit, "RAW_UPLOAD_BODY_LIMIT", 2 * 1024 * 1024)
    temporary_files: list[SpooledTemporaryFile[bytes]] = []
    original_spool = formparsers.SpooledTemporaryFile

    def recording_spool(*args: Any, **kwargs: Any) -> SpooledTemporaryFile[bytes]:
        temporary = original_spool(*args, **kwargs)
        temporary_files.append(temporary)
        return temporary

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", recording_spool)

    async def multipart_body() -> AsyncIterator[bytes]:
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\n\r\n'
        async for chunk in _body_chunks(3 * 1024 * 1024):
            yield chunk
        yield b"\r\n--test--\r\n"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hardened_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/company-evidence",
            content=multipart_body(),
            headers={"content-type": "multipart/form-data; boundary=test"},
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert temporary_files and all(item.closed for item in temporary_files)


@pytest.mark.asyncio
async def test_disconnect_during_upload_sends_no_response_or_server_error(
    hardened_app: FastAPI,
) -> None:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/company-evidence",
        "raw_path": b"/api/company-evidence",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    incoming: list[Message] = [
        {"type": "http.request", "body": b"--test\r\n", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await hardened_app(scope, receive, send)
    assert sent == []


@pytest.mark.parametrize("action", ["missing", "same_size_corrupt", "outside"])
def test_company_database_hit_verifies_and_recovers_first_path(
    hardened_app: FastAPI, db_session: GuardedSession, tmp_path: Path, action: str
) -> None:
    with TestClient(hardened_app) as client:
        first = client.post(
            "/api/company-evidence", files={"file": ("first.pdf", b"company")}
        )
        assert first.status_code == 201
        version = db_session.get(DocumentVersion, first.json()["version_id"])
        assert version is not None
        original_path = Path(version.storage_path)
        if action == "missing":
            original_path.unlink()
        elif action == "same_size_corrupt":
            original_path.write_bytes(b"corrupt")
        else:
            outside = tmp_path / "external.pdf"
            outside.write_bytes(b"company")
            version.storage_path = str(outside)
            db_session.commit()
        repeated = client.post(
            "/api/company-evidence", files={"file": ("other.docx", b"company")}
        )
    if action == "missing":
        assert repeated.status_code == 200
        assert repeated.json()["created"] is False
        assert repeated.json()["version_id"] == first.json()["version_id"]
        assert original_path.read_bytes() == b"company"
        assert len(list((tmp_path / "uploads").rglob("*.pdf"))) == 1
        assert not list((tmp_path / "uploads").rglob("*.docx"))
    else:
        assert repeated.status_code == 500
        assert repeated.json() == {
            "detail": "Stored document failed integrity verification"
        }
        assert original_path.read_bytes() == (
            b"corrupt" if action == "same_size_corrupt" else b"company"
        )
