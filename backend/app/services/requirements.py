from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.agents.gates import (
    CitationGateError,
    requirement_fingerprint,
    validate_requirement_candidate,
)
from app.db import GuardedSession
from app.domain.enums import RequirementKind
from app.domain.schemas import RequirementCandidate, SourceCitation
from app.persistence.models import AuditEvent, Document, DocumentVersion, Requirement


class RequirementScopeError(ValueError):
    code = "requirement_scope_invalid"


@dataclass(frozen=True, slots=True)
class RequirementSaveResult:
    created: tuple[Requirement, ...]
    rejected: tuple[tuple[RequirementCandidate, str], ...]
    duplicates: tuple[str, ...]


def _audit_rejection(
    session: GuardedSession,
    *,
    project_id: int,
    candidate: RequirementCandidate,
    code: str,
) -> None:
    session.add(
        AuditEvent(
            project_id=project_id,
            event_type="requirement_candidate_rejected",
            payload={
                "code": code,
                "candidate_text_hash": hashlib.sha256(
                    candidate.text.encode("utf-8")
                ).hexdigest(),
                "document_version_id": candidate.citation.document_version_id,
            },
        )
    )


def save_requirement_batch(
    session: GuardedSession,
    *,
    project_id: int,
    active_version_id: int,
    candidates: Iterable[RequirementCandidate],
    chunks: Sequence[object],
) -> RequirementSaveResult:
    """Validate and persist only cited requirements; retain history."""

    active_version = session.get(DocumentVersion, active_version_id)
    if active_version is None:
        raise RequirementScopeError("active tender version does not exist")
    active_document = session.get(Document, active_version.document_id)
    if (
        active_document is None
        or active_document.project_id != project_id
        or active_document.role != "tender"
    ):
        raise RequirementScopeError("active version is outside the tender project")
    chunk_objects = list(chunks)
    for chunk in chunk_objects:
        chunk_version_id = getattr(chunk, "document_version_id", None)
        if chunk_version_id is not None and chunk_version_id != active_version_id:
            raise RequirementScopeError("requirement chunk is outside active version")

    existing_fingerprints: set[str] = set()
    for requirement in session.scalars(
        select(Requirement).where(Requirement.project_id == project_id)
    ):
        historical_candidate = RequirementCandidate(
            text=requirement.text,
            kind=RequirementKind(requirement.kind),
            mandatory=requirement.mandatory,
            citation=SourceCitation(
                document_version_id=requirement.source_version_id,
                page_number=requirement.source_page,
                section_path=requirement.source_section,
                quote=requirement.source_quote,
            ),
        )
        existing_fingerprints.add(
            requirement.citation_fingerprint
            or requirement_fingerprint(historical_candidate)
        )
    seen: set[str] = set()
    created: list[Requirement] = []
    valid_candidates = False
    rejected: list[tuple[RequirementCandidate, str]] = []
    duplicates: list[str] = []
    for candidate in candidates:
        try:
            validated = validate_requirement_candidate(
                candidate,
                active_version=active_version_id,
                chunks=chunk_objects,
            )
        except CitationGateError as error:
            rejected.append((candidate, error.code))
            _audit_rejection(
                session,
                project_id=project_id,
                candidate=candidate,
                code=error.code,
            )
            continue
        valid_candidates = True
        if validated.fingerprint in seen or validated.fingerprint in existing_fingerprints:
            duplicates.append(validated.fingerprint)
            continue
        seen.add(validated.fingerprint)
        created.append(
            Requirement(
                project_id=project_id,
                source_version_id=active_version_id,
                source_page=candidate.citation.page_number,
                source_section=candidate.citation.section_path,
                source_quote=candidate.citation.quote,
                text=candidate.text,
                kind=candidate.kind,
                mandatory=candidate.mandatory,
                citation_fingerprint=validated.fingerprint,
            )
        )
    if valid_candidates:
        old_requirements = session.scalars(
            select(Requirement).where(
                Requirement.project_id == project_id,
                Requirement.source_version_id != active_version_id,
                Requirement.active.is_(True),
            )
        )
        for requirement in old_requirements:
            requirement.active = False
        session.add_all(created)
        session.flush()
    return RequirementSaveResult(
        created=tuple(created),
        rejected=tuple(rejected),
        duplicates=tuple(duplicates),
    )


save_requirements = save_requirement_batch
