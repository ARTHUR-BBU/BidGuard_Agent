from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.context import ReviewContext
from app.agents.contracts import (
    AgentRuntimeLimits,
    ModelCallLedger,
    ReasonCode,
    json_safe_ledger,
)
from app.persistence.models import (
    Assessment,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceLink,
    LLMCallRecord,
    ProjectCompanyEvidence,
    Requirement,
    ReviewRun,
)


class ModelGovernanceError(ValueError):
    code = ReasonCode.INVALID_CONTEXT


def validate_runtime_limits(limits: AgentRuntimeLimits) -> AgentRuntimeLimits:
    """Validate server-owned runtime bounds before any model request."""

    return AgentRuntimeLimits.model_validate(limits.model_dump())


def runner_run_kwargs(limits: AgentRuntimeLimits) -> dict[str, int]:
    """Return the only Runner execution knob the SDK owns directly."""

    validated = validate_runtime_limits(limits)
    return {"max_turns": validated.max_turns}


def preflight_call(
    limits: AgentRuntimeLimits,
    *,
    input_tokens: int,
    batch_items: int,
    tool_calls: int = 0,
    estimated_cost_usd: float = 0.0,
) -> CallBudget:
    """Check every per-call resource before an Agent is allowed to run."""

    validated = validate_runtime_limits(limits)
    if input_tokens < 0 or input_tokens > validated.max_input_tokens:
        raise ModelGovernanceError("model input-token budget exceeded")
    if batch_items < 1 or batch_items > validated.max_batch_items:
        raise ModelGovernanceError("model batch budget exceeded")
    return CallBudget(validated).consume(
        tool_calls=tool_calls,
        cost_usd=estimated_cost_usd,
    )


def validate_context_for_call(
    context: ReviewContext,
    *,
    project_id: int | None = None,
    document_version_ids: set[int],
    company_document_version_ids: set[int],
    chunk_ids: set[int],
) -> None:
    """Reject a call that attempts to escape its server-created context."""

    try:
        context.narrow(
            project_id=project_id,
            document_version_ids=document_version_ids,
            company_document_version_ids=company_document_version_ids,
            chunk_ids=chunk_ids,
        )
    except ValueError as error:
        raise ModelGovernanceError(str(error)) from error


def persist_model_call(
    session: Session,
    ledger: ModelCallLedger,
    *,
    context: ReviewContext,
) -> LLMCallRecord:
    """Persist one safe ledger row and verify its review-run project binding."""

    review_run = session.get(ReviewRun, ledger.review_run_id)
    if review_run is None:
        raise ModelGovernanceError("review run does not exist")
    if context.review_run_id != review_run.id or context.project_id != review_run.project_id:
        raise ModelGovernanceError("ledger context does not match review run")
    ledger_versions = list(
        session.execute(
            select(DocumentVersion.id, Document.role).join(
                Document, Document.id == DocumentVersion.document_id
            ).where(DocumentVersion.id.in_(ledger.document_version_ids))
        )
    )
    ledger_company_ids = {
        int(row[0]) for row in ledger_versions if str(row[1]) == "company"
    }
    ledger_project_ids = set(ledger.document_version_ids) - ledger_company_ids
    validate_context_for_call(
        context,
        project_id=review_run.project_id,
        document_version_ids=ledger_project_ids,
        company_document_version_ids=ledger_company_ids,
        chunk_ids=set(ledger.visible_chunk_ids),
    )
    _validate_ledger_scope(session, review_run, ledger)
    _validate_input_object_ids(session, review_run, context, ledger)
    payload = json_safe_ledger(ledger)
    record = LLMCallRecord(
        project_id=review_run.project_id,
        review_run_id=ledger.review_run_id,
        node=ledger.node,
        sequence=ledger.sequence,
        provider=ledger.provider,
        model=ledger.model,
        sdk_version=ledger.sdk_version,
        prompt_version=ledger.prompt_version,
        prompt_hash=ledger.prompt_hash,
        input_object_ids=payload["input_object_ids"],
        document_version_ids=payload["document_version_ids"],
        visible_chunk_ids=payload["visible_chunk_ids"],
        coverage=payload["coverage"],
        output_schema_version=ledger.output_schema_version,
        accepted=ledger.accepted,
        reason_code=str(ledger.reason_code),
        tool_calls=payload["tool_calls"],
        started_at=ledger.started_at,
        finished_at=ledger.finished_at,
        retry_count=ledger.retry_count,
        input_tokens=ledger.input_tokens,
        output_tokens=ledger.output_tokens,
        reported_cost_usd=ledger.reported_cost_usd,
        stop_reason=ledger.stop_reason,
    )
    session.add(record)
    session.flush()
    return record


def _validate_ledger_scope(
    session: Session,
    review_run: ReviewRun,
    ledger: ModelCallLedger,
) -> None:
    """Re-check version and chunk ownership before writing the ledger."""

    if not ledger.document_version_ids and ledger.visible_chunk_ids:
        raise ModelGovernanceError("ledger chunks require document version scope")
    if not ledger.document_version_ids:
        return
    versions = list(
        session.execute(
            select(DocumentVersion.id, Document.project_id, Document.role)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(ledger.document_version_ids))
        )
    )
    if len(versions) != len(set(ledger.document_version_ids)):
        raise ModelGovernanceError("ledger references an unknown document version")
    company_ids = {
        int(row[0]) for row in versions if str(row[2]) == "company"
    }
    project_ids = {
        int(row[0])
        for row in versions
        if str(row[2]) != "company" and row[1] == review_run.project_id
    }
    if len(company_ids) + len(project_ids) != len(versions):
        raise ModelGovernanceError("ledger document version is outside review project")
    if company_ids:
        authorized = set(
            session.scalars(
                select(ProjectCompanyEvidence.document_version_id).where(
                    ProjectCompanyEvidence.project_id == review_run.project_id,
                    ProjectCompanyEvidence.document_version_id.in_(company_ids),
                    ProjectCompanyEvidence.active.is_(True),
                )
            )
        )
        if authorized != company_ids:
            raise ModelGovernanceError("ledger references unauthorized company evidence")
    if ledger.visible_chunk_ids:
        chunks = list(
            session.execute(
                select(DocumentChunk.id, DocumentChunk.document_version_id).where(
                    DocumentChunk.id.in_(ledger.visible_chunk_ids)
                )
            )
        )
        if len(chunks) != len(set(ledger.visible_chunk_ids)) or any(
            int(row[1]) not in set(ledger.document_version_ids) for row in chunks
        ):
            raise ModelGovernanceError("ledger references a chunk outside version scope")


def _validate_input_object_ids(
    session: Session,
    review_run: ReviewRun,
    context: ReviewContext,
    ledger: ModelCallLedger,
) -> None:
    allowed_versions = set(context.allowed_document_version_ids) | set(
        context.allowed_company_document_version_ids
    )
    ledger_versions = set(ledger.document_version_ids)
    ledger_chunks = set(ledger.visible_chunk_ids)
    for object_id in ledger.input_object_ids:
        prefix, _, raw_identifier = object_id.partition(":")
        identifier = int(raw_identifier)
        if prefix == "version":
            if identifier not in allowed_versions or identifier not in ledger_versions:
                raise ModelGovernanceError("ledger input version is outside authorized scope")
        elif prefix == "chunk":
            if identifier not in ledger_chunks:
                raise ModelGovernanceError("ledger input chunk is outside visible scope")
        elif prefix == "document":
            document = session.get(Document, identifier)
            if document is None:
                raise ModelGovernanceError("ledger input document does not exist")
            version_ids = set(
                session.scalars(
                    select(DocumentVersion.id).where(
                        DocumentVersion.document_id == document.id
                    )
                )
            )
            if document.role == "company":
                if not version_ids & set(context.allowed_company_document_version_ids):
                    raise ModelGovernanceError("ledger input company document is unauthorized")
            elif (
                document.project_id != review_run.project_id
                or not version_ids & set(context.allowed_document_version_ids)
            ):
                raise ModelGovernanceError("ledger input document is outside authorized scope")
        elif prefix == "requirement":
            requirement = session.get(Requirement, identifier)
            if (
                requirement is None
                or requirement.project_id != review_run.project_id
                or requirement.source_version_id not in ledger_versions
            ):
                raise ModelGovernanceError("ledger input requirement is outside run scope")
        elif prefix == "assessment":
            assessment = session.get(Assessment, identifier)
            if assessment is None or assessment.review_run_id != review_run.id:
                raise ModelGovernanceError("ledger input assessment is outside run scope")
        elif prefix == "evidence":
            evidence = session.get(EvidenceLink, identifier)
            if (
                evidence is None
                or evidence.document_version_id not in ledger_versions
                or evidence.assessment.review_run_id != review_run.id
            ):
                raise ModelGovernanceError("ledger input evidence is outside run scope")


def next_call_sequence(session: Session, review_run_id: int) -> int:
    latest = session.scalar(
        select(LLMCallRecord.sequence)
        .where(LLMCallRecord.review_run_id == review_run_id)
        .order_by(LLMCallRecord.sequence.desc())
        .limit(1)
    )
    return int(latest or 0) + 1


@dataclass(frozen=True, slots=True)
class CallBudget:
    limits: AgentRuntimeLimits
    used_turns: int = 0
    used_tool_calls: int = 0
    used_cost_usd: float = 0.0

    def consume(self, *, turns: int = 1, tool_calls: int = 0, cost_usd: float = 0.0) -> CallBudget:
        if turns < 0 or tool_calls < 0 or cost_usd < 0:
            raise ModelGovernanceError("budget usage cannot be negative")
        if self.used_turns + turns > self.limits.max_turns:
            raise ModelGovernanceError("model turn budget exhausted")
        if self.used_tool_calls + tool_calls > self.limits.max_tool_calls:
            raise ModelGovernanceError("model tool-call budget exhausted")
        if self.used_cost_usd + cost_usd > self.limits.max_cost_usd:
            raise ModelGovernanceError("model cost budget exhausted")
        return CallBudget(
            limits=self.limits,
            used_turns=self.used_turns + turns,
            used_tool_calls=self.used_tool_calls + tool_calls,
            used_cost_usd=self.used_cost_usd + cost_usd,
        )
