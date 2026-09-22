from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.api.reviews import ReviewStartRequest
from app.db import GuardedSession
from app.domain.enums import RequirementKind, ReviewRunStatus
from app.jobs.handlers import start_review
from app.jobs.worker import (
    ReviewWorker,
    claim_next_job,
    enqueue_job,
    mark_job_complete,
    requeue_interrupted_jobs,
)
from app.persistence.models import (
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    Requirement,
    ReviewJob,
    ReviewRun,
)
from app.services.reviews import ReviewResult
from app.settings import Settings


def _graph(db_session: GuardedSession):
    project = BidProject(name="任务测试项目")
    tender = Document(project=project, role="tender", display_name="tender.pdf")
    version = DocumentVersion(
        document=tender,
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
        status=ReviewRunStatus.QUEUED,
        stage="created",
        model_provider="openai",
        model_name="test-model",
    )
    db_session.add_all([project, version, run])
    db_session.flush()
    db_session.add(
        Requirement(
            project_id=project.id,
            source_version_id=version.id,
            source_page=1,
            source_quote="投标人必须提供营业执照。",
            text="提供营业执照",
            kind=RequirementKind.QUALIFICATION,
            mandatory=True,
        )
    )
    db_session.commit()
    return project, version, run


def _factory(db_session: GuardedSession):
    @contextmanager
    def make_session():
        yield db_session

    return make_session


def test_enqueue_claim_complete_and_requeue_increment_attempt(db_session) -> None:
    project, version, run = _graph(db_session)
    job = enqueue_job(
        db_session,
        project_id=project.id,
        review_run_id=run.id,
        tender_version_id=version.id,
        version_fingerprint="b" * 64,
    )
    db_session.commit()
    assert job.status == "queued"

    claimed = claim_next_job(db_session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    db_session.commit()

    claimed.status = "running"
    claimed.claimed_at = datetime.now(UTC)
    db_session.commit()
    assert requeue_interrupted_jobs(db_session) == 1
    db_session.commit()
    assert claimed.status == "queued"
    assert "interrupted" in (claimed.error or "")

    claimed_again = claim_next_job(db_session)
    assert claimed_again is not None
    assert claimed_again.attempt_count == 2
    mark_job_complete(db_session, claimed_again.id, stage="completed")
    db_session.commit()
    assert claimed_again.status == "completed"


@pytest.mark.asyncio
async def test_worker_claims_job_and_persists_completed_result(db_session) -> None:
    project, version, run = _graph(db_session)
    enqueue_job(
        db_session,
        project_id=project.id,
        review_run_id=run.id,
        tender_version_id=version.id,
        version_fingerprint="c" * 64,
    )
    db_session.commit()
    handled: list[int] = []

    async def handler(job_id: int, _session_factory: Any) -> ReviewResult:
        handled.append(job_id)
        return ReviewResult("completed", 1, 1)

    worker = ReviewWorker(
        _factory(db_session),
        handler=handler,
        poll_interval_seconds=0,
    )
    assert await worker.run_once() is True
    assert handled == [1]
    refreshed = db_session.get(ReviewJob, 1)
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.stage == "completed"


def test_review_start_request_allows_optional_tender_version() -> None:
    assert ReviewStartRequest().tender_version_id is None


def test_start_review_is_idempotent_for_the_same_current_file_scope(db_session) -> None:
    project, version, _run = _graph(db_session)
    settings = Settings(review_model="test-model")

    first, first_created = start_review(
        db_session,
        project_id=project.id,
        tender_version_id=version.id,
        settings=settings,
    )
    second, second_created = start_review(
        db_session,
        project_id=project.id,
        tender_version_id=version.id,
        settings=settings,
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
