from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import (
    AgentRuntimeLimits,
    Coverage,
    CoverageState,
    ReasonCode,
)
from app.persistence.models import (
    BidProject,
    Document,
    DocumentVersion,
    ProjectCompanyEvidence,
    ReviewRun,
    TenderPackageMember,
)


class ContextScopeError(ValueError):
    code = ReasonCode.REJECTED_SCOPE


class ContextConflictError(ValueError):
    code = ReasonCode.CONFLICT_UNRESOLVED


class ReviewContext(BaseModel):
    """Immutable server-created authorization context for one bounded step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: int = Field(gt=0)
    review_run_id: int = Field(gt=0)
    tender_version_id: int = Field(gt=0)
    allowed_document_version_ids: tuple[int, ...] = ()
    allowed_company_document_version_ids: tuple[int, ...] = ()
    allowed_chunk_ids: tuple[int, ...] = ()
    coverage: Coverage
    limits: AgentRuntimeLimits = AgentRuntimeLimits()

    @model_validator(mode="after")
    def validate_scope(self) -> ReviewContext:
        allowed = set(self.allowed_document_version_ids)
        if self.tender_version_id not in allowed:
            raise ValueError("tender version must be inside allowed document scope")
        if set(self.coverage.document_version_ids) - allowed:
            raise ValueError("coverage exceeds allowed document version scope")
        if set(self.coverage.visible_chunk_ids) - set(self.allowed_chunk_ids):
            raise ValueError("coverage exceeds allowed chunk scope")
        if set(self.allowed_company_document_version_ids) & allowed:
            raise ValueError("company evidence scope must be separate from tender scope")
        return self

    def narrow(
        self,
        *,
        project_id: int | None = None,
        document_version_ids: set[int] | None = None,
        company_document_version_ids: set[int] | None = None,
        chunk_ids: set[int] | None = None,
    ) -> ReviewContext:
        """Narrow scope for a model request; widening raises immediately."""

        if project_id is not None and project_id != self.project_id:
            raise ContextScopeError("model attempted to change project scope")
        requested_documents = set(
            self.allowed_document_version_ids
            if document_version_ids is None
            else document_version_ids
        )
        requested_company = set(
            self.allowed_company_document_version_ids
            if company_document_version_ids is None
            else company_document_version_ids
        )
        requested_chunks = set(
            self.allowed_chunk_ids if chunk_ids is None else chunk_ids
        )
        if not requested_documents.issubset(self.allowed_document_version_ids):
            raise ContextScopeError("model attempted to widen document version scope")
        if not requested_company.issubset(
            self.allowed_company_document_version_ids
        ):
            raise ContextScopeError("model attempted to widen company evidence scope")
        if not requested_chunks.issubset(self.allowed_chunk_ids):
            raise ContextScopeError("model attempted to widen chunk scope")
        coverage = self.coverage.model_copy(
            update={
                "document_version_ids": tuple(sorted(
                    set(self.coverage.document_version_ids) & requested_documents
                )),
                "visible_chunk_ids": tuple(sorted(
                    set(self.coverage.visible_chunk_ids) & requested_chunks
                )),
            }
        )
        return self.model_copy(
            update={
                "allowed_document_version_ids": tuple(sorted(requested_documents)),
                "allowed_company_document_version_ids": tuple(sorted(requested_company)),
                "allowed_chunk_ids": tuple(sorted(requested_chunks)),
                "coverage": coverage,
            }
        )


def build_review_context(
    session: Session,
    *,
    review_run_id: int,
    tender_version_id: int,
    limits: AgentRuntimeLimits,
) -> ReviewContext:
    review_run = session.get(ReviewRun, review_run_id)
    tender_version = session.get(DocumentVersion, tender_version_id)
    if review_run is None or tender_version is None:
        raise ContextScopeError("review run or tender version does not exist")
    tender_document = session.get(Document, tender_version.document_id)
    if tender_document is None or tender_document.project_id != review_run.project_id:
        raise ContextScopeError("tender version does not belong to review project")
    project = session.get(BidProject, review_run.project_id)
    if project is None:
        raise ContextScopeError("review project does not exist")

    package_members = list(
        session.scalars(
            select(TenderPackageMember)
            .where(
                TenderPackageMember.project_id == review_run.project_id,
            )
            .order_by(TenderPackageMember.id)
        )
    )
    package_version_ids = {
        member.document_version_id for member in package_members
    }
    package_version_ids.add(tender_version_id)
    package_versions = list(
        session.execute(
            select(DocumentVersion.id, Document.id, Document.project_id, Document.role)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(package_version_ids))
        )
    ) if package_version_ids else []
    version_rows = {int(row[0]): row for row in package_versions}
    if len(version_rows) != len(package_version_ids):
        raise ContextScopeError("tender package references an unknown document version")
    if any(
        row[2] != review_run.project_id or str(row[3]) != "tender"
        for row in package_versions
    ):
        raise ContextScopeError("tender package contains a cross-project or non-tender version")
    if any(
        member.conflict_state == "unresolved"
        for member in package_members
    ):
        raise ContextConflictError("tender package contains unresolved conflict")
    included_members = [member for member in package_members if member.included]
    included_versions = {member.document_version_id for member in included_members}
    included_versions.add(tender_version_id)
    excluded_document_ids = {
        int(version_rows[member.document_version_id][1])
        for member in package_members
        if not member.included
    }

    authorizations = list(
        session.scalars(
            select(ProjectCompanyEvidence)
            .where(
                ProjectCompanyEvidence.project_id == review_run.project_id,
                ProjectCompanyEvidence.active.is_(True),
            )
            .order_by(ProjectCompanyEvidence.id)
        )
    )
    company_versions = {
        authorization.document_version_id for authorization in authorizations
    }
    if company_versions:
        actual_company_versions = set(
            session.scalars(
                select(DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.id.in_(company_versions),
                    Document.role == "company",
                    Document.project_id.is_(None),
                )
            )
        )
        if actual_company_versions != company_versions:
            raise ContextScopeError("company authorization points outside company documents")

    # The initial context deliberately exposes version IDs only. Chunk IDs are
    # added by the caller after deterministic retrieval, never by model output.
    del project
    coverage = Coverage(
        tender_package_member_ids=tuple(member.id for member in included_members),
        included_document_ids=tuple(
            int(version_rows[version_id][1]) for version_id in included_versions
        ),
        excluded_document_ids=tuple(sorted(excluded_document_ids)),
        document_version_ids=tuple(sorted(included_versions)),
        unexamined_ranges=("chunks not selected by deterministic retrieval",),
        state=CoverageState.PARTIAL_FAILURE,
    )
    return ReviewContext(
        project_id=review_run.project_id,
        review_run_id=review_run.id,
        tender_version_id=tender_version_id,
        allowed_document_version_ids=tuple(sorted(included_versions)),
        allowed_company_document_version_ids=tuple(sorted(company_versions)),
        allowed_chunk_ids=(),
        coverage=coverage,
        limits=limits,
    )


@dataclass(frozen=True, slots=True)
class ContextRequest:
    document_version_ids: frozenset[int]
    company_document_version_ids: frozenset[int]
    chunk_ids: frozenset[int]
