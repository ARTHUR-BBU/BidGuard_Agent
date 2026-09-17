import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from app.db import GuardedSession
from app.documents.storage import ensure_content, safe_storage_path, store_content
from app.domain.enums import DocumentRole
from app.persistence.models import BidProject, Document, DocumentVersion

_INGESTION_LOCK = RLock()
_MAX_DB_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document: Document
    version: DocumentVersion
    created: bool


class ProjectNotFoundError(Exception):
    pass


def safe_display_name(original_name: str, suffix: str) -> str:
    normalized = unicodedata.normalize("NFKC", original_name)
    basename = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(
        char
        for char in basename
        if unicodedata.category(char) not in {"Cc", "Cf"}
        or char in {"\u200c", "\u200d"}
    ).strip()
    if not basename:
        return f"upload{suffix}"
    if len(basename) > 500:
        # Preserve both the beginning and the human-readable tail/extension.
        basename = basename[:249] + "…" + basename[-250:]
    return basename


def ingest_project_document(
    session: GuardedSession,
    storage_root: Path,
    project_id: int,
    role: DocumentRole,
    original_name: str,
    reader: BinaryIO,
    *,
    digest: str,
    size: int,
) -> IngestionResult:
    return _ingest(
        session,
        storage_root,
        project_id=project_id,
        role=role,
        original_name=original_name,
        reader=reader,
        digest=digest,
        size=size,
    )


def ingest_company_document(
    session: GuardedSession,
    storage_root: Path,
    original_name: str,
    reader: BinaryIO,
    *,
    digest: str,
    size: int,
) -> IngestionResult:
    return _ingest(
        session,
        storage_root,
        project_id=None,
        role=DocumentRole.COMPANY,
        original_name=original_name,
        reader=reader,
        digest=digest,
        size=size,
    )


def _find_document(
    session: GuardedSession, project_id: int | None, role: DocumentRole, digest: str
) -> Document | None:
    if role == DocumentRole.COMPANY:
        return session.scalar(
            select(Document).where(Document.company_content_sha256 == digest)
        )
    if session.get(BidProject, project_id) is None:
        raise ProjectNotFoundError
    return session.scalar(
        select(Document).where(
            Document.project_id == project_id,
            Document.role == role.value,
        )
    )


def _find_version(
    session: GuardedSession, document: Document | None, digest: str
) -> DocumentVersion | None:
    if document is None:
        return None
    return session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.sha256 == digest,
        )
    )


def _sqlite_busy(error: OperationalError) -> bool:
    code = getattr(error.orig, "sqlite_errorcode", None)
    return (
        isinstance(error.orig, sqlite3.OperationalError)
        and isinstance(code, int)
        and (code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
    )


def _ingest(
    session: GuardedSession,
    storage_root: Path,
    *,
    project_id: int | None,
    role: DocumentRole,
    original_name: str,
    reader: BinaryIO,
    digest: str,
    size: int,
) -> IngestionResult:
    safe_storage_path(storage_root, digest, original_name)
    target: Path | None = None
    try:
        for attempt in range(_MAX_DB_ATTEMPTS):
            try:
                # This lock reduces contention within one worker. Database unique
                # constraints and retry/re-read provide correctness across workers.
                with _INGESTION_LOCK:
                    document = _find_document(session, project_id, role, digest)
                    version = _find_version(session, document, digest)
                    if document is not None and version is not None:
                        result = IngestionResult(document, version, created=False)
                    elif target is not None:
                        if document is None:
                            document = Document(
                                project_id=project_id,
                                role=role.value,
                                company_content_sha256=digest
                                if role == DocumentRole.COMPANY
                                else None,
                                display_name=safe_display_name(
                                    original_name, target.suffix
                                ),
                            )
                            session.add(document)
                            session.flush()
                        next_version = (
                            session.scalar(
                                select(func.max(DocumentVersion.version_number)).where(
                                    DocumentVersion.document_id == document.id
                                )
                            )
                            or 0
                        ) + 1
                        version = DocumentVersion(
                            document=document,
                            version_number=next_version,
                            sha256=digest,
                            storage_path=str(target),
                            parse_status="pending",
                        )
                        session.add(version)
                        session.commit()
                        result = IngestionResult(document, version, created=True)
                    else:
                        result = None
                        # Do not hold a database transaction while publishing bytes.
                        session.rollback()

                if result is not None:
                    # Every reuse, including conflict recovery, verifies the actual
                    # stored path and restores missing content from the fresh upload.
                    ensure_content(
                        storage_root,
                        Path(result.version.storage_path),
                        digest,
                        reader,
                        size,
                    )
                    return result
                target, _ = store_content(
                    storage_root, digest, original_name, reader, size
                )
            except (IntegrityError, OperationalError) as error:
                session.rollback()
                if (
                    attempt == _MAX_DB_ATTEMPTS - 1
                    or isinstance(error, OperationalError)
                    and not _sqlite_busy(error)
                ):
                    raise
                time.sleep(0.01 * 2**attempt)
        raise RuntimeError("Document ingestion retries exhausted")
    except Exception:
        session.rollback()
        # Never delete published content here. Another worker may have committed
        # a reference to it; safe orphans can be collected separately later.
        raise


def list_project_documents(
    session: GuardedSession,
    project_id: int,
) -> list[Document]:
    if session.get(BidProject, project_id) is None:
        raise ProjectNotFoundError
    return list(
        session.scalars(
            select(Document)
            .options(selectinload(Document.versions))
            .where(Document.project_id == project_id)
            .where(
                Document.role.in_(
                    (DocumentRole.TENDER.value, DocumentRole.PROPOSAL.value)
                )
            )
            .order_by(Document.role, Document.id)
        )
    )
