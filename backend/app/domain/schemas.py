import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import EvidenceState, RequirementKind, Severity

PROJECT_DEADLINE_ISO_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


class ParseCoverageIssue(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    page_number: int | None = Field(default=None, ge=1)
    section_path: str | None = Field(default=None, min_length=1, max_length=500)
    object_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_location(self) -> ParseCoverageIssue:
        if self.page_number is None and self.section_path is None:
            raise ValueError("coverage issue must identify a page or section")
        return self


class ParsedChunk(BaseModel):
    chunk_index: int = Field(ge=0)
    page_number: int | None = Field(default=None, ge=1)
    section_path: str | None = Field(default=None, min_length=1, max_length=500)
    text: str = Field(min_length=1)

    @field_validator("text", mode="before")
    @classmethod
    def strip_chunk_text(cls, value: object) -> object:
        return _strip_string(value)


class ParsedDocument(BaseModel):
    chunks: list[ParsedChunk]
    total_pages: int | None = Field(default=None, ge=0)
    parsed_pages: list[int] = Field(default_factory=list)
    blank_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)
    coverage_issues: list[ParseCoverageIssue] = Field(default_factory=list)
    total_sections: int | None = Field(default=None, ge=0)
    parsed_sections: list[str] = Field(default_factory=list)
    needs_ocr: bool = False

    @model_validator(mode="after")
    def validate_coverage(self) -> ParsedDocument:
        indexes = [chunk.chunk_index for chunk in self.chunks]
        if indexes != list(range(len(indexes))):
            raise ValueError("chunk indexes must be contiguous and zero-based")
        for field_name in (
            "parsed_pages",
            "blank_pages",
            "failed_pages",
            "ocr_pages",
        ):
            values = getattr(self, field_name)
            if values != sorted(set(values)) or any(value < 1 for value in values):
                raise ValueError(f"{field_name} must be sorted unique positive pages")
            if self.total_pages is not None and any(
                value > self.total_pages for value in values
            ):
                raise ValueError(f"{field_name} contains a page beyond total_pages")
        page_sets = [
            set(self.parsed_pages),
            set(self.blank_pages),
            set(self.failed_pages),
            set(self.ocr_pages),
        ]
        if any(
            page_sets[index] & page_sets[other]
            for index in range(len(page_sets))
            for other in range(index + 1, len(page_sets))
        ):
            raise ValueError("parsed, blank, failed, and OCR pages must not overlap")
        if self.needs_ocr != bool(self.ocr_pages):
            raise ValueError("needs_ocr must reflect ocr_pages")
        if self.parsed_sections != list(dict.fromkeys(self.parsed_sections)):
            raise ValueError("parsed_sections must be unique and ordered")
        if self.total_sections is not None and len(self.parsed_sections) > self.total_sections:
            raise ValueError("parsed_sections cannot exceed total_sections")
        return self


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

    @field_validator("name")
    @classmethod
    def require_visible_name(cls, value: str) -> str:
        if not any(
            not character.isspace() and unicodedata.category(character) != "Cf"
            for character in value
        ):
            raise ValueError("name must contain a visible character")
        return value

    @field_validator("deadline_at", mode="before")
    @classmethod
    def normalize_deadline_to_utc(cls, value: object) -> datetime | None:
        if value is None:
            return None
        try:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, str):
                iso_value = value.strip()
                if PROJECT_DEADLINE_ISO_PATTERN.fullmatch(iso_value) is None:
                    raise ValueError("deadline_at must use strict ISO-8601 format")
                if iso_value.endswith("Z"):
                    iso_value = f"{iso_value[:-1]}+00:00"
                parsed = datetime.fromisoformat(iso_value)
            else:
                raise ValueError(  # noqa: TRY004 - Pydantic converts this to HTTP 422.
                    "deadline_at must be an ISO-8601 datetime string"
                )
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("deadline_at must include a timezone offset")
            return parsed.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise ValueError("deadline_at must be a valid UTC datetime") from error


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    deadline_at: datetime | None
    created_at: datetime
    status_counts: StatusCounts


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_number: int
    sha256: str
    size_bytes: int | None
    parse_status: str
    parse_error_code: str | None
    parse_error: str | None
    parse_coverage: dict[str, object] | None
    uploaded_at: datetime


class DocumentResponse(BaseModel):
    id: int
    project_id: int | None
    role: str
    display_name: str
    created_at: datetime
    versions: list[DocumentVersionResponse]


class DocumentUploadResponse(BaseModel):
    document_id: int
    version_id: int
    version_number: int
    sha256: str
    size_bytes: int | None
    parse_status: str
    parse_error_code: str | None
    parse_error: str | None
    parse_coverage: dict[str, object] | None
    uploaded_at: datetime
    created: bool
