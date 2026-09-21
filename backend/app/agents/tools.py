from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from agents import function_tool
from sqlalchemy import select

from app.agents.context import ReviewContext
from app.agents.gates import (
    StaleEvidenceError,
    validate_assessment,
)
from app.db import GuardedSession
from app.documents.search import (
    SearchResult,
    _coverage_complete,
    overlap_score,
    search_chunks,
)
from app.domain.schemas import AssessmentCandidate, SourceCitation
from app.domain.status_rules import calculate_display_status
from app.persistence.models import (
    ActionItem,
    Assessment,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceLink,
    ProjectCompanyEvidence,
    Requirement,
    ReviewRun,
)

_MAX_TOOL_QUOTE = 2000
_MAX_SEARCH_LIMIT = 100


class ToolScopeError(ValueError):
    code = "tool_scope_invalid"


class ToolValidationError(ValueError):
    code = "tool_input_invalid"


def _normalise(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _compact_result(result: SearchResult) -> dict[str, object]:
    return {
        "chunk_id": result.chunk_id,
        "document_version_id": result.document_version_id,
        "document_id": result.document_id,
        "version_number": result.version_number,
        "document_role": result.document_role,
        "page_number": result.page_number,
        "section_path": result.section_path,
        "quote": result.text[:_MAX_TOOL_QUOTE],
        "score": result.score,
        "coverage_complete": result.coverage_complete,
    }


@dataclass(slots=True)
class ReviewToolbox:
    """Server-bound read and candidate-write tools for one ReviewContext."""

    session: GuardedSession
    context: ReviewContext

    @property
    def _allowed_versions(self) -> set[int]:
        return set(self.context.allowed_document_version_ids) | set(
            self.context.allowed_company_document_version_ids
        )

    def _require_project(self, project_id: int) -> None:
        if project_id != self.context.project_id:
            raise ToolScopeError("project scope does not match review context")

    def _require_requirement(self, requirement_id: int) -> Requirement:
        requirement = self.session.get(Requirement, requirement_id)
        if (
            requirement is None
            or requirement.project_id != self.context.project_id
            or not requirement.active
        ):
            raise ToolScopeError("requirement is outside active project scope")
        review_run = self.session.get(ReviewRun, self.context.review_run_id)
        if review_run is None or review_run.project_id != self.context.project_id:
            raise ToolScopeError("review run is outside project scope")
        return requirement

    def _require_version(self, document_version_id: int) -> tuple[DocumentVersion, Document]:
        if document_version_id not in self._allowed_versions:
            raise ToolScopeError("document version is outside review scope")
        row = self.session.execute(
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == document_version_id)
        ).first()
        if row is None:
            raise ToolScopeError("document version does not exist")
        version, document = row
        assert isinstance(version, DocumentVersion)
        assert isinstance(document, Document)
        if document.role == "company":
            authorized = self.session.scalar(
                select(ProjectCompanyEvidence.id).where(
                    ProjectCompanyEvidence.project_id == self.context.project_id,
                    ProjectCompanyEvidence.document_version_id == document_version_id,
                    ProjectCompanyEvidence.active.is_(True),
                )
            )
            if authorized is None:
                raise ToolScopeError("company evidence is not authorized for project")
        elif document.project_id != self.context.project_id:
            raise ToolScopeError("document version belongs to another project")
        return version, document

    def get_document_page(
        self,
        document_version_id: int,
        page_number: int,
    ) -> list[dict[str, object]]:
        """Return only compact chunks from one authorized document page."""

        if page_number < 1:
            raise ToolValidationError("page number must be positive")
        self._require_version(document_version_id)
        chunks = self.session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_version_id == document_version_id,
                DocumentChunk.page_number == page_number,
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(20)
        )
        return [
            {
                "chunk_id": chunk.id,
                "document_version_id": chunk.document_version_id,
                "page_number": chunk.page_number,
                "section_path": chunk.section_path,
                "quote": chunk.text[:_MAX_TOOL_QUOTE],
            }
            for chunk in chunks
        ]

    def _proposal_versions(self) -> tuple[int, ...]:
        allowed = set(self.context.allowed_document_version_ids)
        if not allowed:
            return ()
        return tuple(
            self.session.scalars(
                select(DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.id.in_(allowed),
                    Document.project_id == self.context.project_id,
                    Document.role == "proposal",
                )
                .order_by(DocumentVersion.id)
            )
        )

    def search_proposal_evidence(
        self,
        project_id: int,
        query: str,
        limit: int,
    ) -> list[dict[str, object]]:
        """Search only proposal versions already authorized in the context."""

        self._require_project(project_id)
        version_ids = self._proposal_versions()
        if not version_ids:
            return []
        return [
            _compact_result(result)
            for result in search_chunks(
                self.session,
                query,
                project_id=project_id,
                allowed_document_version_ids=version_ids,
                document_roles=("proposal",),
                limit=limit,
            )
        ]

    def search_company_evidence(
        self,
        project_id: int,
        query: str,
        limit: int,
    ) -> list[dict[str, object]]:
        """Search only active company versions explicitly selected for project."""

        self._require_project(project_id)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_SEARCH_LIMIT
        ):
            raise ToolValidationError("limit must be between 1 and 100")
        version_ids = set(self.context.allowed_company_document_version_ids)
        if not version_ids or not query.strip():
            return []
        rows = self.session.execute(
            select(
                DocumentChunk,
                DocumentVersion,
                Document,
            )
            .join(
                DocumentVersion,
                DocumentVersion.id == DocumentChunk.document_version_id,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(
                ProjectCompanyEvidence,
                ProjectCompanyEvidence.document_version_id == DocumentVersion.id,
            )
            .where(
                ProjectCompanyEvidence.project_id == project_id,
                ProjectCompanyEvidence.active.is_(True),
                DocumentVersion.id.in_(version_ids),
                Document.role == "company",
                Document.project_id.is_(None),
                DocumentVersion.parse_status.in_(("parsed", "partial_failure")),
            )
            .order_by(DocumentChunk.id)
        ).all()
        ranked: list[tuple[float, int, dict[str, object]]] = []
        for chunk, version, document in rows:
            score = overlap_score(query, chunk.text)
            if score <= 0:
                continue
            ranked.append(
                (
                    score,
                    chunk.id,
                    {
                        "chunk_id": chunk.id,
                        "document_version_id": version.id,
                        "document_id": document.id,
                        "version_number": version.version_number,
                        "document_role": document.role,
                        "page_number": chunk.page_number,
                        "section_path": chunk.section_path,
                        "quote": chunk.text[:_MAX_TOOL_QUOTE],
                        "score": score,
                        "coverage_complete": _coverage_complete(
                            version.parse_status, version.parse_coverage
                        ),
                    },
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[:limit]]

    def _validate_evidence_citation(self, citation: SourceCitation) -> None:
        if citation.document_version_id not in self._allowed_versions:
            raise StaleEvidenceError("assessment cites an inactive document version")
        version, document = self._require_version(citation.document_version_id)
        if document.role not in {"proposal", "company"}:
            raise ToolValidationError("assessment evidence must come from proposal or company documents")
        if citation.page_number is None and citation.section_path is None:
            raise ToolValidationError("assessment evidence requires a page or section")
        statement = select(DocumentChunk).where(
            DocumentChunk.document_version_id == version.id
        )
        if citation.page_number is not None:
            statement = statement.where(DocumentChunk.page_number == citation.page_number)
        if citation.section_path is not None:
            statement = statement.where(DocumentChunk.section_path == citation.section_path)
        chunks = list(self.session.scalars(statement))
        if not chunks or not any(
            _normalise(citation.quote) in _normalise(chunk.text) for chunk in chunks
        ):
            raise ToolValidationError("assessment evidence quote was not found in the cited chunk")

    def save_assessment(
        self,
        requirement_id: int,
        candidate: AssessmentCandidate | dict[str, Any],
    ) -> Assessment:
        """Persist a checked candidate assessment and its evidence links."""

        requirement = self._require_requirement(requirement_id)
        candidate_model = (
            candidate
            if isinstance(candidate, AssessmentCandidate)
            else AssessmentCandidate.model_validate(candidate)
        )
        if candidate_model.requirement_id != requirement_id:
            raise ToolValidationError("candidate requirement does not match tool argument")
        validate_assessment(candidate_model, self._allowed_versions)
        for citation in candidate_model.evidence:
            self._validate_evidence_citation(citation)
        display_status = calculate_display_status(
            requirement.mandatory,
            candidate_model.evidence_state,
            candidate_model.severity,
            candidate_model.needs_confirmation,
        )
        assessment = self.session.scalar(
            select(Assessment).where(
                Assessment.requirement_id == requirement_id,
                Assessment.review_run_id == self.context.review_run_id,
            )
        )
        if assessment is None:
            assessment = Assessment(
                requirement_id=requirement_id,
                review_run_id=self.context.review_run_id,
                evidence_state=candidate_model.evidence_state,
                severity=candidate_model.severity,
                display_status=display_status,
                needs_confirmation=candidate_model.needs_confirmation,
                reasoning=candidate_model.reasoning,
                recommendation=candidate_model.recommendation,
            )
            self.session.add(assessment)
            self.session.flush()
        else:
            for link in assessment.evidence_links:
                link.valid = False
            assessment.evidence_state = candidate_model.evidence_state
            assessment.severity = candidate_model.severity
            assessment.display_status = display_status
            assessment.needs_confirmation = candidate_model.needs_confirmation
            assessment.reasoning = candidate_model.reasoning
            assessment.recommendation = candidate_model.recommendation
        for citation in candidate_model.evidence:
            self.session.add(
                EvidenceLink(
                    assessment_id=assessment.id,
                    document_version_id=citation.document_version_id,
                    page_number=citation.page_number,
                    section_path=citation.section_path,
                    quote=citation.quote,
                    purpose="assessment",
                    valid=True,
                )
            )
        self.session.flush()
        return assessment

    def request_user_confirmation(
        self,
        requirement_id: int,
        question: str,
        reason: str,
    ) -> dict[str, object]:
        """Create an auditable, non-authoritative confirmation request."""

        self._require_requirement(requirement_id)
        question = question.strip()
        reason = reason.strip()
        if not question or not reason or len(question) > 2000 or len(reason) > 2000:
            raise ToolValidationError("confirmation question and reason are required and bounded")
        event = AuditEvent(
            project_id=self.context.project_id,
            event_type="user_confirmation_requested",
            payload={
                "review_run_id": self.context.review_run_id,
                "requirement_id": requirement_id,
                "question": question,
                "reason": reason,
            },
        )
        self.session.add(event)
        self.session.flush()
        return {
            "status": "awaiting_confirmation",
            "requirement_id": requirement_id,
            "audit_event_id": event.id,
        }

    def create_action_item(
        self,
        requirement_id: int,
        title: str,
        recommendation: str,
    ) -> dict[str, object]:
        """Create a bounded open action item without changing formal status."""

        self._require_requirement(requirement_id)
        title = title.strip()
        recommendation = recommendation.strip()
        if not title or not recommendation or len(title) > 500 or len(recommendation) > 4000:
            raise ToolValidationError("action title and recommendation are required and bounded")
        action = ActionItem(
            requirement_id=requirement_id,
            description=f"{title}\n\n{recommendation}",
            status="open",
        )
        self.session.add(action)
        self.session.flush()
        self.session.add(
            AuditEvent(
                project_id=self.context.project_id,
                event_type="action_item_created",
                payload={
                    "review_run_id": self.context.review_run_id,
                    "requirement_id": requirement_id,
                    "action_item_id": action.id,
                },
            )
        )
        self.session.flush()
        return {
            "status": "open",
            "requirement_id": requirement_id,
            "action_item_id": action.id,
        }


def build_review_tools(
    session: GuardedSession,
    context: ReviewContext,
) -> list[Any]:
    """Build six SDK tools closed over one server-owned ReviewContext."""

    toolbox = ReviewToolbox(session, context)

    @function_tool
    def get_document_page(document_version_id: int, page_number: int) -> list[dict[str, object]]:
        """Read compact evidence chunks from one authorized document page."""

        return toolbox.get_document_page(document_version_id, page_number)

    @function_tool
    def search_proposal_evidence(project_id: int, query: str, limit: int = 10) -> list[dict[str, object]]:
        """Search authorized proposal evidence for the current project."""

        return toolbox.search_proposal_evidence(project_id, query, limit)

    @function_tool
    def search_company_evidence(project_id: int, query: str, limit: int = 10) -> list[dict[str, object]]:
        """Search company evidence explicitly selected for the current project."""

        return toolbox.search_company_evidence(project_id, query, limit)

    @function_tool
    def save_assessment(
        requirement_id: int,
        candidate: AssessmentCandidate,
    ) -> dict[str, object]:
        """Save a candidate assessment only after evidence and status gates."""

        assessment = toolbox.save_assessment(requirement_id, candidate)
        return {
            "assessment_id": assessment.id,
            "requirement_id": assessment.requirement_id,
            "display_status": assessment.display_status,
        }

    @function_tool
    def request_user_confirmation(
        requirement_id: int,
        question: str,
        reason: str,
    ) -> dict[str, object]:
        """Create an auditable request for a human fact confirmation."""

        return toolbox.request_user_confirmation(requirement_id, question, reason)

    @function_tool
    def create_action_item(
        requirement_id: int,
        title: str,
        recommendation: str,
    ) -> dict[str, object]:
        """Create an open action item without changing formal status."""

        return toolbox.create_action_item(requirement_id, title, recommendation)

    return [
        get_document_page,
        search_proposal_evidence,
        search_company_evidence,
        save_assessment,
        request_user_confirmation,
        create_action_item,
    ]


build_evidence_tools = build_review_tools
