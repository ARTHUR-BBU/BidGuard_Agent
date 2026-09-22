from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.engine import Row

from app.db import GuardedSession
from app.domain.enums import DisplayStatus
from app.domain.schemas import ProjectCreate
from app.persistence.models import Assessment, BidProject, Requirement


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project: BidProject
    status_counts: dict[str, int]


def _status_count_columns() -> tuple[object, ...]:
    return tuple(
        func.coalesce(
            func.sum(
                case(
                    (Assessment.display_status == status.value, 1),
                    else_=0,
                )
            ),
            0,
        ).label(status.value)
        for status in DisplayStatus
    )


def _project_summaries_statement():  # type: ignore[no-untyped-def]
    deadline_is_missing = case((BidProject.deadline_at.is_(None), 1), else_=0)
    return (
        select(BidProject, *_status_count_columns())
        .outerjoin(
            Requirement,
            (Requirement.project_id == BidProject.id) & Requirement.active.is_(True),
        )
        .outerjoin(
            Assessment,
            (Assessment.requirement_id == Requirement.id)
            & Assessment.current.is_(True),
        )
        .group_by(
            BidProject.id,
            BidProject.name,
            BidProject.deadline_at,
            BidProject.created_at,
        )
        .order_by(
            deadline_is_missing.asc(),
            BidProject.deadline_at.asc(),
            BidProject.created_at.desc(),
        )
    )


def _summary_from_row(row: Row[tuple[object, ...]]) -> ProjectSummary:
    project = row[0]
    assert isinstance(project, BidProject)
    values = row._mapping
    return ProjectSummary(
        project=project,
        status_counts={status.value: int(values[status.value]) for status in DisplayStatus},
    )


def create_project(session: GuardedSession, project_input: ProjectCreate) -> ProjectSummary:
    project = BidProject(
        name=project_input.name,
        deadline_at=project_input.deadline_at,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return ProjectSummary(
        project=project,
        status_counts={status.value: 0 for status in DisplayStatus},
    )


def list_project_summaries(session: GuardedSession) -> list[ProjectSummary]:
    rows = session.execute(_project_summaries_statement()).all()
    return [_summary_from_row(row) for row in rows]


def get_project_summary(
    session: GuardedSession, project_id: int
) -> ProjectSummary | None:
    row = session.execute(
        _project_summaries_statement().where(BidProject.id == project_id)
    ).first()
    return _summary_from_row(row) if row is not None else None
