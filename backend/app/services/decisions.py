from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import GuardedSession
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    Decision,
    Document,
    DocumentVersion,
    ProjectCompanyEvidence,
    Requirement,
    ReviewJob,
)
from app.settings import Settings

DECISION_TYPES = frozenset({"confirm", "deny", "not_applicable"})
MAX_EXPLANATION_LENGTH = 4000
MAX_ACTOR_LENGTH = 200


class DecisionScopeError(ValueError):
    code = "decision_scope_invalid"


class DecisionValidationError(ValueError):
    code = "decision_invalid"


class ActionItemScopeError(ValueError):
    code = "action_item_scope_invalid"


@dataclass(frozen=True, slots=True)
class ReReviewResult:
    job: ReviewJob
    affected_requirement_ids: tuple[int, ...]
    created: bool


def _clean_actor(actor: str) -> str:
    value = actor.strip()
    if not value or len(value) > MAX_ACTOR_LENGTH:
        raise DecisionValidationError("actor is required and bounded")
    return value


def _clean_explanation(explanation: str) -> str:
    value = explanation.strip()
    if not value or len(value) > MAX_EXPLANATION_LENGTH:
        raise DecisionValidationError("decision explanation is required and bounded")
    return value


def _current_assessment(
    session: GuardedSession,
    requirement_id: int,
) -> Assessment | None:
    return session.scalar(
        select(Assessment)
        .where(
            Assessment.requirement_id == requirement_id,
            Assessment.current.is_(True),
        )
        .order_by(Assessment.id.desc())
        .limit(1)
    )


def _assessment_version_ids(assessment: Assessment | None) -> list[int]:
    if assessment is None:
        return []
    return sorted(
        {
            int(link.document_version_id)
            for link in assessment.evidence_links
            if link.document_version_id is not None
        }
    )


def record_decision(
    session: GuardedSession,
    *,
    requirement_id: int,
    decision: str,
    explanation: str,
    actor: str,
) -> Decision:
    """Append an auditable human decision without rewriting past results."""

    if decision not in DECISION_TYPES:
        raise DecisionValidationError("unsupported decision type")
    clean_explanation = _clean_explanation(explanation)
    clean_actor = _clean_actor(actor)
    requirement = session.get(Requirement, requirement_id)
    if requirement is None or not requirement.active:
        raise DecisionScopeError("requirement is not an active project requirement")
    assessment = _current_assessment(session, requirement_id)
    version_ids = {int(requirement.source_version_id)}
    review_run_id = None
    assessment_id = None
    if assessment is not None:
        assessment_id = assessment.id
        review_run_id = assessment.review_run_id
        version_ids.update(_assessment_version_ids(assessment))
    result = Decision(
        requirement_id=requirement.id,
        decision=decision,
        explanation=clean_explanation,
        actor=clean_actor,
        review_run_id=review_run_id,
        assessment_id=assessment_id,
        version_ids=sorted(version_ids),
    )
    session.add(result)
    session.flush()
    session.add(
        AuditEvent(
            project_id=requirement.project_id,
            event_type="human_decision_recorded",
            payload={
                "decision_id": result.id,
                "requirement_id": requirement.id,
                "decision": decision,
                "actor": clean_actor,
                "review_run_id": review_run_id,
                "assessment_id": assessment_id,
                "version_ids": sorted(version_ids),
            },
        )
    )
    session.flush()
    return result


def complete_action_item(
    session: GuardedSession,
    *,
    action_item_id: int,
    actor: str,
) -> ActionItem:
    """Complete one open action item; completion never changes formal status."""

    clean_actor = _clean_actor(actor)
    action = session.get(ActionItem, action_item_id)
    if action is None or action.requirement is None:
        raise ActionItemScopeError("action item does not exist")
    if action.status != "open":
        raise ActionItemScopeError("action item is not open")
    action.status = "completed"
    action.completed_at = datetime.now(UTC)
    action.completed_by = clean_actor
    session.flush()
    session.add(
        AuditEvent(
            project_id=action.requirement.project_id,
            event_type="action_item_completed",
            payload={
                "action_item_id": action.id,
                "requirement_id": action.requirement_id,
                "actor": clean_actor,
            },
        )
    )
    session.flush()
    return action


def _validated_changed_versions(
    session: GuardedSession,
    *,
    project_id: int,
    changed_version_ids: Iterable[int],
) -> tuple[DocumentVersion, ...]:
    unique_ids = tuple(dict.fromkeys(int(item) for item in changed_version_ids))
    if not unique_ids:
        raise DecisionValidationError("at least one changed document version is required")
    versions = list(
        session.scalars(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(unique_ids))
            .order_by(DocumentVersion.id)
        )
    )
    if len(versions) != len(unique_ids):
        raise DecisionScopeError("changed document version does not exist")
    authorized_company_versions = set(
        session.scalars(
            select(DocumentVersion.id)
            .join(
                ProjectCompanyEvidence,
                ProjectCompanyEvidence.document_version_id == DocumentVersion.id,
            )
            .where(ProjectCompanyEvidence.project_id == project_id)
        )
    )
    for version in versions:
        document = session.get(Document, version.document_id)
        if document is None:
            raise DecisionScopeError("changed document version has no document")
        if document.role in {"tender", "proposal"}:
            if document.project_id != project_id:
                raise DecisionScopeError("changed version is outside project scope")
        elif document.role == "company":
            if version.id not in authorized_company_versions:
                raise DecisionScopeError("company version is not authorized for project")
        else:
            raise DecisionScopeError("unsupported changed document role")
    return tuple(versions)


def _related_version_ids(
    session: GuardedSession,
    versions: Iterable[DocumentVersion],
) -> set[int]:
    related: set[int] = set()
    for version in versions:
        document = session.get(Document, version.document_id)
        if document is None:
            continue
        related.add(version.id)
        if document.role in {"tender", "proposal", "company"}:
            related.update(
                session.scalars(
                    select(DocumentVersion.id).where(
                        DocumentVersion.document_id == document.id
                    )
                )
            )
    return related


def calculate_affected_requirement_ids(
    session: GuardedSession,
    *,
    project_id: int,
    changed_version_ids: Iterable[int],
) -> tuple[int, ...]:
    """Calculate the minimum deterministic re-review set for changed evidence."""

    versions = _validated_changed_versions(
        session,
        project_id=project_id,
        changed_version_ids=changed_version_ids,
    )
    related_ids = _related_version_ids(session, versions)
    requirements = list(
        session.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id, Requirement.active.is_(True))
            .order_by(Requirement.id)
        )
    )
    affected: set[int] = {
        requirement.id
        for requirement in requirements
        if requirement.source_version_id in related_ids
    }
    if requirements:
        assessments = list(
            session.scalars(
                select(Assessment)
                .where(
                    Assessment.requirement_id.in_(
                        requirement.id for requirement in requirements
                    ),
                    Assessment.current.is_(True),
                )
            )
        )
        for assessment in assessments:
            if any(link.document_version_id in related_ids for link in assessment.evidence_links):
                affected.add(assessment.requirement_id)
    return tuple(sorted(affected))


def invalidate_assessments(
    session: GuardedSession,
    *,
    project_id: int,
    requirement_ids: Iterable[int],
    changed_version_ids: Iterable[int],
    reason: str,
) -> None:
    ids = tuple(dict.fromkeys(int(item) for item in requirement_ids))
    if not ids:
        return
    versions = _validated_changed_versions(
        session,
        project_id=project_id,
        changed_version_ids=changed_version_ids,
    )
    related_ids = _related_version_ids(session, versions)
    assessments = list(
        session.scalars(
            select(Assessment).where(
                Assessment.requirement_id.in_(ids),
                Assessment.current.is_(True),
            )
        )
    )
    for assessment in assessments:
        assessment.current = False
        for link in assessment.evidence_links:
            if link.document_version_id in related_ids:
                link.valid = False
        requirement = session.get(Requirement, assessment.requirement_id)
        if requirement is not None and requirement.project_id == project_id:
            session.add(
                AuditEvent(
                    project_id=project_id,
                    event_type="assessment_invalidated",
                    payload={
                        "assessment_id": assessment.id,
                        "requirement_id": assessment.requirement_id,
                        "changed_version_ids": sorted(related_ids),
                        "reason": reason,
                    },
                )
            )
    session.flush()


def deactivate_tender_matrix(
    session: GuardedSession,
    *,
    project_id: int,
    tender_version_id: int,
) -> tuple[int, ...]:
    tender = session.get(DocumentVersion, tender_version_id)
    if tender is None:
        raise DecisionScopeError("tender version does not exist")
    document = session.get(Document, tender.document_id)
    if document is None or document.project_id != project_id or document.role != "tender":
        raise DecisionScopeError("tender version is outside project scope")
    requirements = list(
        session.scalars(
            select(Requirement).where(
                Requirement.project_id == project_id,
                Requirement.active.is_(True),
            )
        )
    )
    ids = tuple(requirement.id for requirement in requirements)
    for requirement in requirements:
        requirement.active = False
    if ids:
        assessments = list(
            session.scalars(
                select(Assessment).where(
                    Assessment.requirement_id.in_(ids),
                    Assessment.current.is_(True),
                )
            )
        )
        for assessment in assessments:
            assessment.current = False
            session.add(
                AuditEvent(
                    project_id=project_id,
                    event_type="tender_matrix_invalidated",
                    payload={
                        "assessment_id": assessment.id,
                        "requirement_id": assessment.requirement_id,
                        "tender_version_id": tender_version_id,
                    },
                )
            )
    session.flush()
    return ids


def start_incremental_review(
    session: GuardedSession,
    *,
    project_id: int,
    changed_version_ids: Iterable[int],
    actor: str,
    tender_version_id: int | None = None,
    settings: Settings | None = None,
) -> ReReviewResult:
    """Invalidate only affected results, then enqueue a bounded re-review."""

    clean_actor = _clean_actor(actor)
    from app.jobs.handlers import build_review_scope, find_active_job, start_review

    changed_ids = tuple(dict.fromkeys(int(item) for item in changed_version_ids))
    if tender_version_id is not None and tender_version_id not in changed_ids:
        changed_ids = (*changed_ids, tender_version_id)
    if not changed_ids:
        raise DecisionValidationError("at least one changed document version is required")
    versions = _validated_changed_versions(
        session,
        project_id=project_id,
        changed_version_ids=changed_ids,
    )
    tender_versions = [
        version
        for version in versions
        if (document := session.get(Document, version.document_id)) is not None
        and document.role == "tender"
    ]
    selected_tender_version_id = tender_version_id or (
        tender_versions[-1].id if tender_versions else None
    )
    scope = build_review_scope(
        session,
        project_id=project_id,
        tender_version_id=selected_tender_version_id,
    )
    existing = find_active_job(
        session,
        project_id=project_id,
        version_fingerprint=scope.fingerprint,
    )
    if existing is not None:
        return ReReviewResult(existing, (), False)

    if selected_tender_version_id is not None and tender_versions:
        affected_ids = deactivate_tender_matrix(
            session,
            project_id=project_id,
            tender_version_id=selected_tender_version_id,
        )
        run_scope_ids: list[int] | None = None
        reason = "tender version changed; requirement matrix replaced"
    else:
        affected_ids = calculate_affected_requirement_ids(
            session,
            project_id=project_id,
            changed_version_ids=changed_ids,
        )
        invalidate_assessments(
            session,
            project_id=project_id,
            requirement_ids=affected_ids,
            changed_version_ids=changed_ids,
            reason="proposal or company evidence version changed",
        )
        run_scope_ids = list(affected_ids)
        reason = "proposal or company evidence version changed"

    job, created = start_review(
        session,
        project_id=project_id,
        tender_version_id=selected_tender_version_id,
        settings=settings,
        affected_requirement_ids=run_scope_ids,
    )
    session.add(
        AuditEvent(
            project_id=project_id,
            event_type="incremental_review_started",
            payload={
                "review_run_id": job.review_run_id,
                "job_id": job.id,
                "actor": clean_actor,
                "changed_version_ids": list(changed_ids),
                "affected_requirement_ids": list(affected_ids),
                "reason": reason,
            },
        )
    )
    session.commit()
    return ReReviewResult(job, tuple(affected_ids), created)
