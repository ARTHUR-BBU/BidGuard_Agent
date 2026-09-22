from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db import GuardedSession, get_db
from app.jobs.handlers import start_review
from app.persistence.models import (
    Assessment,
    BidProject,
    Requirement,
    ReviewJob,
    ReviewRun,
)
from app.settings import get_settings

router = APIRouter(tags=["reviews"])


class ReviewStartRequest(BaseModel):
    tender_version_id: int | None = Field(default=None, gt=0)


class ReviewJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    review_run_id: int | None
    tender_version_id: int | None
    status: str
    stage: str
    attempt_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class RequirementSummaryResponse(BaseModel):
    id: int
    text: str
    kind: str
    mandatory: bool
    source_version_id: int
    source_page: int | None
    source_section: str | None
    display_status: str | None = None
    evidence_state: str | None = None
    severity: str | None = None


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_version_id: int
    page_number: int | None
    section_path: str | None
    quote: str
    purpose: str
    valid: bool


class RequirementDetailResponse(RequirementSummaryResponse):
    reasoning: str | None = None
    recommendation: str | None = None
    needs_confirmation: bool = False
    evidence: list[EvidenceResponse] = Field(default_factory=list)


def _job_response(job: ReviewJob) -> ReviewJobResponse:
    return ReviewJobResponse.model_validate(job, from_attributes=True)


def _latest_assessment(
    session: GuardedSession,
    requirement_id: int,
) -> Assessment | None:
    return session.scalar(
        select(Assessment)
        .join(ReviewRun, ReviewRun.id == Assessment.review_run_id)
        .where(
            Assessment.requirement_id == requirement_id,
            Assessment.current.is_(True),
        )
        .order_by(ReviewRun.id.desc(), Assessment.id.desc())
        .limit(1)
    )


def _require_project(session: GuardedSession, project_id: int) -> None:
    if session.get(BidProject, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


@router.post(
    "/projects/{project_id}/reviews",
    response_model=ReviewJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_review_endpoint(
    project_id: Annotated[int, Path(ge=1)],
    session: Annotated[GuardedSession, Depends(get_db)],
    request: ReviewStartRequest | None = None,
) -> ReviewJobResponse:
    _require_project(session, project_id)
    try:
        job, _created = start_review(
            session,
            project_id=project_id,
            tender_version_id=request.tender_version_id if request else None,
            settings=get_settings(),
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return _job_response(job)


@router.get(
    "/projects/{project_id}/reviews/latest",
    response_model=ReviewJobResponse,
)
def latest_review(
    project_id: Annotated[int, Path(ge=1)],
    session: Annotated[GuardedSession, Depends(get_db)],
) -> ReviewJobResponse:
    _require_project(session, project_id)
    job = session.scalar(
        select(ReviewJob)
        .where(ReviewJob.project_id == project_id)
        .order_by(ReviewJob.created_at.desc(), ReviewJob.id.desc())
        .limit(1)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review job not found")
    return _job_response(job)


@router.get(
    "/projects/{project_id}/requirements",
    response_model=list[RequirementSummaryResponse],
)
def list_requirements(
    project_id: Annotated[int, Path(ge=1)],
    session: Annotated[GuardedSession, Depends(get_db)],
) -> list[RequirementSummaryResponse]:
    _require_project(session, project_id)
    requirements = list(
        session.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id, Requirement.active.is_(True))
            .order_by(Requirement.id)
        )
    )
    result: list[RequirementSummaryResponse] = []
    for requirement in requirements:
        assessment = _latest_assessment(session, requirement.id)
        result.append(
            RequirementSummaryResponse(
                id=requirement.id,
                text=requirement.text,
                kind=str(requirement.kind),
                mandatory=requirement.mandatory,
                source_version_id=requirement.source_version_id,
                source_page=requirement.source_page,
                source_section=requirement.source_section,
                display_status=assessment.display_status if assessment else None,
                evidence_state=assessment.evidence_state if assessment else None,
                severity=assessment.severity if assessment else None,
            )
        )
    return result


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementDetailResponse,
)
def get_requirement_detail(
    requirement_id: Annotated[int, Path(ge=1)],
    session: Annotated[GuardedSession, Depends(get_db)],
) -> RequirementDetailResponse:
    requirement = session.get(Requirement, requirement_id)
    if requirement is None or not requirement.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    assessment = _latest_assessment(session, requirement.id)
    evidence = (
        [link for link in assessment.evidence_links if link.valid]
        if assessment is not None
        else []
    )
    return RequirementDetailResponse(
        id=requirement.id,
        text=requirement.text,
        kind=str(requirement.kind),
        mandatory=requirement.mandatory,
        source_version_id=requirement.source_version_id,
        source_page=requirement.source_page,
        source_section=requirement.source_section,
        display_status=assessment.display_status if assessment else None,
        evidence_state=assessment.evidence_state if assessment else None,
        severity=assessment.severity if assessment else None,
        reasoning=assessment.reasoning if assessment else None,
        recommendation=assessment.recommendation if assessment else None,
        needs_confirmation=assessment.needs_confirmation if assessment else False,
        evidence=[EvidenceResponse.model_validate(item, from_attributes=True) for item in evidence],
    )
