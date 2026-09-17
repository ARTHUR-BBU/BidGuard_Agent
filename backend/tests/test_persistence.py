from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db import Base, GuardedSession, SessionLocal, UTCDateTime, build_engine
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
    calculate_requirement_fingerprint,
)
from app.persistence.repositories import (
    NewRequirement,
    create_requirements,
    get_document_version,
    get_project,
    get_requirement,
    list_project_documents,
    list_project_requirements,
    update_requirements_active,
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


def test_fingerprint_calculation_is_stable_for_normalized_identity() -> None:
    assert calculate_requirement_fingerprint(
        RequirementKind.SUBSTANTIAL,
        "  提供签字盖章的授权委托书  ",
    ) == calculate_requirement_fingerprint(
        "SUBSTANTIAL",
        "提供签字盖章的授权委托书",
    )


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


def requirement_mapping(
    project_id: int,
    source_version_id: int,
    *,
    text_value: str = "bulk requirement",
) -> dict[str, object]:
    return {
        "project_id": project_id,
        "source_version_id": source_version_id,
        "source_quote": "bulk source",
        "text": text_value,
        "kind": RequirementKind.TECHNICAL,
        "mandatory": True,
        "fingerprint": "z" * 64,
    }


def requirement_table() -> Table:
    return cast(Table, Requirement.__table__)


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


def test_explicit_fingerprint_is_replaced_with_calculated_identity(
    db_session: Session,
) -> None:
    _project, _version, requirement = make_project_graph()
    requirement.fingerprint = "z" * 64
    db_session.add(requirement)
    db_session.commit()

    expected = calculate_requirement_fingerprint(requirement.kind, requirement.text)
    assert requirement.fingerprint == expected
    assert requirement.fingerprint != "z" * 64


@pytest.mark.parametrize(
    ("attribute", "new_value"),
    [
        ("text", "被静默修改的要求"),
        ("kind", RequirementKind.COMMERCIAL),
        ("fingerprint", "b" * 64),
    ],
)
def test_persisted_requirement_identity_is_immutable(
    db_session: Session,
    attribute: str,
    new_value: object,
) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    setattr(requirement, attribute, new_value)

    with pytest.raises(ValueError, match="identity is immutable"):
        db_session.commit()


def test_persisted_requirement_active_state_can_be_updated(
    db_session: GuardedSession,
) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    requirement.active = False
    db_session.commit()

    assert requirement.active is False


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "bulk text change"},
        {"kind": RequirementKind.COMMERCIAL},
    ],
)
def test_bulk_dml_cannot_change_requirement_identity(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            update(Requirement).where(Requirement.id == requirement.id).values(**changes)
        )


def test_execute_bulk_update_by_primary_key_is_rejected(db_session: Session) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            update(Requirement),
            [{"id": requirement.id, "text": "bulk primary-key update"}],
        )


def test_execute_ordered_values_update_is_rejected(db_session: Session) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            update(Requirement)
            .where(Requirement.id == requirement.id)
            .ordered_values((Requirement.text, "ordered update"))
        )


def test_bulk_update_mappings_for_requirement_is_rejected(db_session: Session) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk UPDATE mappings"):
        db_session.bulk_update_mappings(
            Requirement,
            [{"id": requirement.id, "text": "legacy bulk update"}],
        )


def test_execute_bulk_insert_for_requirement_is_rejected(db_session: Session) -> None:
    project, version, _requirement = make_project_graph()
    db_session.add(project)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            insert(Requirement),
            [requirement_mapping(project.id, version.id)],
        )


def test_execute_core_table_update_for_requirement_is_rejected(
    db_session: Session,
) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            update(requirement_table())
            .where(requirement_table().c.id == requirement.id)
            .values(text="core table update")
        )


def test_execute_core_table_insert_for_requirement_is_rejected(
    db_session: Session,
) -> None:
    project, version, _requirement = make_project_graph()
    db_session.add(project)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT/UPDATE"):
        db_session.execute(
            insert(requirement_table()),
            [requirement_mapping(project.id, version.id)],
        )


def test_bulk_insert_mappings_for_requirement_is_rejected(db_session: Session) -> None:
    project, version, _requirement = make_project_graph()
    db_session.add(project)
    db_session.commit()

    with pytest.raises(ValueError, match="bulk INSERT mappings"):
        db_session.bulk_insert_mappings(
            Requirement,
            [requirement_mapping(project.id, version.id)],
        )


def test_application_sessions_use_guarded_session(db_session: Session) -> None:
    application_session = SessionLocal()
    try:
        assert isinstance(application_session, GuardedSession)
        assert isinstance(db_session, GuardedSession)
    finally:
        application_session.close()


def test_repository_creates_requirements_with_calculated_fingerprint(
    db_session: GuardedSession,
) -> None:
    project, version, _requirement = make_project_graph()
    db_session.add(project)
    db_session.flush()
    inputs = [
        NewRequirement(
            project_id=project.id,
            source_version_id=version.id,
            source_page=7,
            source_section="资格要求",
            source_quote="必须提供营业执照",
            text="提供有效的营业执照",
            kind=RequirementKind.QUALIFICATION,
            mandatory=True,
        )
    ]

    created = create_requirements(db_session, inputs)

    assert len(created) == 1
    assert created[0].id > 0
    assert created[0].fingerprint == calculate_requirement_fingerprint(
        RequirementKind.QUALIFICATION,
        "提供有效的营业执照",
    )
    assert "fingerprint" not in NewRequirement.__dataclass_fields__


def test_repository_updates_only_requirement_active_state(
    db_session: GuardedSession,
) -> None:
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()

    updated = update_requirements_active(
        db_session,
        [requirement.id],
        active=False,
    )
    db_session.commit()
    db_session.expire_all()
    loaded = db_session.get(Requirement, requirement.id)

    assert [item.id for item in updated] == [requirement.id]
    assert loaded is not None
    assert loaded.active is False


def test_other_models_keep_all_bulk_write_paths(db_session: Session) -> None:
    first = BidProject(name="first", deadline_at=None)
    second = BidProject(name="second", deadline_at=None)
    db_session.add_all([first, second])
    db_session.commit()

    db_session.execute(
        update(BidProject).where(BidProject.id == first.id).values(name="execute update")
    )
    db_session.bulk_update_mappings(
        BidProject,
        [{"id": second.id, "name": "mapping update"}],
    )
    db_session.execute(
        insert(BidProject),
        [{"name": "execute insert", "deadline_at": None}],
    )
    db_session.bulk_insert_mappings(
        BidProject,
        [{"name": "mapping insert", "deadline_at": None}],
    )
    db_session.commit()

    assert set(db_session.scalars(select(BidProject.name))) == {
        "execute update",
        "mapping update",
        "execute insert",
        "mapping insert",
    }


def test_metadata_contains_exactly_the_bidguard_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_metadata_has_stable_constraint_naming_convention() -> None:
    assert set(Base.metadata.naming_convention) >= {"pk", "fk", "ix", "uq", "ck"}
    for table in Base.metadata.tables.values():
        assert table.primary_key.name is not None
        assert all(constraint.name is not None for constraint in table.foreign_key_constraints)
        assert all(constraint.name is not None for constraint in table.constraints)
        assert all(index.name is not None for index in table.indexes)


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
    _project, _version, requirement = make_project_graph()
    db_session.add(requirement)
    db_session.commit()
    requirement_id = requirement.id
    db_session.expire_all()

    loaded_requirement = db_session.get(Requirement, requirement_id)

    assert loaded_requirement is not None
    assert loaded_requirement.source_version.document.project_id == loaded_requirement.project_id
    assert [item.id for item in loaded_requirement.project.documents] == [
        loaded_requirement.source_version.document_id
    ]
    assert [item.id for item in loaded_requirement.source_version.document.versions] == [
        loaded_requirement.source_version_id
    ]


def test_database_generated_ids_and_utc_default_strategy(db_session: Session) -> None:
    project = BidProject(name="测试项目", deadline_at=None)
    db_session.add(project)
    db_session.flush()

    created_at_column = BidProject.__table__.c.created_at
    generated_at = created_at_column.default.arg(None)

    assert project.id >= 1
    assert isinstance(created_at_column.type, UTCDateTime)
    assert created_at_column.type.impl.timezone is True
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


def test_build_engine_enables_sqlite_foreign_keys() -> None:
    sqlite_engine = build_engine("sqlite://")

    with sqlite_engine.connect() as connection:
        foreign_keys = connection.scalar(text("PRAGMA foreign_keys"))

    sqlite_engine.dispose()
    assert foreign_keys == 1


def test_sqlite_rejects_invalid_foreign_key() -> None:
    sqlite_engine = build_engine("sqlite://")
    Base.metadata.create_all(sqlite_engine)

    with Session(sqlite_engine) as session:
        session.add(
            Document(
                project_id=999_999,
                role=DocumentRole.TENDER,
                display_name="missing-project.pdf",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    sqlite_engine.dispose()


def test_deleting_project_cascades_complete_project_graph() -> None:
    sqlite_engine = build_engine("sqlite://")
    Base.metadata.create_all(sqlite_engine)
    model_types = (
        BidProject,
        Document,
        DocumentVersion,
        DocumentChunk,
        Requirement,
        ReviewRun,
        Assessment,
        EvidenceLink,
        ActionItem,
        Decision,
        AuditEvent,
        ReviewJob,
    )

    with Session(sqlite_engine) as session:
        project, version, requirement = make_project_graph()
        run = ReviewRun(
            project=project,
            status=ReviewRunStatus.COMPLETED,
            stage="complete",
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
            reasoning="complete",
            recommendation="none",
        )
        session.add_all(
            [
                DocumentChunk(document_version=version, chunk_index=0, text="chunk"),
                EvidenceLink(
                    assessment=assessment,
                    document_version=version,
                    quote="evidence",
                    purpose="support",
                ),
                ActionItem(
                    requirement=requirement,
                    description="done",
                    status="completed",
                ),
                Decision(
                    requirement=requirement,
                    decision="accept",
                    explanation="verified",
                ),
                AuditEvent(project=project, event_type="completed", payload={}),
                ReviewJob(project=project, status="completed", stage="complete"),
            ]
        )
        session.commit()
        project_id = project.id

    with Session(sqlite_engine) as session:
        persisted_project = session.get(BidProject, project_id)
        assert persisted_project is not None
        session.delete(persisted_project)
        session.commit()

    with Session(sqlite_engine) as session:
        remaining = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in model_types
        }

    sqlite_engine.dispose()
    assert remaining == {model.__tablename__: 0 for model in model_types}


def test_all_model_datetimes_round_trip_as_utc_in_new_session() -> None:
    sqlite_engine = build_engine("sqlite://")
    Base.metadata.create_all(sqlite_engine)
    china_time = timezone(timedelta(hours=8))
    supplied_time = datetime(2030, 1, 2, 9, 30, tzinfo=china_time)

    with Session(sqlite_engine) as session:
        project, version, requirement = make_project_graph()
        project.deadline_at = supplied_time
        run = ReviewRun(
            project=project,
            status=ReviewRunStatus.COMPLETED,
            stage="complete",
            model_provider="test",
            model_name="test-model",
            completed_at=supplied_time,
        )
        action = ActionItem(
            requirement=requirement,
            description="done",
            status="completed",
            completed_at=supplied_time,
        )
        decision = Decision(
            requirement=requirement,
            decision="accept",
            explanation="verified",
        )
        audit = AuditEvent(project=project, event_type="completed", payload={})
        job = ReviewJob(project=project, status="completed", stage="complete")
        session.add_all([run, action, decision, audit, job])
        session.commit()
        persisted_ids = {
            "project": project.id,
            "version": version.id,
            "run": run.id,
            "action": action.id,
            "decision": decision.id,
            "audit": audit.id,
            "job": job.id,
        }

    with Session(sqlite_engine) as session:
        loaded_project = session.get(BidProject, persisted_ids["project"])
        loaded_version = session.get(DocumentVersion, persisted_ids["version"])
        loaded_run = session.get(ReviewRun, persisted_ids["run"])
        loaded_action = session.get(ActionItem, persisted_ids["action"])
        loaded_decision = session.get(Decision, persisted_ids["decision"])
        loaded_audit = session.get(AuditEvent, persisted_ids["audit"])
        loaded_job = session.get(ReviewJob, persisted_ids["job"])
        assert loaded_project is not None
        assert loaded_version is not None
        assert loaded_run is not None
        assert loaded_action is not None
        assert loaded_decision is not None
        assert loaded_audit is not None
        assert loaded_job is not None
        loaded_times = [
            loaded_project.created_at,
            loaded_project.deadline_at,
            loaded_version.document.created_at,
            loaded_version.uploaded_at,
            loaded_run.started_at,
            loaded_run.completed_at,
            loaded_action.created_at,
            loaded_action.completed_at,
            loaded_decision.created_at,
            loaded_audit.created_at,
            loaded_job.created_at,
            loaded_job.updated_at,
        ]

        assert all(value is not None for value in loaded_times)
        assert all(value.utcoffset() == timedelta(0) for value in loaded_times if value)
        assert loaded_project.deadline_at == datetime(2030, 1, 2, 1, 30, tzinfo=UTC)

    sqlite_engine.dispose()


def test_naive_datetime_is_rejected_on_write(db_session: Session) -> None:
    db_session.add(
        BidProject(
            name="naive time",
            deadline_at=datetime(2030, 1, 2, 9, 30),  # noqa: DTZ001
        )
    )

    with pytest.raises(StatementError, match="timezone-aware"):
        db_session.commit()
