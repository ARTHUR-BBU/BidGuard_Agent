from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReasonCode(StrEnum):
    """Stable machine-readable outcomes for model-boundary decisions."""

    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_SCOPE = "rejected_scope"
    REJECTED_COVERAGE = "rejected_coverage"
    REJECTED_BUDGET = "rejected_budget"
    MODEL_NOT_CONFIGURED = "model_not_configured"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    TOOL_LIMIT = "tool_limit"
    MANUAL_STOP = "manual_stop"
    PARTIAL_FAILURE = "partial_failure"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    INVALID_CONTEXT = "invalid_context"


class StopReason(StrEnum):
    COMPLETED = "completed"
    MANUAL_STOP = "manual_stop"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    TOOL_LIMIT = "tool_limit"
    FAILURE = "failure"


class CoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL_FAILURE = "partial_failure"
    NEEDS_CONFIRMATION = "needs_confirmation"


class Coverage(BaseModel):
    """The exact source range a bounded model step was allowed to see."""

    model_config = ConfigDict(extra="forbid")

    state: CoverageState = CoverageState.COMPLETE
    tender_package_member_ids: tuple[int, ...] = ()
    included_document_ids: tuple[int, ...] = ()
    excluded_document_ids: tuple[int, ...] = ()
    document_version_ids: tuple[int, ...] = ()
    visible_chunk_ids: tuple[int, ...] = ()
    visible_page_numbers: tuple[int, ...] = ()
    parsed_page_numbers: tuple[int, ...] = ()
    failed_page_numbers: tuple[int, ...] = ()
    ocr_page_numbers: tuple[int, ...] = ()
    unexamined_ranges: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator(
        "tender_package_member_ids",
        "included_document_ids",
        "excluded_document_ids",
        "document_version_ids",
        "visible_chunk_ids",
        "visible_page_numbers",
        "parsed_page_numbers",
        "failed_page_numbers",
        "ocr_page_numbers",
    )
    @classmethod
    def sorted_unique_nonnegative(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(item < 0 for item in value):
            raise ValueError("coverage identifiers cannot be negative")
        if tuple(value) != tuple(sorted(set(value))):
            raise ValueError("coverage identifiers must be sorted and unique")
        return value

    @field_validator(
        "visible_page_numbers",
        "parsed_page_numbers",
        "failed_page_numbers",
        "ocr_page_numbers",
    )
    @classmethod
    def pages_are_positive(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(item < 1 for item in value):
            raise ValueError("coverage page numbers must be positive")
        return value

    @model_validator(mode="after")
    def validate_state(self) -> Coverage:
        if any(not item.strip() or len(item) > 500 for item in self.unexamined_ranges):
            raise ValueError("coverage ranges must be short, non-empty descriptions")
        if self.state is CoverageState.COMPLETE and not self.document_version_ids:
            raise ValueError("complete coverage must identify a document version")
        if self.state is CoverageState.COMPLETE and not (
            self.visible_chunk_ids or self.visible_page_numbers
        ):
            raise ValueError("complete coverage must identify visible chunks or pages")
        if self.state is CoverageState.COMPLETE and self.unexamined_ranges:
            raise ValueError("complete coverage cannot contain unexamined ranges")
        if self.state is not CoverageState.COMPLETE and not self.unexamined_ranges:
            raise ValueError("partial coverage must name unexamined ranges")
        return self


class EvidenceFact(BaseModel):
    """A future validated fact, kept separate from a model-generated claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "evidence_fact.v1"
    document_version_id: int = Field(gt=0)
    chunk_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2000)
    verified: bool = False


class Claim(BaseModel):
    """A candidate interpretation that has no authority until verified."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "claim.v1"
    claim_type: str = Field(pattern=r"^(supports|contradicts|missing|conflict)$")
    requirement_id: int = Field(gt=0)
    evidence_fact_ids: tuple[int, ...] = ()
    verified: bool = False


class AgentRuntimeLimits(BaseModel):
    """Server-owned limits; model output can never increase these values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(default=1, ge=1, le=100)
    max_tool_calls: int = Field(default=0, ge=0, le=1000)
    max_retries: int = Field(default=0, ge=0, le=10)
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    max_input_tokens: int = Field(default=12000, ge=1, le=1_000_000)
    max_output_tokens: int = Field(default=4000, ge=1, le=100_000)
    max_cost_usd: float = Field(default=1.0, ge=0, le=1000)
    max_batch_items: int = Field(default=20, ge=1, le=1000)


def prompt_hash(prompt_version: str, prompt_text: str) -> str:
    if not prompt_version.strip():
        raise ValueError("prompt version is required")
    if not prompt_text.strip():
        raise ValueError("prompt text is required")
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


class ToolCallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    attempted: int = Field(default=0, ge=0, le=1000)
    succeeded: int = Field(default=0, ge=0, le=1000)
    rejected: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> ToolCallSummary:
        if self.succeeded + self.rejected > self.attempted:
            raise ValueError("tool result counts cannot exceed attempts")
        return self


class ModelCallLedger(BaseModel):
    """Safe, reference-oriented record of one model attempt.

    It deliberately has no prompt or document text field. The default ledger
    stores IDs, hashes, coverage and outcomes, not sensitive source material.
    """

    model_config = ConfigDict(extra="forbid")

    review_run_id: int = Field(gt=0)
    node: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    sdk_version: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=120)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_object_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    document_version_ids: tuple[int, ...] = ()
    visible_chunk_ids: tuple[int, ...] = ()
    coverage: Coverage
    output_schema_version: str = Field(min_length=1, max_length=120)
    accepted: bool
    reason_code: ReasonCode
    tool_calls: list[ToolCallSummary] = Field(default_factory=list, max_length=100)
    started_at: datetime
    finished_at: datetime
    retry_count: int = Field(default=0, ge=0, le=10)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: float | None = Field(default=None, ge=0)
    stop_reason: StopReason

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ledger timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("input_object_ids")
    @classmethod
    def validate_object_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed_prefixes = {
            "assessment",
            "chunk",
            "document",
            "evidence",
            "requirement",
            "version",
        }
        for item in value:
            prefix, separator, identifier = item.partition(":")
            if (
                not separator
                or prefix not in allowed_prefixes
                or not identifier.isdigit()
                or int(identifier) <= 0
                or len(item) > 120
            ):
                raise ValueError("input object IDs must be typed positive identifiers")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> ModelCallLedger:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.reason_code is ReasonCode.ACCEPTED and not self.accepted:
            raise ValueError("accepted reason code requires accepted=true")
        if self.accepted and self.reason_code not in {
            ReasonCode.ACCEPTED,
            ReasonCode.COMPLETED,
        }:
            raise ValueError("accepted calls must use an accepted reason code")
        expected_stop_reasons = {
            ReasonCode.COMPLETED: StopReason.COMPLETED,
            ReasonCode.TIMEOUT: StopReason.TIMEOUT,
            ReasonCode.TOOL_LIMIT: StopReason.TOOL_LIMIT,
            ReasonCode.REJECTED_BUDGET: StopReason.BUDGET_EXHAUSTED,
            ReasonCode.MANUAL_STOP: StopReason.MANUAL_STOP,
        }
        expected = expected_stop_reasons.get(self.reason_code)
        if expected is not None and self.stop_reason is not expected:
            raise ValueError("stop reason must match reason code")
        if self.coverage.document_version_ids != self.document_version_ids:
            raise ValueError("ledger document versions must match coverage")
        if self.coverage.visible_chunk_ids != self.visible_chunk_ids:
            raise ValueError("ledger visible chunks must match coverage")
        return self


def json_safe_ledger(ledger: ModelCallLedger) -> dict[str, Any]:
    """Return the persistence payload without sensitive prompt/document text."""

    return ledger.model_dump(mode="json")
