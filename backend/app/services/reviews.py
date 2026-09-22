from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from agents import Runner
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.context import ReviewContext, build_review_context
from app.agents.contracts import (
    AgentRuntimeLimits,
    Coverage,
    ModelCallLedger,
    ReasonCode,
    StopReason,
    prompt_hash,
)
from app.agents.gates import UnsupportedPassError
from app.agents.provider import (
    ModelConfigurationError,
    build_run_config,
    resolve_model_name,
)
from app.agents.review import (
    REVIEW_INSTRUCTIONS,
    build_review_agent,
    build_review_prompt,
)
from app.agents.tools import ReviewToolbox, build_review_tools
from app.db import GuardedSession
from app.domain.enums import EvidenceState, ReviewRunStatus
from app.domain.schemas import AssessmentCandidate
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    Document,
    DocumentVersion,
    Requirement,
    ReviewRun,
)
from app.services.model_calls import (
    ModelGovernanceError,
    next_call_sequence,
    persist_model_call,
    preflight_call,
    runner_run_kwargs,
)
from app.settings import Settings

MAX_REVIEW_BATCH_SIZE = 10
REVIEW_PROMPT_VERSION = "task11.bounded-review.v1"


@dataclass(frozen=True, slots=True)
class ReviewResult:
    status: str
    processed_count: int
    total_count: int
    resumable_requirement_id: int | None = None


@contextmanager
def _session_scope(
    session_factory: Callable[
        [], GuardedSession | AbstractContextManager[GuardedSession]
    ],
) -> Iterator[GuardedSession]:
    """Accept either a SessionLocal-style context manager or a test session."""

    candidate = session_factory()
    if hasattr(candidate, "__enter__") and hasattr(candidate, "__exit__"):
        context = cast(AbstractContextManager[GuardedSession], candidate)
        with context as session:
            yield session
        return
    yield cast(GuardedSession, candidate)


def _estimate_input_tokens(prompt: str) -> int:
    return max(1, (len(prompt) + 3) // 4)


def _review_context_with_proposals(
    session: GuardedSession,
    context: ReviewContext,
) -> ReviewContext:
    """Add project proposal versions to the server-created review scope."""

    proposal_ids = set(
        session.scalars(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.project_id == context.project_id,
                Document.role == "proposal",
                DocumentVersion.parse_status.in_(("parsed", "partial_failure")),
            )
            .order_by(DocumentVersion.id)
        )
    )
    allowed_versions = set(context.allowed_document_version_ids) | proposal_ids
    coverage = context.coverage.model_copy(
        update={"document_version_ids": tuple(sorted(allowed_versions))}
    )
    return context.model_copy(
        update={
            "allowed_document_version_ids": tuple(sorted(allowed_versions)),
            "coverage": coverage,
        }
    )


def _failure_details(error: BaseException) -> tuple[ReasonCode, StopReason]:
    error_name = type(error).__name__.casefold()
    message = str(error).casefold()
    if isinstance(error, TimeoutError) or "timeout" in error_name or "timeout" in message:
        return ReasonCode.TIMEOUT, StopReason.TIMEOUT
    if "tool" in error_name or "turn" in error_name or "maxturn" in error_name:
        return ReasonCode.TOOL_LIMIT, StopReason.TOOL_LIMIT
    if "tool" in message and ("limit" in message or "budget" in message):
        return ReasonCode.TOOL_LIMIT, StopReason.TOOL_LIMIT
    if isinstance(error, (ValidationError, UnsupportedPassError)) or (
        isinstance(error, ValueError) and "candidate requirement" in message
    ):
        return ReasonCode.REJECTED_SCHEMA, StopReason.FAILURE
    code = getattr(error, "code", None)
    if code == "tool_scope_invalid":
        return ReasonCode.REJECTED_SCOPE, StopReason.FAILURE
    if isinstance(error, ModelConfigurationError):
        if error.code == "MODEL_NOT_CONFIGURED":
            return ReasonCode.MODEL_NOT_CONFIGURED, StopReason.FAILURE
        return ReasonCode.PROVIDER_UNAVAILABLE, StopReason.FAILURE
    if isinstance(error, ModelGovernanceError):
        if "budget" in message:
            return ReasonCode.REJECTED_BUDGET, StopReason.BUDGET_EXHAUSTED
        return ReasonCode.INVALID_CONTEXT, StopReason.FAILURE
    return ReasonCode.PROVIDER_UNAVAILABLE, StopReason.FAILURE


def _ledger_coverage(
    context: ReviewContext,
    document_version_ids: set[int],
) -> Coverage:
    return context.coverage.model_copy(
        update={
            "document_version_ids": tuple(sorted(document_version_ids)),
            "visible_chunk_ids": (),
        }
    )


def _persist_review_ledger(
    session: GuardedSession,
    *,
    context: ReviewContext,
    settings: Settings,
    model: str,
    requirement: Requirement,
    document_version_ids: set[int],
    accepted: bool,
    reason_code: ReasonCode,
    stop_reason: StopReason,
    started_at: datetime,
    finished_at: datetime,
    assessment: Assessment | None = None,
) -> None:
    object_ids = [f"requirement:{requirement.id}"]
    if assessment is not None:
        object_ids.append(f"assessment:{assessment.id}")
        object_ids.extend(
            f"evidence:{link.id}" for link in assessment.evidence_links if link.valid
        )
    coverage = _ledger_coverage(context, document_version_ids)
    persist_model_call(
        session,
        ModelCallLedger(
            review_run_id=context.review_run_id,
            node="bounded_review",
            sequence=next_call_sequence(session, context.review_run_id),
            provider=settings.model_provider,
            model=model,
            sdk_version="openai-agents",
            prompt_version=REVIEW_PROMPT_VERSION,
            prompt_hash=prompt_hash(REVIEW_PROMPT_VERSION, REVIEW_INSTRUCTIONS),
            input_object_ids=tuple(object_ids),
            document_version_ids=tuple(sorted(document_version_ids)),
            visible_chunk_ids=(),
            coverage=coverage,
            output_schema_version="AssessmentCandidate.v1",
            accepted=accepted,
            reason_code=reason_code,
            started_at=started_at,
            finished_at=finished_at,
            stop_reason=stop_reason,
        ),
        context=context,
    )


def _progress_event(
    session: GuardedSession,
    *,
    run: ReviewRun,
    processed_count: int,
    total_count: int,
    resumable_requirement_id: int | None,
) -> None:
    session.add(
        AuditEvent(
            project_id=run.project_id,
            event_type="review_progress_updated",
            payload={
                "review_run_id": run.id,
                "processed_count": processed_count,
                "total_count": total_count,
                "resumable_requirement_id": resumable_requirement_id,
            },
        )
    )


def _ensure_confirmation_or_action(
    toolbox: ReviewToolbox,
    session: GuardedSession,
    *,
    requirement: Requirement,
    assessment: Assessment,
    candidate_needs_confirmation: bool,
) -> None:
    if candidate_needs_confirmation or assessment.evidence_state == EvidenceState.UNCERTAIN:
        toolbox.request_user_confirmation(
            requirement.id,
            f"请确认“{requirement.text}”所依赖的企业事实或条款解释。",
            assessment.reasoning,
        )
        return
    if assessment.display_status not in {"satisfied", "needs_confirmation"}:
        existing = session.scalar(
            select(ActionItem.id).where(
                ActionItem.requirement_id == requirement.id,
                ActionItem.status == "open",
            )
        )
        if existing is None and assessment.recommendation.strip():
            toolbox.create_action_item(
                requirement.id,
                "补充或核验要求证据",
                assessment.recommendation,
            )


async def _review_one(
    session: GuardedSession,
    *,
    context: ReviewContext,
    toolbox: ReviewToolbox,
    requirement: Requirement,
    settings: Settings,
    limits: AgentRuntimeLimits,
    runner: Any,
) -> Assessment:
    started_at = datetime.now(UTC)
    model_for_ledger = (
        settings.review_model.strip()
        or settings.easyrouter_review_model.strip()
        or "unconfigured"
    )
    source_versions = {requirement.source_version_id}
    prompt = build_review_prompt(
        requirement_id=requirement.id,
        text=requirement.text,
        mandatory=requirement.mandatory,
        source_version_id=requirement.source_version_id,
        source_page=requirement.source_page,
        source_section=requirement.source_section,
        source_quote=requirement.source_quote,
    )
    assessment: Assessment | None = None
    try:
        preflight_call(
            limits,
            input_tokens=_estimate_input_tokens(prompt),
            batch_items=1,
        )
        model = resolve_model_name("review", settings)
        model_for_ledger = model
        agent = build_review_agent(model=model, tools=build_review_tools(session, context))
        run_config = build_run_config(settings, purpose="review", limits=limits)
        result = await runner.run(
            agent,
            prompt,
            **runner_run_kwargs(limits),
            run_config=run_config,
        )
        output = result.final_output
        candidate = (
            output
            if isinstance(output, AssessmentCandidate)
            else AssessmentCandidate.model_validate(output)
        )
        if candidate.requirement_id != requirement.id:
            raise ValueError("candidate requirement does not match")
        for citation in candidate.evidence:
            source_versions.add(citation.document_version_id)
        assessment = toolbox.save_assessment(requirement.id, candidate)
        _ensure_confirmation_or_action(
            toolbox,
            session,
            requirement=requirement,
            assessment=assessment,
            candidate_needs_confirmation=candidate.needs_confirmation,
        )
        session.flush()
        _persist_review_ledger(
            session,
            context=context,
            settings=settings,
            model=model,
            requirement=requirement,
            document_version_ids=source_versions,
            accepted=True,
            reason_code=ReasonCode.ACCEPTED,
            stop_reason=StopReason.COMPLETED,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            assessment=assessment,
        )
        return assessment
    except Exception as error:
        reason_code, stop_reason = _failure_details(error)
        _persist_review_ledger(
            session,
            context=context,
            settings=settings,
            model=model_for_ledger,
            requirement=requirement,
            document_version_ids=source_versions,
            accepted=False,
            reason_code=reason_code,
            stop_reason=stop_reason,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            assessment=assessment,
        )
        raise


async def run_review(
    session_factory: Callable[
        [], GuardedSession | AbstractContextManager[GuardedSession]
    ],
    *,
    review_run_id: int,
    tender_version_id: int,
    settings: Settings,
    requirement_ids: Iterable[int] | None = None,
    limits: AgentRuntimeLimits | None = None,
    runner: Any = Runner,
) -> ReviewResult:
    """Run a bounded, resumable review over one requirement list.

    The service owns the project and version scope, commits at batch boundaries,
    and commits the current transaction on failure so earlier assessments remain.
    """

    runtime_limits = limits or AgentRuntimeLimits(
        max_turns=settings.max_agent_turns,
        max_tool_calls=settings.max_tool_calls,
    )
    requested_ids = None if requirement_ids is None else tuple(sorted(set(requirement_ids)))

    with _session_scope(session_factory) as session:
        run = session.get(ReviewRun, review_run_id)
        if run is None:
            raise ModelGovernanceError("review run does not exist")
        context = _review_context_with_proposals(
            session,
            build_review_context(
                session,
                review_run_id=review_run_id,
                tender_version_id=tender_version_id,
                limits=runtime_limits,
            ),
        )
        statement = select(Requirement).where(
            Requirement.project_id == context.project_id,
            Requirement.active.is_(True),
        )
        if requested_ids is not None:
            statement = statement.where(Requirement.id.in_(requested_ids))
            requirements = list(session.scalars(statement.order_by(Requirement.id)))
        if requested_ids is not None and {item.id for item in requirements} != set(requested_ids):
            raise ModelGovernanceError("review requirements are outside project scope")
        if run.resumable_requirement_id is not None:
            requirements = [
                item for item in requirements if item.id >= run.resumable_requirement_id
            ]
        total_count = len(requirements)
        processed_count = 0
        awaiting_confirmation = False
        run.status = ReviewRunStatus.REVIEWING
        run.stage = "assessment"
        run.resumable_requirement_id = requirements[0].id if requirements else None
        session.commit()

        if not requirements:
            run.status = ReviewRunStatus.COMPLETED
            run.resumable_requirement_id = None
            run.completed_at = datetime.now(UTC)
            _progress_event(
                session,
                run=run,
                processed_count=0,
                total_count=0,
                resumable_requirement_id=None,
            )
            session.commit()
            return ReviewResult("completed", 0, 0)

        batch_size = min(MAX_REVIEW_BATCH_SIZE, runtime_limits.max_batch_items)
        toolbox = ReviewToolbox(session, context)
        for offset in range(0, total_count, batch_size):
            batch = requirements[offset : offset + batch_size]
            for requirement in batch:
                run.resumable_requirement_id = requirement.id
                try:
                    assessment = await _review_one(
                        session,
                        context=context,
                        toolbox=toolbox,
                        requirement=requirement,
                        settings=settings,
                        limits=runtime_limits,
                        runner=runner,
                    )
                except Exception as error:  # noqa: BLE001 - failure is persisted and run is resumable
                    run.status = ReviewRunStatus.PARTIAL_FAILURE
                    run.stage = "assessment"
                    run.resumable_requirement_id = requirement.id
                    session.add(
                        AuditEvent(
                            project_id=run.project_id,
                            event_type="review_requirement_failed",
                            payload={
                                "review_run_id": run.id,
                                "requirement_id": requirement.id,
                                "error_type": type(error).__name__,
                            },
                        )
                    )
                    _progress_event(
                        session,
                        run=run,
                        processed_count=processed_count,
                        total_count=total_count,
                        resumable_requirement_id=requirement.id,
                    )
                    session.commit()
                    return ReviewResult(
                        "partial_failure",
                        processed_count,
                        total_count,
                        requirement.id,
                    )
                processed_count += 1
                awaiting_confirmation = (
                    awaiting_confirmation
                    or assessment.display_status == "needs_confirmation"
                )
                run.resumable_requirement_id = (
                    requirements[offset + batch.index(requirement) + 1].id
                    if offset + batch.index(requirement) + 1 < total_count
                    else None
                )
                _progress_event(
                    session,
                    run=run,
                    processed_count=processed_count,
                    total_count=total_count,
                    resumable_requirement_id=run.resumable_requirement_id,
                )
            session.commit()

        run.status = (
            ReviewRunStatus.AWAITING_CONFIRMATION
            if awaiting_confirmation
            else ReviewRunStatus.COMPLETED
        )
        run.resumable_requirement_id = None
        run.completed_at = None if awaiting_confirmation else datetime.now(UTC)
        _progress_event(
            session,
            run=run,
            processed_count=processed_count,
            total_count=total_count,
            resumable_requirement_id=None,
        )
        session.commit()
        return ReviewResult(
            str(run.status),
            processed_count,
            total_count,
            None,
        )
