import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import BinaryIO

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from app.db import GuardedSession
from app.documents.parser import (
    SAFE_PARSE_MESSAGES,
    DocumentParseError,
    parse_document,
)
from app.documents.storage import (
    StorageIntegrityError,
    ensure_content,
    safe_storage_path,
    store_content,
    verify_stored_content,
)
from app.domain.enums import DocumentRole
from app.domain.schemas import ParseCoverageIssue, ParsedDocument
from app.persistence.models import (
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
)

_INGESTION_LOCK = RLock()
_MAX_DB_ATTEMPTS = 5
_PARSE_LEASE_TIMEOUT = timedelta(minutes=15)


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
                            size_bytes=size,
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
                    stale_parse = (
                        result.version.parse_status == "parsing"
                        and (
                            result.version.parse_attempt_started_at is None
                            or result.version.parse_attempt_started_at
                            <= datetime.now(UTC) - _PARSE_LEASE_TIMEOUT
                        )
                    )
                    should_parse = result.created or result.version.parse_status in {
                        "pending",
                        "failed",
                    } or stale_parse
                    if result.version.size_bytes is None:
                        result.version.size_bytes = size
                        session.commit()
                    if should_parse:
                        parse_document_version(
                            session,
                            result.version.id,
                            storage_root,
                        )
                        session.refresh(result.version)
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


def _coverage_payload(parsed: ParsedDocument) -> dict[str, object]:
    return parsed.model_dump(mode="json", exclude={"chunks"})


def _parse_outcome(
    parsed: ParsedDocument,
) -> tuple[str, str | None, str | None]:
    incomplete_issues = [
        issue for issue in parsed.coverage_issues if issue.code != "blank_page"
    ]
    incomplete = bool(
        parsed.failed_pages or parsed.ocr_pages or incomplete_issues
    )
    if parsed.chunks:
        if incomplete:
            return (
                "partial_failure",
                "incomplete_coverage",
                "Document parsing is incomplete",
            )
        return "parsed", None, None
    if parsed.needs_ocr:
        return "needs_ocr", "ocr_required", "Document requires OCR"
    if incomplete:
        return (
            "failed",
            "incomplete_coverage",
            "Document parsing is incomplete",
        )
    return "failed", "no_extractable_text", "Document contains no extractable text"


def _finalize_parse_attempt(
    session: GuardedSession,
    version_id: int,
    attempt_id: str,
    parsed: ParsedDocument,
    *,
    status: str,
    error_code: str | None,
    error_message: str | None,
) -> bool:
    session.rollback()
    claimed = session.execute(
        update(DocumentVersion)
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.parse_attempt_id == attempt_id,
        )
        .values(parse_attempt_id=attempt_id)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        session.rollback()
        return False
    current = session.get(DocumentVersion, version_id)
    if current is None:
        session.rollback()
        return False
    if status == "failed" and current.chunks:
        coverage = current.parse_coverage or {}
        issues = coverage.get("coverage_issues") or []
        previous_status = (
            "partial_failure"
            if coverage.get("failed_pages")
            or coverage.get("ocr_pages")
            or any(
                isinstance(issue, dict) and issue.get("code") != "blank_page"
                for issue in issues
            )
            else "parsed"
        )
        current.parse_status = previous_status
        current.parse_error_code = error_code
        current.parse_error = (
            "Latest parse attempt failed; previous parsed content retained"
        )
        current.parse_attempt_id = None
        current.parse_attempt_started_at = None
        session.commit()
        return False
    session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
    )
    session.add_all(
        DocumentChunk(
            document_version_id=version_id,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            section_path=chunk.section_path,
            text=chunk.text,
        )
        for chunk in parsed.chunks
    )
    current.parse_status = status
    current.parse_error_code = error_code
    current.parse_error = error_message
    current.parse_coverage = _coverage_payload(parsed)
    current.parse_attempt_id = None
    current.parse_attempt_started_at = None
    session.commit()
    return True


def parse_document_version(
    session: GuardedSession,
    version_id: int,
    storage_root: Path,
) -> bool:
    """Parse one immutable version outside a DB transaction, then replace atomically.

    A fresh attempt token makes a late older parse unable to overwrite a newer result.
    The source file is always retained, including validation and parse failures.
    """
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise ValueError("Document version not found")
    attempt_id = str(uuid.uuid4())
    storage_path = Path(version.storage_path)
    digest = version.sha256
    size = version.size_bytes
    session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .values(
            parse_status="parsing",
            parse_error_code=None,
            parse_error=None,
            parse_attempt_id=attempt_id,
            parse_attempt_started_at=datetime.now(UTC),
        )
    )
    session.commit()

    try:
        if size is None:
            raise StorageIntegrityError("Stored content size metadata is missing")
        verified_path = verify_stored_content(
            storage_root, storage_path, digest, size
        )
        parsed = parse_document(verified_path)
        status, error_code, error_message = _parse_outcome(parsed)
    except StorageIntegrityError:
        parsed = ParsedDocument(
            chunks=[],
            coverage_issues=[
                ParseCoverageIssue(
                    code="source_unverified", section_path="$document"
                )
            ],
        )
        status = "failed"
        error_code = "source_integrity_failed"
        error_message = SAFE_PARSE_MESSAGES[error_code]
    except DocumentParseError as error:
        parsed = ParsedDocument(
            chunks=[],
            coverage_issues=[
                ParseCoverageIssue(code=error.code, section_path="$document")
            ],
        )
        status = "failed"
        error_code = error.code
        error_message = SAFE_PARSE_MESSAGES[error.code]
    except Exception:  # noqa: BLE001 - this is the safe parser isolation boundary.
        parsed = ParsedDocument(
            chunks=[],
            coverage_issues=[
                ParseCoverageIssue(code="parse_failed", section_path="$document")
            ],
        )
        status = "failed"
        error_code = "parse_failed"
        error_message = SAFE_PARSE_MESSAGES[error_code]
    return _finalize_parse_attempt(
        session,
        version_id,
        attempt_id,
        parsed,
        status=status,
        error_code=error_code,
        error_message=error_message,
    )


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
