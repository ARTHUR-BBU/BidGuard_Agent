from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Runner
from sqlalchemy import select

from app.agents.context import build_review_context
from app.agents.contracts import AgentRuntimeLimits
from app.agents.extraction import run_extraction_batch
from app.db import GuardedSession
from app.persistence.models import (
    BidProject,
    Document,
    DocumentChunk,
    DocumentVersion,
    ProjectCompanyEvidence,
    Requirement,
    ReviewJob,
    ReviewRun,
)
from app.services.ingestion import parse_document_version
from app.services.requirements import save_requirement_batch
from app.services.reviews import ReviewResult, run_review
from app.settings import Settings, get_settings

from .worker import (
    enqueue_job,
    find_active_job,
    session_scope,
)


@dataclass(frozen=True, slots=True)
class ReviewScope:
    project_id: int
    tender_version_id: int
    version_ids: tuple[int, ...]
    fingerprint: str


def _latest_version(
    session: GuardedSession,
    *,
    project_id: int,
    role: str,
) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.project_id == project_id, Document.role == role)
        .order_by(DocumentVersion.version_number.desc(), DocumentVersion.id.desc())
        .limit(1)
    )


def build_review_scope(
    session: GuardedSession,
    *,
    project_id: int,
    tender_version_id: int | None = None,
) -> ReviewScope:
    project = session.get(BidProject, project_id)
    if project is None:
        raise ValueError("project does not exist")
    tender = (
        session.get(DocumentVersion, tender_version_id)
        if tender_version_id is not None
        else _latest_version(session, project_id=project_id, role="tender")
    )
    if tender is None:
        raise ValueError("project has no tender document version")
    tender_document = session.get(Document, tender.document_id)
    if (
        tender_document is None
        or tender_document.project_id != project_id
        or tender_document.role != "tender"
    ):
        raise ValueError("tender version is outside project scope")

    version_ids = {tender.id}
    proposal = _latest_version(session, project_id=project_id, role="proposal")
    if proposal is not None:
        version_ids.add(proposal.id)
    version_ids.update(
        session.scalars(
            select(ProjectCompanyEvidence.document_version_id).where(
                ProjectCompanyEvidence.project_id == project_id,
                ProjectCompanyEvidence.active.is_(True),
            )
        )
    )
    ordered_ids = tuple(sorted(version_ids))
    fingerprint = hashlib.sha256(
        ",".join(str(version_id) for version_id in ordered_ids).encode("ascii")
    ).hexdigest()
    return ReviewScope(project_id, tender.id, ordered_ids, fingerprint)


def start_review(
    session: GuardedSession,
    *,
    project_id: int,
    tender_version_id: int | None = None,
    settings: Settings | None = None,
    affected_requirement_ids: list[int] | None = None,
) -> tuple[ReviewJob, bool]:
    """Create one idempotent queued review job for the current file scope."""

    configured = settings or get_settings()
    scope = build_review_scope(
        session,
        project_id=project_id,
        tender_version_id=tender_version_id,
    )
    existing = find_active_job(
        session,
        project_id=project_id,
        version_fingerprint=scope.fingerprint,
    )
    if existing is not None:
        return existing, False
    model_name = (
        configured.review_model.strip()
        or configured.easyrouter_review_model.strip()
        or "unconfigured"
    )
    run = ReviewRun(
        project_id=project_id,
        status="queued",
        stage="created",
        model_provider=configured.model_provider,
        model_name=model_name,
        affected_requirement_ids=(
            sorted(set(affected_requirement_ids))
            if affected_requirement_ids is not None
            else None
        ),
    )
    session.add(run)
    session.flush()
    job = enqueue_job(
        session,
        project_id=project_id,
        review_run_id=run.id,
        tender_version_id=scope.tender_version_id,
        version_fingerprint=scope.fingerprint,
        version_ids=list(scope.version_ids),
    )
    session.commit()
    return job, True


def _set_stage(
    session_factory: Callable[..., Any],
    job_id: int,
    stage: str,
) -> ReviewJob:
    with session_scope(session_factory) as session:
        job = session.get(ReviewJob, job_id)
        if job is None:
            raise ValueError("review job does not exist")
        job.stage = stage
        session.commit()
        return job


def _parse_scope_versions(
    session_factory: Callable[..., Any],
    *,
    job: ReviewJob,
    storage_root: Path,
) -> None:
    with session_scope(session_factory) as session:
        raw_version_ids: list[int] = list(job.version_ids or [])
        if not raw_version_ids and job.tender_version_id is not None:
            raw_version_ids.append(job.tender_version_id)
        version_ids = [int(version_id) for version_id in raw_version_ids]
        for version_id in version_ids:
            if version_id is None:
                continue
            version = session.get(DocumentVersion, version_id)
            if version is None:
                raise ValueError("review job references an unknown document version")
            if version.parse_status in {"pending", "parsing"}:
                parse_document_version(session, version.id, storage_root)
            if version.parse_status not in {"parsed", "partial_failure"}:
                raise ValueError("review job contains an unavailable document version")


async def _extract_if_stale(
    session_factory: Callable[..., Any],
    *,
    job: ReviewJob,
    settings: Settings,
    limits: AgentRuntimeLimits,
    runner: Any,
) -> None:
    with session_scope(session_factory) as session:
        if job.review_run_id is None or job.tender_version_id is None:
            raise ValueError("review job is missing its review run scope")
        requirement_exists = session.scalar(
            select(Requirement.id).where(
                Requirement.project_id == job.project_id,
                Requirement.source_version_id == job.tender_version_id,
                Requirement.active.is_(True),
            )
        )
        if requirement_exists is not None:
            return
        context = build_review_context(
            session,
            review_run_id=job.review_run_id,
            tender_version_id=job.tender_version_id,
            limits=limits,
        )
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_version_id == job.tender_version_id
                )
                .order_by(DocumentChunk.chunk_index)
            )
        )
        if not chunks:
            raise ValueError("tender version has no parsed chunks for extraction")
        context = context.model_copy(
            update={
                "allowed_chunk_ids": tuple(chunk.id for chunk in chunks),
                "coverage": context.coverage.model_copy(
                    update={"visible_chunk_ids": tuple(chunk.id for chunk in chunks)}
                ),
            }
        )
        excerpt = " ".join(" ".join(chunk.text.split()) for chunk in chunks)
        batch = await run_extraction_batch(
            excerpt,
            context=context,
            settings=settings,
            limits=limits,
            source_chunk_ids=[chunk.id for chunk in chunks],
            session=session,
            runner=runner,
        )
        save_requirement_batch(
            session,
            project_id=job.project_id,
            active_version_id=job.tender_version_id,
            candidates=batch.requirements,
            chunks=chunks,
        )
        session.commit()


async def process_review_job(
    job_id: int,
    session_factory: Callable[..., Any],
    *,
    settings: Settings | None = None,
    runner: Any = Runner,
) -> ReviewResult:
    """Execute parse → extract-if-stale → review for one claimed job."""

    configured = settings or get_settings()
    limits = AgentRuntimeLimits(
        max_turns=configured.max_agent_turns,
        max_tool_calls=configured.max_tool_calls,
    )
    with session_scope(session_factory) as session:
        job = session.get(ReviewJob, job_id)
        if job is None:
            raise ValueError("review job does not exist")
        raw_version_ids: list[int] = list(job.version_ids or [])
        if not raw_version_ids and job.tender_version_id is not None:
            raw_version_ids.append(job.tender_version_id)
        version_ids = [int(version_id) for version_id in raw_version_ids]
        job_snapshot = ReviewJob(
            id=job.id,
            project_id=job.project_id,
            review_run_id=job.review_run_id,
            tender_version_id=job.tender_version_id,
            version_ids=version_ids,
            version_fingerprint=job.version_fingerprint,
            status=job.status,
            stage=job.stage,
        )
        review_run = (
            session.get(ReviewRun, job.review_run_id)
            if job.review_run_id is not None
            else None
        )
        affected_requirement_ids = (
            list(review_run.affected_requirement_ids or [])
            if review_run is not None and review_run.affected_requirement_ids is not None
            else None
        )
        session.rollback()
    _set_stage(session_factory, job_id, "parsing")
    _parse_scope_versions(
        session_factory,
        job=job_snapshot,
        storage_root=configured.storage_root,
    )
    _set_stage(session_factory, job_id, "extracting")
    await _extract_if_stale(
        session_factory,
        job=job_snapshot,
        settings=configured,
        limits=limits,
        runner=runner,
    )
    _set_stage(session_factory, job_id, "reviewing")
    if job_snapshot.review_run_id is None or job_snapshot.tender_version_id is None:
        raise ValueError("review job is missing its review run scope")
    result = await run_review(
        session_factory,
        review_run_id=job_snapshot.review_run_id,
        tender_version_id=job_snapshot.tender_version_id,
        settings=configured,
        limits=limits,
        runner=runner,
        requirement_ids=affected_requirement_ids,
    )
    _set_stage(
        session_factory,
        job_id,
        "awaiting_confirmation" if result.status == "awaiting_confirmation" else result.status,
    )
    return result
