from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from app.db import GuardedSession, get_db
from app.persistence.models import BidProject
from app.services.decisions import (
    ActionItemScopeError,
    DecisionScopeError,
    DecisionValidationError,
    complete_action_item,
    record_decision,
    start_incremental_review,
)
from app.settings import get_settings

router = APIRouter(tags=["decisions"])


class DecisionRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=80)
    explanation: str = Field(min_length=1, max_length=4000)
    actor: str = Field(min_length=1, max_length=200)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    decision: str
    explanation: str
    actor: str | None
    review_run_id: int | None
    assessment_id: int | None
    version_ids: list[int] | None
    created_at: datetime


class ActionItemCompleteRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=200)


class ActionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    description: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    completed_by: str | None


class ReReviewRequest(BaseModel):
    changed_version_ids: list[int] = Field(default_factory=list, max_length=50)
    actor: str = Field(min_length=1, max_length=200)
    tender_version_id: int | None = Field(default=None, gt=0)


class ReReviewResponse(BaseModel):
    job_id: int
    review_run_id: int | None
    status: str
    stage: str
    affected_requirement_ids: list[int]
    created: bool


@router.post(
    "/requirements/{requirement_id}/decisions",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_decision(
    requirement_id: Annotated[int, Path(ge=1)],
    request: DecisionRequest,
    session: Annotated[GuardedSession, Depends(get_db)],
) -> DecisionResponse:
    try:
        decision = record_decision(
            session,
            requirement_id=requirement_id,
            decision=request.decision,
            explanation=request.explanation,
            actor=request.actor,
        )
        session.commit()
    except DecisionScopeError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DecisionValidationError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return DecisionResponse.model_validate(decision, from_attributes=True)


@router.post(
    "/action-items/{action_item_id}/complete",
    response_model=ActionItemResponse,
)
def complete_action(
    action_item_id: Annotated[int, Path(ge=1)],
    request: ActionItemCompleteRequest,
    session: Annotated[GuardedSession, Depends(get_db)],
) -> ActionItemResponse:
    try:
        action = complete_action_item(
            session,
            action_item_id=action_item_id,
            actor=request.actor,
        )
        session.commit()
    except ActionItemScopeError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except DecisionValidationError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return ActionItemResponse.model_validate(action, from_attributes=True)


@router.post(
    "/projects/{project_id}/re-review",
    response_model=ReReviewResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_re_review(
    project_id: Annotated[int, Path(ge=1)],
    request: ReReviewRequest,
    session: Annotated[GuardedSession, Depends(get_db)],
) -> ReReviewResponse:
    if session.get(BidProject, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        result = start_incremental_review(
            session,
            project_id=project_id,
            changed_version_ids=request.changed_version_ids,
            actor=request.actor,
            tender_version_id=request.tender_version_id,
            settings=get_settings(),
        )
    except DecisionScopeError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except DecisionValidationError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return ReReviewResponse(
        job_id=result.job.id,
        review_run_id=result.job.review_run_id,
        status=result.job.status,
        stage=result.job.stage,
        affected_requirement_ids=list(result.affected_requirement_ids),
        created=result.created,
    )
