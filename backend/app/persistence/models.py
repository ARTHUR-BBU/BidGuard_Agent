from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import (
    Mapped,
    Mapper,
    ORMExecuteState,
    mapped_column,
    relationship,
)
from sqlalchemy.sql.dml import UpdateBase

from app.db import Base, GuardedSession, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_fingerprint_part(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split()).casefold()


def calculate_requirement_fingerprint(kind: object, text: object) -> str:
    normalized_kind = _normalize_fingerprint_part(kind)
    normalized_text = _normalize_fingerprint_part(text)
    return hashlib.sha256(f"{normalized_kind}\n{normalized_text}".encode()).hexdigest()


def _requirement_fingerprint(context: Any) -> str:
    parameters = context.get_current_parameters()
    return calculate_requirement_fingerprint(parameters["kind"], parameters["text"])


class BidProject(Base):
    __tablename__ = "bid_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    review_runs: Mapped[list[ReviewRun]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    review_jobs: Mapped[list[ReviewJob]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    company_evidence_authorizations: Mapped[list[ProjectCompanyEvidence]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    tender_package_members: Mapped[list[TenderPackageMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    llm_call_records: Mapped[list[LLMCallRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "(role = 'company' AND project_id IS NULL AND company_content_sha256 IS NOT NULL) "
            "OR (role IN ('tender', 'proposal') AND project_id IS NOT NULL "
            "AND company_content_sha256 IS NULL)",
            name="ck_documents_identity",
        ),
        UniqueConstraint("project_id", "role", name="uq_documents_project_role"),
        UniqueConstraint("company_content_sha256", name="uq_documents_company_content"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    company_content_sha256: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    project: Mapped[BidProject | None] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_number"
        ),
        UniqueConstraint("document_id", "sha256", name="uq_document_versions_digest"),
        CheckConstraint("version_number >= 1", name="ck_document_versions_number_positive"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_document_versions_size_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    parse_status: Mapped[str] = mapped_column(
        String(40), default="pending", nullable=False
    )
    parse_error_code: Mapped[str | None] = mapped_column(String(80))
    parse_error: Mapped[str | None] = mapped_column(Text)
    parse_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    parse_attempt_id: Mapped[str | None] = mapped_column(String(36))
    parse_attempt_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    uploaded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_document_chunks_index"
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="ck_document_chunks_page_positive",
        ),
        CheckConstraint(
            "section_ordinal IS NULL OR section_ordinal >= 1",
            name="ck_document_chunks_section_ordinal_positive",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        CheckConstraint(
            "length(trim(text)) > 0",
            name="ck_document_chunks_text_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(500))
    section_ordinal: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")


class Requirement(Base):
    __tablename__ = "requirements"
    __guard_bulk_writes__ = True
    __table_args__ = (
        CheckConstraint(
            "source_page IS NULL OR source_page >= 1",
            name="ck_requirements_source_page_positive",
        ),
        CheckConstraint(
            "length(fingerprint) = 64", name="ck_requirements_fingerprint_length"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_section: Mapped[str | None] = mapped_column(String(500))
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fingerprint: Mapped[str] = mapped_column(
        String(64), default=_requirement_fingerprint, nullable=False, index=True
    )
    # Business identity (kind + text) and cited-location identity are kept
    # separate so repeated wording at different source locations is visible.
    citation_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    project: Mapped[BidProject] = relationship(back_populates="requirements")
    source_version: Mapped[DocumentVersion] = relationship()
    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", passive_deletes=True
    )
    action_items: Mapped[list[ActionItem]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", passive_deletes=True
    )
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", passive_deletes=True
    )


class ReviewRun(Base):
    __tablename__ = "review_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    resumable_requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL")
    )
    affected_requirement_ids: Mapped[list[int] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[BidProject] = relationship(back_populates="review_runs")
    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan", passive_deletes=True
    )
    llm_call_records: Mapped[list[LLMCallRecord]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan", passive_deletes=True
    )


class ProjectCompanyEvidence(Base):
    """Server-maintained authorization of a company document for a project."""

    __tablename__ = "project_company_evidence"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "document_version_id",
            name="uq_project_company_evidence_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    selected_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    selected_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    project: Mapped[BidProject] = relationship(back_populates="company_evidence_authorizations")
    document_version: Mapped[DocumentVersion] = relationship()


class TenderPackageMember(Base):
    """Included/excluded tender-package component and its precedence state."""

    __tablename__ = "tender_package_members"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "document_version_id",
            name="uq_tender_package_project_version",
        ),
        CheckConstraint(
            "member_kind IN ('main', 'attachment', 'addendum', 'clarification', 'template')",
            name="ck_tender_package_member_kind",
        ),
        CheckConstraint(
            "conflict_state IN ('resolved', 'unresolved', 'none')",
            name="ck_tender_package_conflict_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    member_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    precedence_order: Mapped[int | None] = mapped_column(Integer)
    conflict_state: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    supersedes_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("tender_package_members.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    project: Mapped[BidProject] = relationship(back_populates="tender_package_members")
    document_version: Mapped[DocumentVersion] = relationship()


class LLMCallRecord(Base):
    """Reference-only model call ledger; no prompt or document text columns."""

    __tablename__ = "llm_call_records"
    __table_args__ = (
        UniqueConstraint("review_run_id", "sequence", name="uq_llm_calls_run_sequence"),
        CheckConstraint("sequence >= 1", name="ck_llm_calls_sequence_positive"),
        CheckConstraint("retry_count >= 0", name="ck_llm_calls_retry_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    sdk_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_object_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    document_version_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    visible_chunk_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reported_cost_usd: Mapped[float | None] = mapped_column()
    stop_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    project: Mapped[BidProject] = relationship(back_populates="llm_call_records")
    review_run: Mapped[ReviewRun] = relationship(back_populates="llm_call_records")


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id", "review_run_id", name="uq_assessments_requirement_run"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_state: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    display_status: Mapped[str] = mapped_column(String(40), nullable=False)
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    current: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    requirement: Mapped[Requirement] = relationship(back_populates="assessments")
    review_run: Mapped[ReviewRun] = relationship(back_populates="assessments")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", passive_deletes=True
    )


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="ck_evidence_links_page_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(500))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    assessment: Mapped[Assessment] = relationship(back_populates="evidence_links")
    document_version: Mapped[DocumentVersion] = relationship()


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_by: Mapped[str | None] = mapped_column(String(200))

    requirement: Mapped[Requirement] = relationship(back_populates="action_items")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(80), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(200))
    review_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_runs.id", ondelete="SET NULL"), index=True
    )
    assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="SET NULL"), index=True
    )
    version_ids: Mapped[list[int] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    requirement: Mapped[Requirement] = relationship(back_populates="decisions")


@event.listens_for(Requirement, "before_delete")
def _protect_requirement_decision_history(
    _mapper: Mapper[Requirement],
    connection: Connection,
    target: Requirement,
) -> None:
    decision_id = connection.execute(
        select(Decision.id)
        .where(Decision.requirement_id == target.id)
        .limit(1)
    ).scalar_one_or_none()
    if decision_id is not None:
        raise ValueError(
            "Requirement with human or pending decisions cannot be deleted; deactivate it instead"
        )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    project: Mapped[BidProject] = relationship(back_populates="audit_events")


class ReviewJob(Base):
    __tablename__ = "review_jobs"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0", name="ck_review_jobs_attempt_nonnegative"
        ),
        CheckConstraint(
            "version_fingerprint IS NULL OR length(version_fingerprint) = 64",
            name="ck_review_jobs_fingerprint_length",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), index=True
    )
    tender_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True
    )
    version_fingerprint: Mapped[str | None] = mapped_column(
        String(64), index=True
    )
    version_ids: Mapped[list[int] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[BidProject] = relationship(back_populates="review_jobs")


@event.listens_for(Requirement, "before_insert")
def _set_requirement_fingerprint(
    _mapper: Mapper[Requirement],
    _connection: Connection,
    target: Requirement,
) -> None:
    target.fingerprint = calculate_requirement_fingerprint(target.kind, target.text)


@event.listens_for(Requirement, "before_update")
def _protect_requirement_identity(
    _mapper: Mapper[Requirement],
    _connection: Connection,
    target: Requirement,
) -> None:
    state = inspect(target)
    identity_fields = ("kind", "text", "fingerprint")
    if any(state.attrs[field].history.has_changes() for field in identity_fields):
        raise ValueError(
            "Requirement identity is immutable; create a new requirement and "
            "deactivate the old one"
        )


@event.listens_for(GuardedSession, "do_orm_execute")
def _protect_requirement_bulk_identity(execute_state: ORMExecuteState) -> None:
    if not (execute_state.is_insert or execute_state.is_update):
        return

    statement = execute_state.statement
    if not isinstance(statement, UpdateBase):
        return
    if (
        execute_state.bind_mapper is Requirement.__mapper__
        or statement.table is Requirement.__table__
    ):
        raise ValueError("Requirement bulk INSERT/UPDATE is not allowed")
