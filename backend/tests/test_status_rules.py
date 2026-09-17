import pytest

from app.domain.enums import DisplayStatus, EvidenceState, Severity
from app.domain.status_rules import calculate_display_status


@pytest.mark.parametrize(
    ("mandatory", "evidence", "severity", "needs_confirmation", "expected"),
    [
        (True, EvidenceState.MISSING, Severity.CRITICAL, False, DisplayStatus.HIGH_RISK),
        (True, EvidenceState.PARTIAL, Severity.WARNING, False, DisplayStatus.NEEDS_EVIDENCE),
        (False, EvidenceState.PARTIAL, Severity.OPPORTUNITY, False, DisplayStatus.OPTIMIZE),
        (True, EvidenceState.MATCHED, Severity.NONE, False, DisplayStatus.SATISFIED),
        (True, EvidenceState.UNCERTAIN, Severity.WARNING, True, DisplayStatus.NEEDS_CONFIRMATION),
    ],
)
def test_calculate_display_status(
    mandatory: bool,
    evidence: EvidenceState,
    severity: Severity,
    needs_confirmation: bool,
    expected: DisplayStatus,
) -> None:
    assert (
        calculate_display_status(
            mandatory=mandatory,
            evidence=evidence,
            severity=severity,
            needs_confirmation=needs_confirmation,
        )
        is expected
    )


def test_missing_mandatory_evidence_is_never_satisfied() -> None:
    result = calculate_display_status(
        mandatory=True,
        evidence=EvidenceState.MISSING,
        severity=Severity.NONE,
        needs_confirmation=False,
    )

    assert result is not DisplayStatus.SATISFIED
