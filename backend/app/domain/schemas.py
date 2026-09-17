from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import EvidenceState, RequirementKind, Severity


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


class SourceCitation(BaseModel):
    document_version_id: int
    page_number: int | None = None
    section_path: str | None = None
    quote: str = Field(min_length=1, max_length=2000)

    @field_validator("quote", mode="before")
    @classmethod
    def strip_quote(cls, value: object) -> object:
        return _strip_string(value)


class RequirementCandidate(BaseModel):
    text: str = Field(min_length=3)
    kind: RequirementKind
    mandatory: bool
    requested_evidence: list[str] = Field(default_factory=list)
    citation: SourceCitation

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return _strip_string(value)


class RequirementBatch(BaseModel):
    requirements: list[RequirementCandidate]


class AssessmentCandidate(BaseModel):
    requirement_id: int
    evidence_state: EvidenceState
    severity: Severity
    needs_confirmation: bool
    reasoning: str = Field(min_length=3, max_length=4000)
    evidence: list[SourceCitation] = Field(default_factory=list)
    recommendation: str = Field(default="", max_length=4000)

    @field_validator("reasoning", mode="before")
    @classmethod
    def strip_reasoning(cls, value: object) -> object:
        return _strip_string(value)


class StatusCounts(BaseModel):
    high_risk: int = 0
    needs_evidence: int = 0
    optimize: int = 0
    satisfied: int = 0
    needs_confirmation: int = 0


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    deadline_at: datetime | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("deadline_at")
    @classmethod
    def normalize_deadline_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone offset")
        return value.astimezone(UTC)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    deadline_at: datetime | None
    created_at: datetime
    status_counts: StatusCounts
