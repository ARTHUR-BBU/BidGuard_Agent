from app.domain.enums import DisplayStatus, EvidenceState, Severity


def calculate_display_status(
    mandatory: bool,
    evidence: EvidenceState,
    severity: Severity,
    needs_confirmation: bool,
) -> DisplayStatus:
    if needs_confirmation or evidence is EvidenceState.UNCERTAIN:
        return DisplayStatus.NEEDS_CONFIRMATION
    if mandatory and evidence is EvidenceState.MISSING:
        return DisplayStatus.HIGH_RISK
    if severity is Severity.CRITICAL:
        return DisplayStatus.HIGH_RISK
    if evidence in (EvidenceState.MISSING, EvidenceState.PARTIAL):
        if severity is Severity.OPPORTUNITY:
            return DisplayStatus.OPTIMIZE
        return DisplayStatus.NEEDS_EVIDENCE
    return DisplayStatus.SATISFIED
