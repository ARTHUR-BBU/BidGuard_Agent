from enum import StrEnum


class DocumentRole(StrEnum):
    TENDER = "tender"
    PROPOSAL = "proposal"
    COMPANY = "company"


class RequirementKind(StrEnum):
    QUALIFICATION = "qualification"
    SUBSTANTIAL = "substantial"
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    SCORING = "scoring"


class EvidenceState(StrEnum):
    MISSING = "missing"
    PARTIAL = "partial"
    MATCHED = "matched"
    UNCERTAIN = "uncertain"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    OPPORTUNITY = "opportunity"
    NONE = "none"


class DisplayStatus(StrEnum):
    HIGH_RISK = "high_risk"
    NEEDS_EVIDENCE = "needs_evidence"
    OPTIMIZE = "optimize"
    SATISFIED = "satisfied"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ReviewRunStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    REVIEWING = "reviewing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
