from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

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
def app(db_session: GuardedSession) -> Iterator[FastAPI]:
    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        class_=GuardedSession,
        expire_on_commit=False,
    )

    def get_request_db() -> Iterator[GuardedSession]:
        with session_factory() as request_session:
            try:
                yield request_session
            except Exception:
                request_session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_db] = get_request_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _add_assessment(
    session: GuardedSession,
    project: BidProject,
    status: DisplayStatus,
    *,
    current: bool = True,
    requirement_active: bool = True,
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
        active=requirement_active,
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


def test_project_status_counts_exclude_inactive_requirements(
    client: TestClient, db_session: GuardedSession
) -> None:
    project = BidProject(name="active requirement project", deadline_at=None)
    _add_assessment(db_session, project, DisplayStatus.HIGH_RISK)
    _add_assessment(
        db_session,
        project,
        DisplayStatus.NEEDS_EVIDENCE,
        requirement_active=False,
    )
    db_session.add(project)
    db_session.commit()

    response = client.get(f"/api/projects/{project.id}")

    assert response.status_code == 200
    assert response.json()["status_counts"] == {
        "high_risk": 1,
        "needs_evidence": 0,
        "optimize": 0,
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


@pytest.mark.parametrize("name", ["\u200b", "\ufeff", " \u200b\ufeff "])
def test_project_name_rejects_only_invisible_characters(
    client: TestClient, name: str
) -> None:
    response = client.post("/api/projects", json={"name": name})

    assert response.status_code == 422


def test_project_name_preserves_visible_unicode_and_format_characters(
    client: TestClient,
) -> None:
    name = "  团队👩\u200d💻\u200b投标  "

    response = client.post("/api/projects", json={"name": name})

    assert response.status_code == 201
    assert response.json()["name"] == "团队👩\u200d💻\u200b投标"


@pytest.mark.parametrize(
    "deadline_at",
    [1790845200, 1790845200.0, "1790845200", True, False, "", "not-an-iso-date"],
)
def test_project_deadline_rejects_non_iso_json_values(
    client: TestClient, deadline_at: object
) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "invalid deadline", "deadline_at": deadline_at},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "deadline_at",
    [
        "2026-10-01X09:00:00Z",
        "2026-10-01_09:00:00+08:00",
        "2026-10-01 09:00:00Z",
        "2026-10-01T09:00Z",
        "2026-10-01T09:00:00+0800",
        "2026-10-01T09:00:00+08:60",
        "2026-02-30T09:00:00Z",
        "2026-10-01T25:00:00Z",
    ],
)
def test_project_deadline_rejects_near_iso_formats(
    client: TestClient, deadline_at: str
) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "near iso deadline", "deadline_at": deadline_at},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("deadline_at", "expected_utc"),
    [
        ("  2026-10-01T09:00:00Z  ", "2026-10-01T09:00:00Z"),
        ("2026-10-01T17:00:00+08:00", "2026-10-01T09:00:00Z"),
        ("2026-10-01T04:00:00-05:00", "2026-10-01T09:00:00Z"),
        ("2026-10-01T09:00:00.1Z", "2026-10-01T09:00:00.100000Z"),
        ("2026-10-01T09:00:00.123456Z", "2026-10-01T09:00:00.123456Z"),
    ],
)
def test_project_deadline_accepts_strict_iso_boundaries(
    client: TestClient, deadline_at: str, expected_utc: str
) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "strict iso deadline", "deadline_at": deadline_at},
    )

    assert response.status_code == 201
    assert response.json()["deadline_at"] == expected_utc


@pytest.mark.parametrize(
    "deadline_at",
    ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"],
)
def test_project_deadline_rejects_utc_conversion_overflow(
    app: FastAPI, deadline_at: str
) -> None:
    with TestClient(app, raise_server_exceptions=False) as error_client:
        response = error_client.post(
            "/api/projects",
            json={"name": "overflow deadline", "deadline_at": deadline_at},
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("project_id", "expected_status"),
    [
        (0, 422),
        (-1, 422),
        (9_223_372_036_854_775_807, 404),
        (9_223_372_036_854_775_808, 422),
    ],
)
def test_project_detail_validates_database_safe_identifier_bounds(
    client: TestClient, project_id: int, expected_status: int
) -> None:
    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == expected_status
    if expected_status == 404:
        assert response.json() == {"detail": "Project not found"}


def test_project_create_persists_across_independent_request_sessions(
    client: TestClient,
) -> None:
    created = client.post("/api/projects", json={"name": "persistent project"})

    detail = client.get(f"/api/projects/{created.json()['id']}")
    listed = client.get("/api/projects")

    assert created.status_code == 201
    assert detail.status_code == 200
    assert [item["name"] for item in listed.json()] == ["persistent project"]


def test_failed_project_insert_rolls_back_and_next_request_succeeds(
    app: FastAPI, db_session: GuardedSession
) -> None:
    engine = db_session.get_bind()
    fail_next_project_insert = True

    def fail_one_project_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: Any,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal fail_next_project_insert
        if fail_next_project_insert and "INSERT INTO bid_projects" in statement:
            fail_next_project_insert = False
            raise OperationalError(statement, parameters, Exception("forced insert failure"))

    event.listen(engine, "before_cursor_execute", fail_one_project_insert)
    try:
        with TestClient(app, raise_server_exceptions=False) as error_client:
            failed = error_client.post("/api/projects", json={"name": "failed project"})
            created = error_client.post("/api/projects", json={"name": "recovered project"})
            listed = error_client.get("/api/projects")
    finally:
        event.remove(engine, "before_cursor_execute", fail_one_project_insert)

    assert failed.status_code == 500
    assert created.status_code == 201
    assert [item["name"] for item in listed.json()] == ["recovered project"]
