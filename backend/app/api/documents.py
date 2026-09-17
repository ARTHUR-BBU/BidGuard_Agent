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
from app.documents.storage import ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES
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
        parse_status=result.version.parse_status,
        uploaded_at=result.version.uploaded_at,
        created=result.created,
    )


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files are supported",
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty"
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File exceeds 50 MiB limit",
        )
    return filename, content


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_document(
    project_id: Annotated[int, ApiPath(ge=1, le=9_223_372_036_854_775_807)],
    role: Annotated[Literal["tender", "proposal"], Query()],
    file: Annotated[UploadFile, File()],
    response: Response,
    session: Annotated[GuardedSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_storage_root)],
) -> DocumentUploadResponse:
    filename, content = await _read_upload(file)
    try:
        result = ingest_project_document(
            session,
            storage_root,
            project_id,
            DocumentRole(role),
            filename,
            content,
        )
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _upload_response(result)


@router.post(
    "/company-evidence",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_company_evidence(
    file: Annotated[UploadFile, File()],
    response: Response,
    session: Annotated[GuardedSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_storage_root)],
) -> DocumentUploadResponse:
    filename, content = await _read_upload(file)
    result = ingest_company_document(session, storage_root, filename, content)
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
