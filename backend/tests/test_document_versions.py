from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import GuardedSession, get_db
from app.documents.storage import MAX_UPLOAD_BYTES
from app.main import create_app
from app.persistence.models import BidProject, Document, DocumentVersion
from app.settings import get_settings


@pytest.fixture
def app(
    db_session: GuardedSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str = "测试项目") -> int:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return int(response.json()["id"])


def _upload_project_document(
    client: TestClient,
    project_id: int,
    content: bytes,
    *,
    filename: str = "招标文件.pdf",
    role: str = "tender",
) -> Response:
    return client.post(
        f"/api/projects/{project_id}/documents",
        params={"role": role},
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_rejects_executable_extension(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(
        client,
        project_id,
        b"not really an executable",
        filename="payload.exe",
    )

    assert response.status_code == 415
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


def test_same_bytes_do_not_create_duplicate_version(
    client: TestClient, db_session: GuardedSession
) -> None:
    project_id = _create_project(client)
    content = b"same tender bytes"

    first = _upload_project_document(client, project_id, content)
    second = _upload_project_document(
        client, project_id, content, filename="renamed.pdf"
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["version_id"] == first.json()["version_id"]
    assert second.json()["version_number"] == 1
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert db_session.scalar(select(func.count()).select_from(DocumentVersion)) == 1


def test_changed_bytes_create_next_version(
    client: TestClient, db_session: GuardedSession
) -> None:
    project_id = _create_project(client)

    first = _upload_project_document(client, project_id, b"first bytes")
    second = _upload_project_document(client, project_id, b"changed bytes")
    listed = client.get(f"/api/projects/{project_id}/documents")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["document_id"] == first.json()["document_id"]
    assert second.json()["version_number"] == 2
    assert listed.status_code == 200
    assert [version["id"] for version in listed.json()[0]["versions"]] == [
        first.json()["version_id"],
        second.json()["version_id"],
    ]
    assert db_session.get(DocumentVersion, first.json()["version_id"]) is not None


def test_storage_path_stays_inside_configured_root(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(
        client,
        project_id,
        b"safe content",
        filename=r"..\..\outside.PDF",
    )

    assert response.status_code == 201
    version = db_session.get(DocumentVersion, response.json()["version_id"])
    document = db_session.get(Document, response.json()["document_id"])
    assert version is not None
    assert document is not None
    storage_root = (tmp_path / "uploads").resolve()
    stored = Path(version.storage_path).resolve()
    assert storage_root in stored.parents
    assert stored.suffix == ".pdf"
    assert document.display_name == "outside.PDF"
    assert ".." not in document.display_name


def test_company_document_can_be_reused_without_project_id(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    first_project = _create_project(client, "项目一")
    second_project = _create_project(client, "项目二")
    content = b"shared company evidence"

    first = client.post(
        "/api/company-evidence",
        files={"file": ("license.pdf", content, "application/pdf")},
    )
    second = client.post(
        "/api/company-evidence",
        files={"file": ("renamed-license.pdf", content, "application/pdf")},
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["document_id"] == first.json()["document_id"]
    assert second.json()["version_id"] == first.json()["version_id"]
    assert second.json()["created"] is False
    document = db_session.get(Document, first.json()["document_id"])
    assert document is not None
    assert document.project_id is None
    assert db_session.get(BidProject, first_project) is not None
    assert db_session.get(BidProject, second_project) is not None
    assert db_session.scalar(select(func.count()).select_from(Document)) == 1
    assert db_session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    assert (
        len([path for path in (tmp_path / "uploads").rglob("*") if path.is_file()]) == 1
    )


def test_empty_file_is_rejected_without_database_or_disk_changes(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(client, project_id, b"")

    assert response.status_code == 400
    assert response.json() == {"detail": "File is empty"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


def test_file_over_size_limit_is_rejected(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(
        client,
        project_id,
        b"x" * (MAX_UPLOAD_BYTES + 1),
    )

    assert response.status_code == 413
    assert db_session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
    assert not (tmp_path / "uploads").exists()


def test_uppercase_pdf_extension_is_accepted(client: TestClient) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(
        client,
        project_id,
        b"uppercase extension",
        filename="TENDER.PDF",
    )

    assert response.status_code == 201
    assert response.json()["parse_status"] == "pending"
    assert response.json()["uploaded_at"].endswith("Z")


def test_missing_project_does_not_create_document_or_file(
    client: TestClient, db_session: GuardedSession, tmp_path: Path
) -> None:
    response = _upload_project_document(client, 999_999, b"orphan bytes")

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


@pytest.mark.parametrize("role", ["company", "other"])
def test_project_upload_rejects_invalid_role(
    client: TestClient, db_session: GuardedSession, tmp_path: Path, role: str
) -> None:
    project_id = _create_project(client)

    response = _upload_project_document(
        client,
        project_id,
        b"invalid role bytes",
        role=role,
    )

    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert not (tmp_path / "uploads").exists()


def test_get_documents_has_stable_document_and_version_order(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    tender_one = _upload_project_document(client, project_id, b"tender v1")
    tender_two = _upload_project_document(client, project_id, b"tender v2")
    proposal = _upload_project_document(
        client,
        project_id,
        b"proposal v1",
        filename="proposal.docx",
        role="proposal",
    )

    response = client.get(f"/api/projects/{project_id}/documents")

    assert response.status_code == 200
    assert [item["role"] for item in response.json()] == ["proposal", "tender"]
    assert [version["id"] for version in response.json()[0]["versions"]] == [
        proposal.json()["version_id"]
    ]
    assert [version["id"] for version in response.json()[1]["versions"]] == [
        tender_one.json()["version_id"],
        tender_two.json()["version_id"],
    ]
    missing = client.get("/api/projects/999999/documents")
    assert missing.status_code == 404


def test_database_failure_keeps_published_content_but_removes_temporary_files(
    app: FastAPI, db_session: GuardedSession, tmp_path: Path
) -> None:
    engine = db_session.get_bind()
    project = BidProject(name="数据库失败项目", deadline_at=None)
    db_session.add(project)
    db_session.commit()
    fail_next_version_insert = True

    def fail_one_version_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: Any,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal fail_next_version_insert
        if fail_next_version_insert and "INSERT INTO document_versions" in statement:
            fail_next_version_insert = False
            raise OperationalError(statement, parameters, Exception("forced failure"))

    event.listen(engine, "before_cursor_execute", fail_one_version_insert)
    try:
        with TestClient(app, raise_server_exceptions=False) as error_client:
            response = _upload_project_document(
                error_client,
                project.id,
                b"must be retained",
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_one_version_insert)

    assert response.status_code == 500
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert db_session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
    uploads = tmp_path / "uploads"
    published_files = [path for path in uploads.rglob("*") if path.is_file()]
    assert len(published_files) == 1
    assert published_files[0].read_bytes() == b"must be retained"
    assert not list(uploads.rglob("*.tmp"))
