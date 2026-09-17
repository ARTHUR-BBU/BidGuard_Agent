from pydantic import BaseModel, Field, field_validator

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
