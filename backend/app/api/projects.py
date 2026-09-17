from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.db import GuardedSession, get_db
from app.domain.schemas import ProjectCreate, ProjectResponse, StatusCounts
from app.services.projects import (
    ProjectSummary,
    create_project,
    get_project_summary,
    list_project_summaries,
)

router = APIRouter(tags=["projects"])


def _response(summary: ProjectSummary) -> ProjectResponse:
    return ProjectResponse(
        id=summary.project.id,
        name=summary.project.name,
        deadline_at=summary.project.deadline_at,
        created_at=summary.project.created_at,
        status_counts=StatusCounts(**summary.status_counts),
    )


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project_endpoint(
    project_input: ProjectCreate,
    session: Annotated[GuardedSession, Depends(get_db)],
) -> ProjectResponse:
    return _response(create_project(session, project_input))


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    session: Annotated[GuardedSession, Depends(get_db)],
) -> list[ProjectResponse]:
    return [_response(summary) for summary in list_project_summaries(session)]


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)],
    session: Annotated[GuardedSession, Depends(get_db)],
) -> ProjectResponse:
    summary = get_project_summary(session, project_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _response(summary)
