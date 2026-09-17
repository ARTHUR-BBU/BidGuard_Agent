from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import BidProject, Document, DocumentVersion, Requirement


def get_project(session: Session, project_id: int) -> BidProject | None:
    return session.get(BidProject, project_id)


def get_document_version(
    session: Session, document_version_id: int
) -> DocumentVersion | None:
    return session.get(DocumentVersion, document_version_id)


def get_requirement(session: Session, requirement_id: int) -> Requirement | None:
    return session.get(Requirement, requirement_id)


def list_project_documents(session: Session, project_id: int) -> list[Document]:
    statement = (
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.id)
    )
    return list(session.scalars(statement))


def list_project_requirements(session: Session, project_id: int) -> list[Requirement]:
    statement = (
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.id)
    )
    return list(session.scalars(statement))
