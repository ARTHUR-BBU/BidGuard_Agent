from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DateTime, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.domain.enums import (
    DisplayStatus,
    DocumentRole,
    EvidenceState,
    RequirementKind,
    ReviewRunStatus,
    Severity,
)
from app.main import create_app
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    BidProject,
    Decision,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceLink,
    Requirement,
    ReviewJob,
    ReviewRun,
)
from app.persistence.repositories import (
    get_document_version,
    get_project,
    get_requirement,
    list_project_documents,
    list_project_requirements,
)

EXPECTED_TABLES = {
    "action_items",
    "assessments",
    "audit_events",
    "bid_projects",
    "decisions",
    "document_chunks",
    "document_versions",
    "documents",
    "evidence_links",
    "requirements",
    "review_jobs",
    "review_runs",
}


def make_project_graph() -> tuple[BidProject, DocumentVersion, Requirement]:
    project = BidProject(name="数据治理平台", deadline_at=None)
    document = Document(
        project=project,
        role=DocumentRole.TENDER,
        display_name="招标文件.pdf",
    )
    version = DocumentVersion(
        document=document,
        version_number=1,
        sha256="a" * 64,
        storage_path="tender/a.pdf",
    )
    requirement = Requirement(
        project=project,
        source_version=version,
        source_page=12,
        source_quote="必须提供授权委托书",
        text="提供签字盖章的授权委托书",
        kind=RequirementKind.SUBSTANTIAL,
        mandatory=True,
    )
    return project, version, requirement


def test_requirement_points_to_exact_tender_version(db_session: Session) -> None:
    _project, _version, requirement = make_project_graph()

    db_session.add(requirement)
    db_session.commit()

    assert requirement.source_version.sha256 == "a" * 64
    assert requirement.source_page == 12


def test_requirement_generates_stable_nonempty_fingerprint(db_session: Session) -> None:
    project, version, first = make_project_graph()
    second = Requirement(
        project=project,
        source_version=version,
        source_quote="另一处原文",
        text="  提供签字盖章的授权委托书  ",
        kind=RequirementKind.SUBSTANTIAL,
        mandatory=True,
    )
    third = Requirement(
        project=project,
        source_version=version,
        source_quote="第三处原文",
        text="提供营业执照",
        kind=RequirementKind.SUBSTANTIAL,
        mandatory=True,
    )
    db_session.add_all([first, second, third])
    db_session.commit()

    assert len(first.fingerprint) == 64
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != third.fingerprint


def test_metadata_contains_exactly_the_bidguard_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


@pytest.mark.parametrize(
    "duplicate_factory",
    [
        pytest.param(
            lambda project, version, requirement, run: DocumentVersion(
                document=version.document,
                version_number=version.version_number,
                sha256="b" * 64,
                storage_path="tender/b.pdf",
            ),
            id="document-version-number",
        ),
        pytest.param(
            lambda project, version, requirement, run: DocumentChunk(
                document_version=version,
                chunk_index=0,
                text="duplicate chunk",
            ),
            id="document-chunk-index",
        ),
        pytest.param(
            lambda project, version, requirement, run: Assessment(
                requirement=requirement,
                review_run=run,
                evidence_state=EvidenceState.MATCHED,
                severity=Severity.NONE,
                display_status=DisplayStatus.SATISFIED,
                needs_confirmation=False,
                reasoning="duplicate assessment",
                recommendation="none",
            ),
            id="assessment-per-run",
        ),
    ],
)
def test_unique_pairs_reject_duplicates(db_session: Session, duplicate_factory) -> None:
    project, version, requirement = make_project_graph()
    chunk = DocumentChunk(document_version=version, chunk_index=0, text="first chunk")
    run = ReviewRun(
        project=project,
        status=ReviewRunStatus.REVIEWING,
        stage="assessment",
        model_provider="test",
        model_name="test-model",
    )
    assessment = Assessment(
        requirement=requirement,
        review_run=run,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        display_status=DisplayStatus.SATISFIED,
        needs_confirmation=False,
        reasoning="first assessment",
        recommendation="none",
    )
    db_session.add_all([chunk, assessment])
    db_session.commit()

    db_session.add(duplicate_factory(project, version, requirement, run))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize("role", [DocumentRole.TENDER, DocumentRole.PROPOSAL])
def test_non_company_document_requires_project(db_session: Session, role: DocumentRole) -> None:
    db_session.add(Document(project=None, role=role, display_name="orphan.pdf"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_company_document_can_be_reused_without_project(db_session: Session) -> None:
    document = Document(
        project=None,
        role=DocumentRole.COMPANY,
        display_name="营业执照.pdf",
    )

    db_session.add(document)
    db_session.commit()

    assert document.id > 0
    assert document.project is None


def test_relationships_are_bidirectional_and_source_version_is_exact(
    db_session: Session,
) -> None:
    project, version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    assert version.document.project is project
    assert version.document in project.documents
    assert version in version.document.versions
    assert requirement.project is project
    assert requirement.source_version is version


def test_database_generated_ids_and_utc_default_strategy(db_session: Session) -> None:
    project = BidProject(name="测试项目", deadline_at=None)
    db_session.add(project)
    db_session.flush()

    created_at_column = BidProject.__table__.c.created_at
    generated_at = created_at_column.default.arg(None)

    assert project.id >= 1
    assert isinstance(created_at_column.type, DateTime)
    assert created_at_column.type.timezone is True
    assert generated_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("target", ["requirement", "chunk", "evidence"])
def test_database_rejects_non_positive_page_numbers(
    db_session: Session,
    target: str,
) -> None:
    project, version, requirement = make_project_graph()
    run = ReviewRun(
        project=project,
        status=ReviewRunStatus.REVIEWING,
        stage="assessment",
        model_provider="test",
        model_name="test-model",
    )
    assessment = Assessment(
        requirement=requirement,
        review_run=run,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        display_status=DisplayStatus.SATISFIED,
        needs_confirmation=False,
        reasoning="source checked",
        recommendation="none",
    )
    if target == "requirement":
        requirement.source_page = 0
    elif target == "chunk":
        db_session.add(
            DocumentChunk(
                document_version=version,
                chunk_index=0,
                page_number=0,
                text="chunk",
            )
        )
    else:
        db_session.add(
            EvidenceLink(
                assessment=assessment,
                document_version=version,
                page_number=0,
                quote="evidence",
                purpose="support",
            )
        )
    db_session.add(assessment)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_all_models_can_be_persisted_together(db_session: Session) -> None:
    project, version, requirement = make_project_graph()
    run = ReviewRun(
        project=project,
        status=ReviewRunStatus.REVIEWING,
        stage="assessment",
        model_provider="test",
        model_name="test-model",
    )
    assessment = Assessment(
        requirement=requirement,
        review_run=run,
        evidence_state=EvidenceState.PARTIAL,
        severity=Severity.WARNING,
        display_status=DisplayStatus.NEEDS_EVIDENCE,
        needs_confirmation=False,
        reasoning="missing signature",
        recommendation="add signature",
    )
    db_session.add_all(
        [
            DocumentChunk(document_version=version, chunk_index=0, text="chunk"),
            EvidenceLink(
                assessment=assessment,
                document_version=version,
                quote="partial evidence",
                purpose="support",
            ),
            ActionItem(
                requirement=requirement,
                description="补充签章",
                status="open",
            ),
            Decision(
                requirement=requirement,
                decision="accept",
                explanation="confirmed by reviewer",
            ),
            AuditEvent(project=project, event_type="review_started", payload={"run": 1}),
            ReviewJob(project=project, status="queued", stage="created"),
        ]
    )
    db_session.commit()

    assert db_session.scalar(select(AuditEvent).where(AuditEvent.project_id == project.id))
    assert db_session.scalar(select(ReviewJob).where(ReviewJob.project_id == project.id))


def test_focused_repository_reads(db_session: Session) -> None:
    project, version, requirement = make_project_graph()
    company_document = Document(
        role=DocumentRole.COMPANY,
        display_name="公司资料.pdf",
    )
    db_session.add_all([requirement, company_document])
    db_session.commit()

    assert get_project(db_session, project.id) is project
    assert get_document_version(db_session, version.id) is version
    assert get_requirement(db_session, requirement.id) is requirement
    assert list_project_documents(db_session, project.id) == [version.document]
    assert list_project_requirements(db_session, project.id) == [requirement]
    assert get_project(db_session, 999_999) is None


def test_application_lifespan_bootstraps_schema(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "bootstrap.db"
    bootstrap_engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr("app.main.engine", bootstrap_engine)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200

    assert set(inspect(bootstrap_engine).get_table_names()) == EXPECTED_TABLES
    bootstrap_engine.dispose()
