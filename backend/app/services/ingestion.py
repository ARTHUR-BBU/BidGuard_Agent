from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db import GuardedSession
from app.documents.storage import safe_storage_path, sha256_bytes, store_content
from app.domain.enums import DocumentRole
from app.persistence.models import BidProject, Document, DocumentVersion

_INGESTION_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document: Document
    version: DocumentVersion
    created: bool


class ProjectNotFoundError(Exception):
    pass


def safe_display_name(original_name: str, suffix: str) -> str:
    basename = original_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return basename or f"upload{suffix}"


def ingest_project_document(
    session: GuardedSession,
    storage_root: Path,
    project_id: int,
    role: DocumentRole,
    original_name: str,
    content: bytes,
) -> IngestionResult:
    with _INGESTION_LOCK:
        if session.get(BidProject, project_id) is None:
            raise ProjectNotFoundError
        document = session.scalar(
            select(Document).where(
                Document.project_id == project_id,
                Document.role == role.value,
            )
        )
        return _ingest(
            session,
            storage_root,
            document=document,
            project_id=project_id,
            role=role,
            original_name=original_name,
            content=content,
        )


def ingest_company_document(
    session: GuardedSession,
    storage_root: Path,
    original_name: str,
    content: bytes,
) -> IngestionResult:
    digest = sha256_bytes(content)
    safe_storage_path(storage_root, digest, original_name)
    with _INGESTION_LOCK:
        existing = session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.role == DocumentRole.COMPANY.value)
            .where(DocumentVersion.sha256 == digest)
            .order_by(Document.id, DocumentVersion.version_number)
        ).first()
        if existing is not None:
            return IngestionResult(existing[0], existing[1], created=False)
        return _ingest(
            session,
            storage_root,
            document=None,
            project_id=None,
            role=DocumentRole.COMPANY,
            original_name=original_name,
            content=content,
        )


def _ingest(
    session: GuardedSession,
    storage_root: Path,
    *,
    document: Document | None,
    project_id: int | None,
    role: DocumentRole,
    original_name: str,
    content: bytes,
) -> IngestionResult:
    digest = sha256_bytes(content)
    target = safe_storage_path(storage_root, digest, original_name)
    if document is not None:
        existing_version = session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .where(DocumentVersion.sha256 == digest)
            .order_by(DocumentVersion.version_number)
        )
        if existing_version is not None:
            return IngestionResult(document, existing_version, created=False)

    created_file = False
    try:
        target, created_file = store_content(
            storage_root,
            digest,
            original_name,
            content,
        )
        if document is None:
            document = Document(
                project_id=project_id,
                role=role.value,
                display_name=safe_display_name(original_name, target.suffix),
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
        session.refresh(document)
        session.refresh(version)
        return IngestionResult(document, version, created=True)
    except IntegrityError:
        session.rollback()
        recovered = session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.sha256 == digest)
            .where(Document.role == role.value)
            .where(Document.project_id == project_id)
            .order_by(Document.id, DocumentVersion.version_number)
        ).first()
        if recovered is not None:
            return IngestionResult(recovered[0], recovered[1], created=False)
        _remove_unreferenced_file(session, target, created_file)
        raise
    except Exception:
        session.rollback()
        _remove_unreferenced_file(session, target, created_file)
        raise


def _remove_unreferenced_file(
    session: GuardedSession,
    target: Path,
    created_file: bool,
) -> None:
    if not created_file:
        return
    reference_count = session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.storage_path == str(target))
    )
    if reference_count == 0:
        target.unlink(missing_ok=True)


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
