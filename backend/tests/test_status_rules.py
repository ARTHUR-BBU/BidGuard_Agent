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


@pytest.mark.parametrize("severity", [Severity.NONE, Severity.OPPORTUNITY])
def test_missing_mandatory_evidence_is_high_risk(severity: Severity) -> None:
    assert calculate_display_status(
        mandatory=True,
        evidence=EvidenceState.MISSING,
        severity=severity,
        needs_confirmation=False,
    ) is DisplayStatus.HIGH_RISK


@pytest.mark.parametrize(
    ("mandatory", "evidence", "severity", "needs_confirmation"),
    [
        (True, EvidenceState.UNCERTAIN, Severity.WARNING, False),
        (False, EvidenceState.MATCHED, Severity.WARNING, True),
        (True, EvidenceState.MISSING, Severity.CRITICAL, True),
        (True, EvidenceState.UNCERTAIN, Severity.CRITICAL, False),
    ],
)
def test_confirmation_status_overrides_other_status_rules(
    mandatory: bool,
    evidence: EvidenceState,
    severity: Severity,
    needs_confirmation: bool,
) -> None:
    assert calculate_display_status(
        mandatory=mandatory,
        evidence=evidence,
        severity=severity,
        needs_confirmation=needs_confirmation,
    ) is DisplayStatus.NEEDS_CONFIRMATION


def test_critical_partial_opportunity_is_high_risk() -> None:
    assert calculate_display_status(
        mandatory=False,
        evidence=EvidenceState.PARTIAL,
        severity=Severity.CRITICAL,
        needs_confirmation=False,
    ) is DisplayStatus.HIGH_RISK
