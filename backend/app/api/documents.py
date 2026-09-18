from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import Path as ApiPath

from app.db import GuardedSession, get_db
from app.documents.storage import (
    ALLOWED_SUFFIXES,
    StorageCapabilityError,
    StorageIntegrityError,
    UploadTooLargeError,
    hash_upload,
)
from app.domain.enums import DocumentRole
from app.domain.schemas import (
    DocumentResponse,
    DocumentUploadResponse,
    DocumentVersionResponse,
)
from app.services.ingestion import (
    IngestionResult,
    ProjectNotFoundError,
    ingest_company_document,
    ingest_project_document,
    list_project_documents,
)
from app.settings import get_settings

router = APIRouter(tags=["documents"])


def get_storage_root() -> Path:
    return get_settings().storage_root


def _upload_response(result: IngestionResult) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        document_id=result.document.id,
        version_id=result.version.id,
        version_number=result.version.version_number,
        sha256=result.version.sha256,
        size_bytes=result.version.size_bytes,
        parse_status=result.version.parse_status,
        parse_error_code=result.version.parse_error_code,
        parse_error=result.version.parse_error,
        parse_coverage=result.version.parse_coverage,
        uploaded_at=result.version.uploaded_at,
        created=result.created,
    )


def _read_upload(file: UploadFile) -> tuple[str, str, int]:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files are supported",
        )
    try:
        digest, size = hash_upload(file.file)
    except UploadTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File exceeds 50 MiB limit",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty"
        ) from error
    return filename, digest, size


def _storage_error(
    error: StorageIntegrityError | StorageCapabilityError,
) -> HTTPException:
    if isinstance(error, StorageIntegrityError):
        return HTTPException(500, "Stored document failed integrity verification")
    return HTTPException(503, "Document storage is unavailable")


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_project_document(
    project_id: Annotated[int, ApiPath(ge=1, le=9_223_372_036_854_775_807)],
    role: Annotated[Literal["tender", "proposal"], Query()],
    file: Annotated[UploadFile, File()],
    response: Response,
    session: Annotated[GuardedSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_storage_root)],
) -> DocumentUploadResponse:
    filename, digest, size = _read_upload(file)
    try:
        result = ingest_project_document(
            session,
            storage_root,
            project_id,
            DocumentRole(role),
            filename,
            file.file,
            digest=digest,
            size=size,
        )
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from error
    except (StorageIntegrityError, StorageCapabilityError) as error:
        raise _storage_error(error) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _upload_response(result)


@router.post(
    "/company-evidence",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_company_evidence(
    file: Annotated[UploadFile, File()],
    response: Response,
    session: Annotated[GuardedSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_storage_root)],
) -> DocumentUploadResponse:
    filename, digest, size = _read_upload(file)
    try:
        result = ingest_company_document(
            session, storage_root, filename, file.file, digest=digest, size=size
        )
    except (StorageIntegrityError, StorageCapabilityError) as error:
        raise _storage_error(error) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _upload_response(result)


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentResponse],
)
def get_project_documents(
    project_id: Annotated[int, ApiPath(ge=1, le=9_223_372_036_854_775_807)],
    session: Annotated[GuardedSession, Depends(get_db)],
) -> list[DocumentResponse]:
    try:
        documents = list_project_documents(session, project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from error
    return [
        DocumentResponse(
            id=document.id,
            project_id=document.project_id,
            role=document.role,
            display_name=document.display_name,
            created_at=document.created_at,
            versions=[
                DocumentVersionResponse.model_validate(version)
                for version in sorted(
                    document.versions,
                    key=lambda item: (item.version_number, item.id),
                )
            ],
        )
        for document in documents
    ]
