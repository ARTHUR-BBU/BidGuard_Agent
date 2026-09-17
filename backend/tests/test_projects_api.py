from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import GuardedSession, get_db
from app.domain.enums import (
    DisplayStatus,
    EvidenceState,
    RequirementKind,
    ReviewRunStatus,
    Severity,
)
from app.main import create_app
from app.persistence.models import (
    Assessment,
    BidProject,
    Document,
    DocumentVersion,
    Requirement,
    ReviewRun,
)


@pytest.fixture
def client(db_session: GuardedSession) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _add_assessment(
    session: GuardedSession,
    project: BidProject,
    status: DisplayStatus,
    *,
    current: bool = True,
) -> None:
    document = Document(
        project=project,
        role="tender",
        display_name=f"{project.name}.pdf",
    )
    version = DocumentVersion(
        document=document,
        version_number=1,
        sha256="a" * 64,
        storage_path=f"tender/{project.name}.pdf",
    )
    requirement = Requirement(
        project=project,
        source_version=version,
        source_quote="招标文件原文",
        text=f"{project.name} requirement {status.value}",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
    )
    review_run = ReviewRun(
        project=project,
        status=ReviewRunStatus.COMPLETED,
        stage="complete",
        model_provider="test",
        model_name="test-model",
    )
    session.add(
        Assessment(
            requirement=requirement,
            review_run=review_run,
            evidence_state=EvidenceState.MATCHED,
            severity=Severity.NONE,
            display_status=status,
            needs_confirmation=status == DisplayStatus.NEEDS_CONFIRMATION,
            reasoning="reviewed against tender source",
            recommendation="none",
            current=current,
        )
    )


def test_project_collection_routes_create_and_list_projects(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "  城市更新投标  ", "deadline_at": "2026-10-01T09:00:00Z"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["name"] == "城市更新投标"
    assert payload["deadline_at"] == "2026-10-01T09:00:00Z"
    assert payload["status_counts"] == {
        "high_risk": 0,
        "needs_evidence": 0,
        "optimize": 0,
        "satisfied": 0,
        "needs_confirmation": 0,
    }

    without_deadline = client.post("/api/projects", json={"name": "无截止日期项目"})
    listed = client.get("/api/projects")

    assert without_deadline.status_code == 201
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [payload["id"], without_deadline.json()["id"]]


def test_project_detail_returns_project_and_stable_missing_error(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "详情项目"})

    detail = client.get(f"/api/projects/{created.json()['id']}")
    missing = client.get("/api/projects/999999")

    assert detail.status_code == 200
    assert detail.json()["id"] == created.json()["id"]
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Project not found"}


def test_project_list_orders_deadlines_then_newest_created_at(
    client: TestClient, db_session: GuardedSession
) -> None:
    projects = [
        BidProject(
            name="early",
            deadline_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        BidProject(
            name="same deadline older",
            deadline_at=datetime(2026, 10, 2, 9, tzinfo=UTC),
            created_at=datetime(2026, 9, 2, tzinfo=UTC),
        ),
        BidProject(
            name="same deadline newer",
            deadline_at=datetime(2026, 10, 2, 9, tzinfo=UTC),
            created_at=datetime(2026, 9, 3, tzinfo=UTC),
        ),
        BidProject(
            name="late",
            deadline_at=datetime(2026, 10, 3, 9, tzinfo=UTC),
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
        ),
        BidProject(
            name="no deadline older",
            deadline_at=None,
            created_at=datetime(2026, 9, 5, tzinfo=UTC),
        ),
        BidProject(
            name="no deadline newer",
            deadline_at=None,
            created_at=datetime(2026, 9, 6, tzinfo=UTC),
        ),
    ]
    db_session.add_all(projects)
    db_session.commit()

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == [
        "early",
        "same deadline newer",
        "same deadline older",
        "late",
        "no deadline newer",
        "no deadline older",
    ]


def test_project_status_counts_use_only_current_assessments_for_its_requirements(
    client: TestClient, db_session: GuardedSession
) -> None:
    first = BidProject(name="first", deadline_at=None)
    second = BidProject(name="second", deadline_at=None)
    _add_assessment(db_session, first, DisplayStatus.HIGH_RISK)
    _add_assessment(db_session, first, DisplayStatus.NEEDS_CONFIRMATION)
    _add_assessment(db_session, first, DisplayStatus.SATISFIED, current=False)
    _add_assessment(db_session, second, DisplayStatus.OPTIMIZE)
    db_session.add_all([first, second])
    db_session.commit()

    first_response = client.get(f"/api/projects/{first.id}")
    second_response = client.get(f"/api/projects/{second.id}")

    assert first_response.status_code == 200
    assert first_response.json()["status_counts"] == {
        "high_risk": 1,
        "needs_evidence": 0,
        "optimize": 0,
        "satisfied": 0,
        "needs_confirmation": 1,
    }
    assert second_response.status_code == 200
    assert second_response.json()["status_counts"] == {
        "high_risk": 0,
        "needs_evidence": 0,
        "optimize": 1,
        "satisfied": 0,
        "needs_confirmation": 0,
    }


def test_project_input_trims_name_and_rejects_invalid_values(client: TestClient) -> None:
    trimmed = client.post("/api/projects", json={"name": "  trimmed name  "})
    blank = client.post("/api/projects", json={"name": "   "})
    too_long = client.post("/api/projects", json={"name": "x" * 301})
    naive_deadline = client.post(
        "/api/projects",
        json={"name": "naive", "deadline_at": "2026-10-01T09:00:00"},
    )
    offset_deadline = client.post(
        "/api/projects",
        json={"name": "offset", "deadline_at": "2026-10-01T17:00:00+08:00"},
    )

    assert trimmed.status_code == 201
    assert trimmed.json()["name"] == "trimmed name"
    assert blank.status_code == 422
    assert too_long.status_code == 422
    assert naive_deadline.status_code == 422
    assert offset_deadline.status_code == 201
    assert offset_deadline.json()["deadline_at"] == "2026-10-01T09:00:00Z"
