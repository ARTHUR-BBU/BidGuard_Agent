from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update

from app.db import GuardedSession
from app.persistence.models import ReviewJob

JobHandler = Callable[[int, Any], Any]


@contextmanager
def session_scope(
    session_factory: Callable[
        [], GuardedSession | AbstractContextManager[GuardedSession]
    ],
) -> Iterator[GuardedSession]:
    candidate = session_factory()
    if hasattr(candidate, "__enter__") and hasattr(candidate, "__exit__"):
        context = cast(AbstractContextManager[GuardedSession], candidate)
        with context as session:
            yield session
        return
    yield cast(GuardedSession, candidate)


def enqueue_job(
    session: GuardedSession,
    *,
    project_id: int,
    review_run_id: int,
    tender_version_id: int,
    version_fingerprint: str,
    version_ids: list[int] | None = None,
) -> ReviewJob:
    """Create a queued job; the caller owns the transaction."""

    if len(version_fingerprint) != 64:
        raise ValueError("review version fingerprint must be a SHA-256 hex digest")
    job = ReviewJob(
        project_id=project_id,
        review_run_id=review_run_id,
        tender_version_id=tender_version_id,
        version_fingerprint=version_fingerprint,
        version_ids=list(version_ids or [tender_version_id]),
        status="queued",
        stage="created",
        attempt_count=0,
    )
    session.add(job)
    session.flush()
    return job


def find_active_job(
    session: GuardedSession,
    *,
    project_id: int,
    version_fingerprint: str,
) -> ReviewJob | None:
    return session.scalar(
        select(ReviewJob)
        .where(
            ReviewJob.project_id == project_id,
            ReviewJob.version_fingerprint == version_fingerprint,
            ReviewJob.status.in_(("queued", "running", "awaiting_confirmation")),
        )
        .order_by(ReviewJob.id.desc())
        .limit(1)
    )


def claim_next_job(session: GuardedSession) -> ReviewJob | None:
    """Atomically move the oldest queued job to running and increment attempts."""

    candidate_id = session.scalar(
        select(ReviewJob.id)
        .where(ReviewJob.status == "queued")
        .order_by(ReviewJob.id)
        .limit(1)
    )
    if candidate_id is None:
        return None
    now = datetime.now(UTC)
    result = session.execute(
        update(ReviewJob)
        .where(ReviewJob.id == candidate_id, ReviewJob.status == "queued")
        .values(
            status="running",
            stage="starting",
            attempt_count=ReviewJob.attempt_count + 1,
            claimed_at=now,
            finished_at=None,
            error=None,
        )
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        session.rollback()
        return None
    session.flush()
    return session.get(ReviewJob, candidate_id)


def mark_job_complete(
    session: GuardedSession,
    job_id: int,
    *,
    stage: str = "completed",
    status: str = "completed",
) -> ReviewJob:
    job = session.get(ReviewJob, job_id)
    if job is None:
        raise ValueError("review job does not exist")
    if status not in {"completed", "awaiting_confirmation"}:
        raise ValueError("invalid completed review job status")
    job.status = status
    job.stage = stage
    job.finished_at = datetime.now(UTC)
    job.claimed_at = None
    job.error = None
    session.flush()
    return job


def mark_job_failed(
    session: GuardedSession,
    job_id: int,
    error: str,
    *,
    stage: str = "failed",
) -> ReviewJob:
    job = session.get(ReviewJob, job_id)
    if job is None:
        raise ValueError("review job does not exist")
    job.status = "failed"
    job.stage = stage
    job.error = error.strip()[:2000] or "review job failed"
    job.finished_at = datetime.now(UTC)
    job.claimed_at = None
    session.flush()
    return job


def requeue_interrupted_jobs(session: GuardedSession) -> int:
    """Return jobs left running by a stopped process to the durable queue."""

    result = session.execute(
        update(ReviewJob)
        .where(ReviewJob.status == "running")
        .values(
            status="queued",
            stage="requeued",
            error="requeued after interrupted worker process",
            claimed_at=None,
        )
        .execution_options(synchronize_session="fetch")
    )
    session.flush()
    session.expire_all()
    return int(getattr(result, "rowcount", 0) or 0)


class ReviewWorker:
    """One asyncio polling worker backed by the database job table."""

    def __init__(
        self,
        session_factory: Callable[
            [], GuardedSession | AbstractContextManager[GuardedSession]
        ],
        *,
        handler: JobHandler | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll interval cannot be negative")
        self.session_factory = session_factory
        self.handler = handler
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()

    def recover_interrupted_jobs(self) -> int:
        with session_scope(self.session_factory) as session:
            count = requeue_interrupted_jobs(session)
            session.commit()
            return count

    async def run_once(self) -> bool:
        with session_scope(self.session_factory) as session:
            job = claim_next_job(session)
            if job is None:
                session.rollback()
                return False
            job_id = job.id
            session.commit()

        handler = self.handler
        if handler is None:
            from app.jobs.handlers import process_review_job

            handler = process_review_job
        try:
            result = handler(job_id, self.session_factory)
            if inspect.isawaitable(result):
                result = await result
            status = getattr(result, "status", "completed")
            with session_scope(self.session_factory) as session:
                if status == "partial_failure":
                    mark_job_failed(
                        session,
                        job_id,
                        "review run returned partial_failure",
                        stage="partial_failure",
                    )
                else:
                    mark_job_complete(
                        session,
                        job_id,
                        stage=(
                            "awaiting_confirmation"
                            if status == "awaiting_confirmation"
                            else "completed"
                        ),
                        status=(
                            "awaiting_confirmation"
                            if status == "awaiting_confirmation"
                            else "completed"
                        ),
                    )
                session.commit()
        except Exception as error:  # noqa: BLE001 - job boundary must persist failure.
            with session_scope(self.session_factory) as session:
                mark_job_failed(session, job_id, type(error).__name__)
                session.commit()
        return True

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            claimed = await self.run_once()
            if claimed:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.poll_interval_seconds
                )
            except TimeoutError:
                continue

    def request_stop(self) -> None:
        self._stop_event.set()
