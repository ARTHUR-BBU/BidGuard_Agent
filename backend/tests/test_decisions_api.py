from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.decisions import DecisionRequest
from app.db import GuardedSession, get_db
from app.main import create_app
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    Requirement,
    ReviewRun,
)
from app.services.decisions import (
    ActionItemScopeError,
    DecisionValidationError,
    complete_action_item,
    record_decision,
)


def _graph(db_session: GuardedSession):
    project = BidProject(name="人工决定测试项目")
    tender = Document(project=project, role="tender", display_name="tender.pdf")
    version = DocumentVersion(
        document=tender,
        version_number=1,
        sha256="a" * 64,
        storage_path="tender.pdf",
        size_bytes=10,
        parse_status="parsed",
        parse_coverage={"total_pages": 1, "parsed_pages": [1], "coverage_issues": []},
    )
    version.chunks.append(
        DocumentChunk(
            document_version=version,
            chunk_index=0,
            page_number=1,
            text="投标人必须提供营业执照。",
        )
    )
    run = ReviewRun(
        project=project,
        status="completed",
        stage="assessment",
        model_provider="openai",
        model_name="test-model",
    )
    requirement = Requirement(
        project_id=0,
        source_version_id=0,
        source_page=1,
        source_quote="投标人必须提供营业执照。",
        text="提供营业执照",
        kind="qualification",
        mandatory=True,
    )
    db_session.add_all([project, version, run])
    db_session.flush()
    requirement.project_id = project.id
    requirement.source_version_id = version.id
    db_session.add(requirement)
    db_session.flush()
    assessment = Assessment(
        requirement_id=requirement.id,
        review_run_id=run.id,
        evidence_state="uncertain",
        severity="warning",
        display_status="needs_confirmation",
        needs_confirmation=True,
        reasoning="需要负责人确认。",
        recommendation="确认材料有效期。",
        current=True,
    )
    action = ActionItem(
        requirement_id=requirement.id,
        description="补充有效期证明",
        status="open",
    )
    db_session.add_all([assessment, action])
    db_session.commit()
    return project, requirement, assessment, action


def test_record_decision_is_append_only_and_audited(db_session) -> None:
    project, requirement, assessment, _action = _graph(db_session)

    decision = record_decision(
        db_session,
        requirement_id=requirement.id,
        decision="confirm",
        explanation="负责人确认截止日前仍然有效。",
        actor="张三",
    )
    db_session.commit()

    assert decision.actor == "张三"
    assert decision.assessment_id == assessment.id
    assert decision.version_ids == [requirement.source_version_id]
    event = db_session.query(AuditEvent).filter_by(event_type="human_decision_recorded").one()
    assert event.project_id == project.id
    assert event.payload["decision_id"] == decision.id

    second = record_decision(
        db_session,
        requirement_id=requirement.id,
        decision="deny",
        explanation="复核后不能确认。",
        actor="李四",
    )
    db_session.commit()
    assert second.id != decision.id
    assert db_session.query(AuditEvent).filter_by(event_type="human_decision_recorded").count() == 2


def test_decision_requires_bounded_explanation(db_session) -> None:
    _, requirement, _, _ = _graph(db_session)
    with pytest.raises(DecisionValidationError):
        record_decision(
            db_session,
            requirement_id=requirement.id,
            decision="confirm",
            explanation=" ",
            actor="张三",
        )
    with pytest.raises(DecisionValidationError):
        record_decision(
            db_session,
            requirement_id=requirement.id,
            decision="unknown",
            explanation="说明",
            actor="张三",
        )


def test_action_item_completion_is_audited_and_not_repeatable(db_session) -> None:
    project, _requirement, _assessment, action = _graph(db_session)
    completed = complete_action_item(
        db_session,
        action_item_id=action.id,
        actor="张三",
    )
    db_session.commit()
    assert completed.status == "completed"
    assert completed.completed_by == "张三"
    assert completed.completed_at is not None
    event = db_session.query(AuditEvent).filter_by(event_type="action_item_completed").one()
    assert event.project_id == project.id
    with pytest.raises(ActionItemScopeError):
        complete_action_item(db_session, action_item_id=action.id, actor="张三")


def test_decision_request_contract_requires_actor_and_explanation() -> None:
    request = DecisionRequest(
        decision="not_applicable",
        explanation="招标文件明确不要求随标提交。",
        actor="项目负责人",
    )
    assert request.actor == "项目负责人"


def test_requirement_with_decision_history_cannot_be_deleted(db_session) -> None:
    _project, requirement, _assessment, _action = _graph(db_session)
    record_decision(
        db_session,
        requirement_id=requirement.id,
        decision="confirm",
        explanation="保留历史决定。",
        actor="张三",
    )
    db_session.commit()
    db_session.delete(requirement)
    with pytest.raises(ValueError, match="cannot be deleted"):
        db_session.flush()
    db_session.rollback()


def test_decision_and_action_endpoints_persist_user_action(
    db_session: GuardedSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, requirement, _assessment, action = _graph(db_session)
    engine = db_session.get_bind()
    monkeypatch.setattr("app.main.engine", engine)
    factory = sessionmaker(bind=engine, class_=GuardedSession, expire_on_commit=False)

    def override_db():
        with factory() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        decision_response = client.post(
            f"/api/requirements/{requirement.id}/decisions",
            json={
                "decision": "confirm",
                "explanation": "负责人已核验原件。",
                "actor": "项目负责人",
            },
        )
        action_response = client.post(
            f"/api/action-items/{action.id}/complete",
            json={"actor": "项目负责人"},
        )
    application.dependency_overrides.clear()

    assert decision_response.status_code == 201
    assert decision_response.json()["actor"] == "项目负责人"
    assert action_response.status_code == 200
    assert action_response.json()["status"] == "completed"
