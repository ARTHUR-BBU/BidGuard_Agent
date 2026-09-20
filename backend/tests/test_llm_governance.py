from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from app.agents.context import (
    ContextConflictError,
    ContextScopeError,
    build_review_context,
)
from app.agents.contracts import (
    AgentRuntimeLimits,
    Claim,
    Coverage,
    CoverageState,
    EvidenceFact,
    ModelCallLedger,
    ReasonCode,
    StopReason,
    prompt_hash,
)
from app.agents.provider import build_run_config
from app.persistence.models import (
    BidProject,
    Document,
    DocumentVersion,
    ProjectCompanyEvidence,
    ReviewRun,
    TenderPackageMember,
)
from app.services.model_calls import (
    CallBudget,
    ModelGovernanceError,
    persist_model_call,
    preflight_call,
    runner_run_kwargs,
    validate_context_for_call,
)
from app.settings import Settings


def _review_graph(db_session):
    project = BidProject(name="治理测试项目")
    tender = Document(project=project, role="tender", display_name="tender.pdf")
    tender_version = DocumentVersion(
        document=tender,
        version_number=1,
        sha256="a" * 64,
        storage_path="tender.pdf",
        size_bytes=10,
    )
    company = Document(
        role="company",
        display_name="company.pdf",
        company_content_sha256="b" * 64,
    )
    company_version = DocumentVersion(
        document=company,
        version_number=1,
        sha256="b" * 64,
        storage_path="company.pdf",
        size_bytes=10,
    )
    run = ReviewRun(
        project=project,
        status="queued",
        stage="governance",
        model_provider="openai",
        model_name="gpt-test",
    )
    db_session.add_all([project, tender_version, company_version, run])
    db_session.flush()
    return project, tender_version, company_version, run


def _ledger(run_id: int, *, version_id: int = 10, sequence: int = 1) -> ModelCallLedger:
    started = datetime.now(UTC)
    coverage = Coverage(document_version_ids=[version_id], visible_page_numbers=[1])
    return ModelCallLedger(
        review_run_id=run_id,
        node="bounded_test",
        sequence=sequence,
        provider="openai",
        model="gpt-test",
        sdk_version="sdk-test",
        prompt_version="test.v1",
        prompt_hash=prompt_hash("test.v1", "fixed prompt"),
        input_object_ids=[f"version:{version_id}"],
        document_version_ids=[version_id],
        visible_chunk_ids=[],
        coverage=coverage,
        output_schema_version="test.v1",
        accepted=True,
        reason_code=ReasonCode.ACCEPTED,
        started_at=started,
        finished_at=started + timedelta(milliseconds=10),
        stop_reason=StopReason.COMPLETED,
    )


def test_coverage_is_explicit_and_fail_closed() -> None:
    assert Coverage(document_version_ids=[1], visible_page_numbers=[1]).state is CoverageState.COMPLETE
    with pytest.raises(ValueError, match="unexamined"):
        Coverage(
            document_version_ids=[1],
            visible_page_numbers=[1],
            unexamined_ranges=["page 2"],
        )
    with pytest.raises(ValueError, match="partial coverage"):
        Coverage(
            document_version_ids=[1],
            state=CoverageState.PARTIAL_FAILURE,
        )
    with pytest.raises(ValueError, match="document version"):
        Coverage()


def test_prompt_hash_and_ledger_never_accept_full_prompt_text() -> None:
    assert len(prompt_hash("v1", "fixed prompt")) == 64
    with pytest.raises(ValueError):
        prompt_hash("v1", "")
    fields = set(ModelCallLedger.model_fields)
    assert "prompt_text" not in fields
    assert "document_text" not in fields
    with pytest.raises(ValueError, match="typed positive"):
        ModelCallLedger(
            **_ledger(1).model_dump(exclude={"input_object_ids"}),
            input_object_ids=["full tender text masquerading as an id"],
        )
    fact = EvidenceFact(document_version_id=1, chunk_id=2, quote="原文")
    claim = Claim(claim_type="supports", requirement_id=3)
    assert fact.verified is False
    assert claim.verified is False


def test_runtime_budget_never_increases() -> None:
    budget = CallBudget(AgentRuntimeLimits(max_turns=1, max_tool_calls=1, max_cost_usd=0.1))
    with pytest.raises(ModelGovernanceError, match="turn budget"):
        budget.consume(turns=2)
    with pytest.raises(ModelGovernanceError, match="tool-call"):
        budget.consume(tool_calls=2)
    with pytest.raises(ModelGovernanceError, match="cost"):
        budget.consume(cost_usd=0.2)
    with pytest.raises(ModelGovernanceError, match="input-token"):
        preflight_call(
            AgentRuntimeLimits(max_input_tokens=10),
            input_tokens=11,
            batch_items=1,
        )
    with pytest.raises(ModelGovernanceError, match="batch"):
        preflight_call(
            AgentRuntimeLimits(max_batch_items=1),
            input_tokens=1,
            batch_items=2,
        )
    with pytest.raises(ModelGovernanceError, match="tool-call"):
        preflight_call(
            AgentRuntimeLimits(max_tool_calls=0),
            input_tokens=1,
            batch_items=1,
            tool_calls=1,
        )
    limits = AgentRuntimeLimits(
        max_turns=3,
        max_retries=1,
        timeout_seconds=7,
        max_output_tokens=123,
    )
    config = build_run_config(
        Settings(review_model="governed-test"),
        limits=limits,
    )
    assert config.model_settings is not None
    assert config.model_settings.timeout == 7
    assert config.model_settings.max_tokens == 123
    assert config.model_settings.retry is not None
    assert config.model_settings.retry.max_retries == 1
    assert runner_run_kwargs(limits) == {"max_turns": 3}


def test_context_narrowing_rejects_model_scope_expansion() -> None:
    from app.agents.context import ReviewContext

    context = ReviewContext(
        project_id=1,
        review_run_id=2,
        tender_version_id=10,
        allowed_document_version_ids=(10, 11),
        allowed_company_document_version_ids=(30,),
        allowed_chunk_ids=(20,),
        coverage=Coverage(document_version_ids=[10], visible_chunk_ids=[20]),
    )
    with pytest.raises(ContextScopeError):
        context.narrow(document_version_ids={10, 99})
    with pytest.raises(ContextScopeError):
        context.narrow(project_id=999)
    with pytest.raises(ModelGovernanceError):
        validate_context_for_call(
            context,
            document_version_ids={10},
            company_document_version_ids={31},
            chunk_ids={20},
        )


def test_context_builder_enforces_company_authorization_and_conflict(db_session) -> None:
    project, tender_version, company_version, run = _review_graph(db_session)
    db_session.add(
        ProjectCompanyEvidence(
            project_id=project.id,
            document_version_id=company_version.id,
            selected_by="user-1",
        )
    )
    db_session.add(
        TenderPackageMember(
            project_id=project.id,
            document_version_id=tender_version.id,
            member_kind="main",
            included=True,
        )
    )
    db_session.commit()
    context = build_review_context(
        db_session,
        review_run_id=run.id,
        tender_version_id=tender_version.id,
        limits=AgentRuntimeLimits(),
    )
    assert context.project_id == project.id
    assert context.allowed_company_document_version_ids == (company_version.id,)

    package = db_session.query(TenderPackageMember).one()
    package.conflict_state = "unresolved"
    db_session.commit()
    with pytest.raises(ContextConflictError):
        build_review_context(
            db_session,
            review_run_id=run.id,
            tender_version_id=tender_version.id,
            limits=AgentRuntimeLimits(),
        )


def test_context_builder_rejects_cross_project_and_excluded_unresolved_member(
    db_session,
) -> None:
    project, tender_version, _, run = _review_graph(db_session)
    other_project = BidProject(name="另一个项目")
    other_tender = Document(
        project=other_project,
        role="tender",
        display_name="other.pdf",
    )
    other_version = DocumentVersion(
        document=other_tender,
        version_number=1,
        sha256="c" * 64,
        storage_path="other.pdf",
        size_bytes=10,
    )
    db_session.add_all([other_project, other_version])
    db_session.flush()
    db_session.add(
        TenderPackageMember(
            project_id=project.id,
            document_version_id=other_version.id,
            member_kind="attachment",
            included=True,
        )
    )
    db_session.commit()
    with pytest.raises(ContextScopeError):
        build_review_context(
            db_session,
            review_run_id=run.id,
            tender_version_id=tender_version.id,
            limits=AgentRuntimeLimits(),
        )

    db_session.rollback()
    db_session.query(TenderPackageMember).delete()
    db_session.add(
        TenderPackageMember(
            project_id=project.id,
            document_version_id=tender_version.id,
            member_kind="addendum",
            included=False,
            conflict_state="unresolved",
        )
    )
    db_session.commit()
    with pytest.raises(ContextConflictError):
        build_review_context(
            db_session,
            review_run_id=run.id,
            tender_version_id=tender_version.id,
            limits=AgentRuntimeLimits(),
        )


def test_referenced_document_version_cannot_be_deleted_silently(db_session) -> None:
    project, tender_version, _, _ = _review_graph(db_session)
    db_session.add(
        TenderPackageMember(
            project_id=project.id,
            document_version_id=tender_version.id,
            member_kind="main",
            included=True,
        )
    )
    db_session.commit()
    db_session.delete(tender_version)
    with pytest.raises((IntegrityError, StatementError, ValueError)):
        db_session.commit()
    db_session.rollback()

def test_model_call_ledger_persists_safe_record(db_session) -> None:
    _, tender_version, _, run = _review_graph(db_session)
    context = build_review_context(
        db_session,
        review_run_id=run.id,
        tender_version_id=tender_version.id,
        limits=AgentRuntimeLimits(),
    )
    record = persist_model_call(
        db_session,
        _ledger(run.id, version_id=tender_version.id),
        context=context,
    )
    db_session.commit()
    assert record.project_id == run.project_id
    assert record.reason_code == ReasonCode.ACCEPTED
    assert not hasattr(record, "prompt_text")
    assert not hasattr(record, "document_text")


def test_model_call_ledger_rejects_orphaned_object_ids(db_session) -> None:
    _, tender_version, _, run = _review_graph(db_session)
    context = build_review_context(
        db_session,
        review_run_id=run.id,
        tender_version_id=tender_version.id,
        limits=AgentRuntimeLimits(),
    )
    ledger = _ledger(run.id, version_id=tender_version.id).model_copy(
        update={"input_object_ids": ("version:999999",)}
    )
    with pytest.raises(ModelGovernanceError, match="input version"):
        persist_model_call(db_session, ledger, context=context)


def test_model_call_ledger_rejects_same_project_unselected_document(db_session) -> None:
    project, tender_version, _, run = _review_graph(db_session)
    proposal = Document(project=project, role="proposal", display_name="draft.pdf")
    proposal_version = DocumentVersion(
        document=proposal,
        version_number=1,
        sha256="d" * 64,
        storage_path="draft.pdf",
        size_bytes=10,
    )
    db_session.add(proposal_version)
    db_session.flush()
    context = build_review_context(
        db_session,
        review_run_id=run.id,
        tender_version_id=tender_version.id,
        limits=AgentRuntimeLimits(),
    )
    ledger = _ledger(run.id, version_id=tender_version.id).model_copy(
        update={"input_object_ids": (f"document:{proposal.id}",)}
    )
    with pytest.raises(ModelGovernanceError, match="authorized scope"):
        persist_model_call(db_session, ledger, context=context)


def test_governance_tables_are_created() -> None:
    from app.db import Base

    assert {
        "llm_call_records",
        "project_company_evidence",
        "tender_package_members",
    }.issubset(Base.metadata.tables)
