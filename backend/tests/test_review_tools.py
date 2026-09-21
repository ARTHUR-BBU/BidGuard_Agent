from __future__ import annotations

import pytest

from app.agents.context import ReviewContext
from app.agents.contracts import Coverage
from app.agents.gates import StaleEvidenceError, UnsupportedPassError
from app.agents.tools import ReviewToolbox, build_review_tools
from app.domain.enums import EvidenceState, RequirementKind, Severity
from app.domain.schemas import AssessmentCandidate, SourceCitation
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    ProjectCompanyEvidence,
    Requirement,
    ReviewRun,
)


def _setup(db_session):
    project = BidProject(name="工具测试项目")
    tender_document = Document(
        project=project, role="tender", display_name="tender.pdf"
    )
    tender_version = DocumentVersion(
        document=tender_document,
        version_number=1,
        sha256="a" * 64,
        storage_path="tender.pdf",
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
    tender_chunk = DocumentChunk(
        document_version=tender_version,
        chunk_index=0,
        page_number=1,
        text="投标人必须提供营业执照。",
    )
    proposal_document = Document(
        project=project, role="proposal", display_name="proposal.docx"
    )
    proposal_version = DocumentVersion(
        document=proposal_document,
        version_number=1,
        sha256="b" * 64,
        storage_path="proposal.docx",
        size_bytes=10,
        parse_status="parsed",
        parse_coverage={
            "total_sections": 1,
            "parsed_sections": [1],
            "coverage_issues": [],
            "needs_ocr": False,
        },
    )
    proposal_chunk = DocumentChunk(
        document_version=proposal_version,
        chunk_index=0,
        page_number=1,
        text="我方提供有效营业执照，证照编号为 A-001。",
    )
    company_document = Document(
        role="company",
        display_name="company.pdf",
        company_content_sha256="c" * 64,
    )
    company_version = DocumentVersion(
        document=company_document,
        version_number=1,
        sha256="c" * 64,
        storage_path="company.pdf",
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
    company_chunk = DocumentChunk(
        document_version=company_version,
        chunk_index=0,
        page_number=1,
        text="公司营业执照统一社会信用代码为 C-001。",
    )
    authorization = ProjectCompanyEvidence(
        project=project,
        document_version=company_version,
        selected_by="tester",
        reason="工具测试",
    )
    run = ReviewRun(
        project=project,
        status="reviewing",
        stage="assessment",
        model_provider="openai",
        model_name="test-model",
    )
    db_session.add_all(
        [
            project,
            tender_version,
            tender_chunk,
            proposal_version,
            proposal_chunk,
            company_version,
            company_chunk,
            authorization,
            run,
        ]
    )
    db_session.flush()
    requirement = Requirement(
        project_id=project.id,
        source_version_id=tender_version.id,
        source_page=1,
        source_quote="投标人必须提供营业执照。",
        text="提供营业执照",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
    )
    db_session.add(requirement)
    db_session.flush()
    context = ReviewContext(
        project_id=project.id,
        review_run_id=run.id,
        tender_version_id=tender_version.id,
        allowed_document_version_ids=(tender_version.id, proposal_version.id),
        allowed_company_document_version_ids=(company_version.id,),
        allowed_chunk_ids=(tender_chunk.id, proposal_chunk.id, company_chunk.id),
        coverage=Coverage(
            document_version_ids=[tender_version.id, proposal_version.id],
            visible_chunk_ids=[tender_chunk.id, proposal_chunk.id],
        ),
    )
    return project, run, requirement, proposal_version, company_version, context


def _candidate(requirement_id: int, *, evidence=None) -> AssessmentCandidate:
    return AssessmentCandidate(
        requirement_id=requirement_id,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        needs_confirmation=False,
        reasoning="响应材料提供了对应证据。",
        evidence=list(evidence or []),
        recommendation="保留当前材料。",
    )


def test_read_tools_are_project_scoped_and_return_compact_citations(db_session) -> None:
    project, _, _, proposal_version, _, context = _setup(db_session)
    toolbox = ReviewToolbox(db_session, context)

    page = toolbox.get_document_page(proposal_version.id, 1)
    assert page[0]["document_version_id"] == proposal_version.id
    assert page[0]["quote"] == "我方提供有效营业执照，证照编号为 A-001。"
    assert toolbox.search_proposal_evidence(
        project.id, "营业执照", 5
    )[0]["document_version_id"] == proposal_version.id

    with pytest.raises(ValueError, match="project scope"):
        toolbox.search_proposal_evidence(project.id + 1, "营业执照", 5)


def test_company_search_uses_only_explicit_project_authorization(db_session) -> None:
    project, _, _, _, company_version, context = _setup(db_session)
    toolbox = ReviewToolbox(db_session, context)

    results = toolbox.search_company_evidence(project.id, "营业执照", 5)
    assert results
    assert {item["document_version_id"] for item in results} == {company_version.id}


def test_save_assessment_rejects_unsupported_matched_pass(db_session) -> None:
    _, _, requirement, _, _, context = _setup(db_session)
    toolbox = ReviewToolbox(db_session, context)

    with pytest.raises(UnsupportedPassError, match="requires evidence"):
        toolbox.save_assessment(requirement.id, _candidate(requirement.id))


def test_save_assessment_rejects_stale_evidence_and_persists_valid_result(db_session) -> None:
    _, _, requirement, proposal_version, _, context = _setup(db_session)
    toolbox = ReviewToolbox(db_session, context)
    stale = _candidate(
        requirement.id,
        evidence=[
            SourceCitation(
                document_version_id=999,
                page_number=1,
                quote="我方提供有效营业执照",
            )
        ],
    )
    with pytest.raises(StaleEvidenceError, match="inactive document version"):
        toolbox.save_assessment(requirement.id, stale)

    candidate = _candidate(
        requirement.id,
        evidence=[
            SourceCitation(
                document_version_id=proposal_version.id,
                page_number=1,
                quote="我方提供有效营业执照",
            )
        ],
    )
    saved = toolbox.save_assessment(requirement.id, candidate)
    db_session.commit()
    assert saved.display_status == "satisfied"
    assert db_session.query(Assessment).count() == 1
    assert saved.evidence_links[0].document_version_id == proposal_version.id


def test_confirmation_and_action_tools_leave_audit_and_action_records(db_session) -> None:
    _, _, requirement, _, _, context = _setup(db_session)
    toolbox = ReviewToolbox(db_session, context)

    confirmation = toolbox.request_user_confirmation(
        requirement.id, "请确认资质是否在投标截止日前有效。", "文档未给出有效期。"
    )
    action = toolbox.create_action_item(
        requirement.id, "补充资质有效期", "请上传有效期证明。"
    )
    db_session.commit()

    assert confirmation["status"] == "awaiting_confirmation"
    assert action["status"] == "open"
    assert db_session.query(ActionItem).count() == 1
    assert db_session.query(AuditEvent).count() == 2


def test_sdk_tools_are_bounded_wrappers(db_session) -> None:
    _, _, _, _, _, context = _setup(db_session)
    tools = build_review_tools(db_session, context)
    assert [tool.name for tool in tools] == [
        "get_document_page",
        "search_proposal_evidence",
        "search_company_evidence",
        "save_assessment",
        "request_user_confirmation",
        "create_action_item",
    ]
