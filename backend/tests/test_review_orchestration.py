from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.agents.contracts import AgentRuntimeLimits
from app.domain.enums import EvidenceState, RequirementKind, Severity
from app.domain.schemas import AssessmentCandidate, SourceCitation
from app.persistence.models import (
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    Requirement,
    ReviewRun,
)
from app.services.reviews import run_review
from app.settings import Settings


def _version(
    document: Document,
    *,
    number: int,
    sha: str,
    text: str,
) -> DocumentVersion:
    version = DocumentVersion(
        document=document,
        version_number=number,
        sha256=sha * 64,
        storage_path=f"{document.display_name}.v{number}",
        size_bytes=10,
        parse_status="parsed",
        parse_coverage={
            "total_pages": 1,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": False,
        },
    )
    version.chunks.append(
        DocumentChunk(document_version=version, chunk_index=0, page_number=1, text=text)
    )
    return version


def _graph(db_session) -> tuple[BidProject, ReviewRun, DocumentVersion, list[Requirement]]:
    project = BidProject(name="编排测试项目")
    tender = Document(project=project, role="tender", display_name="tender.pdf")
    proposal = Document(project=project, role="proposal", display_name="proposal.docx")
    tender_version = _version(
        tender,
        number=1,
        sha="a",
        text="投标人必须提供营业执照。评分项按方案完整性评分。需确认项目负责人是否具备类似经验。",
    )
    proposal_version = _version(
        proposal,
        number=1,
        sha="b",
        text="我方提供有效营业执照，证照编号为 A-001。方案完整，包含实施计划。",
    )
    run = ReviewRun(
        project=project,
        status="queued",
        stage="assessment",
        model_provider="openai",
        model_name="test-model",
    )
    requirements = [
        Requirement(
            project_id=0,
            source_version_id=0,
            source_page=1,
            source_quote="投标人必须提供营业执照。",
            text="提供营业执照",
            kind=RequirementKind.QUALIFICATION,
            mandatory=True,
        ),
        Requirement(
            project_id=0,
            source_version_id=0,
            source_page=1,
            source_quote="评分项按方案完整性评分。",
            text="提高方案完整性评分",
            kind=RequirementKind.SCORING,
            mandatory=False,
        ),
        Requirement(
            project_id=0,
            source_version_id=0,
            source_page=1,
            source_quote="需确认项目负责人是否具备类似经验。",
            text="确认项目负责人类似经验",
            kind=RequirementKind.TECHNICAL,
            mandatory=True,
        ),
    ]
    db_session.add_all([project, tender_version, proposal_version, run])
    db_session.flush()
    for requirement in requirements:
        requirement.project_id = project.id
        requirement.source_version_id = tender_version.id
    db_session.add_all(requirements)
    db_session.commit()
    return project, run, tender_version, requirements


def _candidate(
    requirement_id: int,
    *,
    state: EvidenceState,
    severity: Severity,
    confirmation: bool = False,
    evidence: list[SourceCitation] | None = None,
) -> AssessmentCandidate:
    return AssessmentCandidate(
        requirement_id=requirement_id,
        evidence_state=state,
        severity=severity,
        needs_confirmation=confirmation,
        reasoning="根据授权材料形成的候选判断。",
        evidence=evidence or [],
        recommendation="保留证据并由业务人员确认后继续。",
    )


class _Runner:
    outcomes: ClassVar[list[Any]] = []
    calls = 0

    @classmethod
    async def run(cls, _agent, prompt: str, *, max_turns: int, run_config: Any):
        del max_turns, run_config
        outcome = cls.outcomes[cls.calls]
        cls.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        assert "requirement_id=" in prompt
        return SimpleNamespace(final_output=outcome.model_dump())


def _settings() -> Settings:
    return Settings(model_provider="openai", review_model="test-model")


def _factory(db_session):
    @contextmanager
    def make_session():
        yield db_session

    return make_session


@pytest.mark.asyncio
async def test_matched_evidence_saves_satisfied(db_session) -> None:
    _, run, _, requirements = _graph(db_session)
    _Runner.calls = 0
    _Runner.outcomes = [
        _candidate(
            requirements[0].id,
            state=EvidenceState.MATCHED,
            severity=Severity.NONE,
            evidence=[
                SourceCitation(
                    document_version_id=2,
                    page_number=1,
                    quote="我方提供有效营业执照",
                )
            ],
        )
    ]

    result = await run_review(
        _factory(db_session),
        review_run_id=run.id,
        tender_version_id=requirements[0].source_version_id,
        requirement_ids=[requirements[0].id],
        settings=_settings(),
        limits=AgentRuntimeLimits(max_turns=1, max_tool_calls=6),
        runner=_Runner,
    )

    assert result.status == "completed"
    assert result.processed_count == 1
    assert run.assessments[0].display_status == "satisfied"


@pytest.mark.asyncio
async def test_missing_mandatory_evidence_saves_high_risk(db_session) -> None:
    _, run, _, requirements = _graph(db_session)
    _Runner.calls = 0
    _Runner.outcomes = [
        _candidate(
            requirements[0].id,
            state=EvidenceState.MISSING,
            severity=Severity.CRITICAL,
        )
    ]

    await run_review(
        _factory(db_session),
        review_run_id=run.id,
        tender_version_id=requirements[0].source_version_id,
        requirement_ids=[requirements[0].id],
        settings=_settings(),
        runner=_Runner,
    )

    assert run.assessments[0].display_status == "high_risk"


@pytest.mark.asyncio
async def test_partial_scoring_evidence_saves_optimize(db_session) -> None:
    _, run, _, requirements = _graph(db_session)
    _Runner.calls = 0
    _Runner.outcomes = [
        _candidate(
            requirements[1].id,
            state=EvidenceState.PARTIAL,
            severity=Severity.OPPORTUNITY,
        )
    ]

    await run_review(
        _factory(db_session),
        review_run_id=run.id,
        tender_version_id=requirements[1].source_version_id,
        requirement_ids=[requirements[1].id],
        settings=_settings(),
        runner=_Runner,
    )

    assert run.assessments[0].display_status == "optimize"


@pytest.mark.asyncio
async def test_ambiguous_requirement_requests_confirmation(db_session) -> None:
    _, run, _, requirements = _graph(db_session)
    _Runner.calls = 0
    _Runner.outcomes = [
        _candidate(
            requirements[2].id,
            state=EvidenceState.UNCERTAIN,
            severity=Severity.WARNING,
            confirmation=True,
        )
    ]

    await run_review(
        _factory(db_session),
        review_run_id=run.id,
        tender_version_id=requirements[2].source_version_id,
        requirement_ids=[requirements[2].id],
        settings=_settings(),
        runner=_Runner,
    )

    assert run.assessments[0].display_status == "needs_confirmation"
    assert any(event.event_type == "user_confirmation_requested" for event in run.project.audit_events)


@pytest.mark.asyncio
async def test_limit_failure_commits_completed_assessments_and_cursor(db_session) -> None:
    _, run, _, requirements = _graph(db_session)
    _Runner.calls = 0
    _Runner.outcomes = [
        _candidate(
            requirements[0].id,
            state=EvidenceState.MISSING,
            severity=Severity.CRITICAL,
        ),
        TimeoutError("bounded review timed out"),
    ]

    result = await run_review(
        _factory(db_session),
        review_run_id=run.id,
        tender_version_id=requirements[0].source_version_id,
        requirement_ids=[requirements[0].id, requirements[1].id],
        settings=_settings(),
        limits=AgentRuntimeLimits(max_turns=1, max_tool_calls=6),
        runner=_Runner,
    )

    db_session.expire_all()
    refreshed = db_session.get(ReviewRun, run.id)
    assert refreshed is not None
    assert result.status == "partial_failure"
    assert refreshed.status == "partial_failure"
    assert refreshed.resumable_requirement_id == requirements[1].id
    assert len(refreshed.assessments) == 1
