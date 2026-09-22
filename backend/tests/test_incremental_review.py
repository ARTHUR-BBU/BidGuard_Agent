from __future__ import annotations

from app.db import GuardedSession
from app.domain.enums import RequirementKind
from app.persistence.models import (
    Assessment,
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceLink,
    ProjectCompanyEvidence,
    Requirement,
    ReviewRun,
)
from app.services.decisions import (
    calculate_affected_requirement_ids,
    start_incremental_review,
)
from app.settings import Settings


def _version(document: Document, number: int, digest: str, text: str) -> DocumentVersion:
    version = DocumentVersion(
        document=document,
        version_number=number,
        sha256=digest * 64,
        storage_path=f"{document.display_name}.v{number}",
        size_bytes=10,
        parse_status="parsed",
        parse_coverage={"total_pages": 1, "parsed_pages": [1], "coverage_issues": []},
    )
    version.chunks.append(
        DocumentChunk(
            document_version=version,
            chunk_index=0,
            page_number=1,
            text=text,
        )
    )
    return version


def _graph(db_session: GuardedSession):
    project = BidProject(name="增量复核测试项目")
    tender_document = Document(project=project, role="tender", display_name="tender.pdf")
    tender_v1 = _version(tender_document, 1, "a", "必须提供营业执照。")
    proposal_document = Document(project=project, role="proposal", display_name="proposal.docx")
    proposal_v1 = _version(proposal_document, 1, "b", "营业执照编号 A-001。")
    proposal_v2 = _version(proposal_document, 2, "c", "营业执照编号 A-002。")
    company_document = Document(
        role="company",
        display_name="company.pdf",
        company_content_sha256="d" * 64,
    )
    company_v1 = _version(company_document, 1, "d", "证书有效期至 2026 年。")
    company_v2 = _version(company_document, 2, "e", "证书有效期至 2027 年。")
    run = ReviewRun(
        project=project,
        status="completed",
        stage="assessment",
        model_provider="openai",
        model_name="test-model",
    )
    db_session.add_all(
        [project, tender_v1, proposal_v1, proposal_v2, company_v1, company_v2, run]
    )
    db_session.flush()
    requirement_proposal = Requirement(
        project_id=project.id,
        source_version_id=tender_v1.id,
        source_page=1,
        source_quote="必须提供营业执照。",
        text="营业执照响应",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
    )
    requirement_company = Requirement(
        project_id=project.id,
        source_version_id=tender_v1.id,
        source_page=1,
        source_quote="必须提供营业执照。",
        text="证书有效期",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
    )
    db_session.add_all([requirement_proposal, requirement_company])
    db_session.flush()
    db_session.add(
        ProjectCompanyEvidence(
            project_id=project.id,
            document_version_id=company_v1.id,
            selected_by="张三",
            active=True,
        )
    )
    db_session.add(
        ProjectCompanyEvidence(
            project_id=project.id,
            document_version_id=company_v2.id,
            selected_by="张三",
            active=True,
        )
    )
    assessment_proposal = Assessment(
        requirement_id=requirement_proposal.id,
        review_run_id=run.id,
        evidence_state="matched",
        severity="none",
        display_status="satisfied",
        needs_confirmation=False,
        reasoning="proposal evidence",
        recommendation="",
        current=True,
    )
    assessment_company = Assessment(
        requirement_id=requirement_company.id,
        review_run_id=run.id,
        evidence_state="matched",
        severity="none",
        display_status="satisfied",
        needs_confirmation=False,
        reasoning="company evidence",
        recommendation="",
        current=True,
    )
    db_session.add_all([assessment_proposal, assessment_company])
    db_session.flush()
    db_session.add_all(
        [
            EvidenceLink(
                assessment_id=assessment_proposal.id,
                document_version_id=proposal_v1.id,
                page_number=1,
                quote="营业执照编号 A-001。",
                purpose="assessment",
            ),
            EvidenceLink(
                assessment_id=assessment_company.id,
                document_version_id=company_v1.id,
                page_number=1,
                quote="证书有效期至 2026 年。",
                purpose="assessment",
            ),
        ]
    )
    db_session.commit()
    return (
        project,
        tender_v1,
        proposal_v1,
        proposal_v2,
        company_v1,
        company_v2,
        requirement_proposal,
        requirement_company,
        assessment_proposal,
        assessment_company,
    )


def test_new_proposal_version_only_invalidates_related_requirement(db_session) -> None:
    (
        project,
        _tender_v1,
        _proposal_v1,
        proposal_v2,
        _company_v1,
        _company_v2,
        requirement_proposal,
        requirement_company,
        assessment_proposal,
        assessment_company,
    ) = _graph(db_session)

    assert calculate_affected_requirement_ids(
        db_session,
        project_id=project.id,
        changed_version_ids=[proposal_v2.id],
    ) == (requirement_proposal.id,)
    result = start_incremental_review(
        db_session,
        project_id=project.id,
        changed_version_ids=[proposal_v2.id],
        actor="张三",
        settings=Settings(review_model="test-model"),
    )

    assert result.affected_requirement_ids == (requirement_proposal.id,)
    assert result.job.status == "queued"
    assert assessment_proposal.current is False
    assert assessment_company.current is True
    assert requirement_proposal.active is True
    assert requirement_company.active is True
    assert result.job.review_run_id is not None


def test_changed_tender_deactivates_old_matrix_and_keeps_history(db_session) -> None:
    (
        project,
        tender_v1,
        _proposal_v1,
        _proposal_v2,
        _company_v1,
        _company_v2,
        requirement_proposal,
        requirement_company,
        assessment_proposal,
        assessment_company,
    ) = _graph(db_session)
    tender_v2 = _version(tender_v1.document, 2, "f", "必须提供营业执照和授权委托书。")
    db_session.add(tender_v2)
    db_session.commit()

    result = start_incremental_review(
        db_session,
        project_id=project.id,
        changed_version_ids=[tender_v2.id],
        actor="张三",
        tender_version_id=tender_v2.id,
        settings=Settings(review_model="test-model"),
    )

    assert set(result.affected_requirement_ids) == {
        requirement_proposal.id,
        requirement_company.id,
    }
    assert requirement_proposal.active is False
    assert requirement_company.active is False
    assert assessment_proposal.current is False
    assert assessment_company.current is False
    assert db_session.get(Requirement, requirement_proposal.id) is not None
    assert result.job.status == "queued"


def test_company_version_change_invalidates_assessments_using_company_evidence(
    db_session,
) -> None:
    (
        project,
        _tender_v1,
        _proposal_v1,
        _proposal_v2,
        _company_v1,
        company_v2,
        _requirement_proposal,
        requirement_company,
        _assessment_proposal,
        assessment_company,
    ) = _graph(db_session)

    result = start_incremental_review(
        db_session,
        project_id=project.id,
        changed_version_ids=[company_v2.id],
        actor="张三",
        settings=Settings(review_model="test-model"),
    )

    assert result.affected_requirement_ids == (requirement_company.id,)
    assert assessment_company.current is False
