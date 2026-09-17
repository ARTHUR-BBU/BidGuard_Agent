from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import GuardedSession
from app.domain.enums import RequirementKind
from app.persistence.models import BidProject, Document, DocumentVersion, Requirement


@dataclass(frozen=True, slots=True)
class NewRequirement:
    project_id: int
    source_version_id: int
    source_quote: str
    text: str
    kind: RequirementKind
    mandatory: bool
    source_page: int | None = None
    source_section: str | None = None


def create_requirements(
    session: GuardedSession,
    inputs: Iterable[NewRequirement],
) -> list[Requirement]:
    requirements = [
        Requirement(
            project_id=item.project_id,
            source_version_id=item.source_version_id,
            source_page=item.source_page,
            source_section=item.source_section,
            source_quote=item.source_quote,
            text=item.text,
            kind=item.kind,
            mandatory=item.mandatory,
        )
        for item in inputs
    ]
    session.add_all(requirements)
    session.flush()
    return requirements


def update_requirements_active(
    session: GuardedSession,
    requirement_ids: Iterable[int],
    *,
    active: bool,
) -> list[Requirement]:
    unique_ids = list(dict.fromkeys(requirement_ids))
    if not unique_ids:
        return []
    statement = (
        select(Requirement)
        .where(Requirement.id.in_(unique_ids))
        .order_by(Requirement.id)
    )
    requirements = list(session.scalars(statement))
    for requirement in requirements:
        requirement.active = active
    session.flush()
    return requirements


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
