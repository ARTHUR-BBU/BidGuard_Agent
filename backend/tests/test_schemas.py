import pytest
from pydantic import ValidationError

from app.domain.enums import EvidenceState, RequirementKind, Severity
from app.domain.schemas import AssessmentCandidate, RequirementCandidate, SourceCitation


def test_source_citation_rejects_blank_quote_and_trims_valid_quote() -> None:
    with pytest.raises(ValidationError):
        SourceCitation(document_version_id=1, quote="   ")

    citation = SourceCitation(document_version_id=1, quote="  quoted evidence  ")

    assert citation.quote == "quoted evidence"


@pytest.mark.parametrize("text", ["   ", " ab "])
def test_requirement_candidate_rejects_blank_or_too_short_trimmed_text(text: str) -> None:
    with pytest.raises(ValidationError):
        RequirementCandidate(
            text=text,
            kind=RequirementKind.QUALIFICATION,
            mandatory=True,
            citation=SourceCitation(document_version_id=1, quote="source quote"),
        )


def test_requirement_candidate_trims_valid_text() -> None:
    candidate = RequirementCandidate(
        text="  valid requirement  ",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
        citation=SourceCitation(document_version_id=1, quote="source quote"),
    )

    assert candidate.text == "valid requirement"


def test_assessment_candidate_rejects_blank_reasoning_and_trims_valid_reasoning() -> None:
    with pytest.raises(ValidationError):
        AssessmentCandidate(
            requirement_id=1,
            evidence_state=EvidenceState.MATCHED,
            severity=Severity.NONE,
            needs_confirmation=False,
            reasoning="   ",
        )

    assessment = AssessmentCandidate(
        requirement_id=1,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        needs_confirmation=False,
        reasoning="  supported by source  ",
    )

    assert assessment.reasoning == "supported by source"


def test_candidate_default_lists_are_not_shared() -> None:
    first_requirement = RequirementCandidate(
        text="first requirement",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
        citation=SourceCitation(document_version_id=1, quote="source quote"),
    )
    second_requirement = RequirementCandidate(
        text="second requirement",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
        citation=SourceCitation(document_version_id=1, quote="source quote"),
    )
    first_requirement.requested_evidence.append("license")

    first_assessment = AssessmentCandidate(
        requirement_id=1,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        needs_confirmation=False,
        reasoning="supported by source",
    )
    second_assessment = AssessmentCandidate(
        requirement_id=2,
        evidence_state=EvidenceState.MATCHED,
        severity=Severity.NONE,
        needs_confirmation=False,
        reasoning="supported by source",
    )
    first_assessment.evidence.append(SourceCitation(document_version_id=1, quote="evidence"))

    assert second_requirement.requested_evidence == []
    assert second_assessment.evidence == []
