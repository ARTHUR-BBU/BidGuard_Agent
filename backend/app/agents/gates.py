from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.enums import EvidenceState
from app.domain.schemas import AssessmentCandidate, RequirementCandidate


class CitationGateError(ValueError):
    """A candidate cannot become a formal requirement without a valid citation."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class UnsupportedPassError(ValueError):
    code = "unsupported_pass"


class StaleEvidenceError(ValueError):
    code = "stale_evidence"


@dataclass(frozen=True, slots=True)
class ValidatedRequirement:
    candidate: RequirementCandidate
    fingerprint: str

    @property
    def text(self) -> str:
        return self.candidate.text


def _normalise(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _chunk_value(chunk: object, name: str) -> object:
    if isinstance(chunk, dict):
        return chunk.get(name)
    return getattr(chunk, name, None)


def _quote_exists(quote: str, text: str) -> bool:
    return _normalise(quote) in _normalise(text)


def requirement_fingerprint(candidate: RequirementCandidate) -> str:
    citation = candidate.citation
    identity = "\n".join(
        (
            _normalise(candidate.text),
            _normalise(candidate.kind),
            str(citation.document_version_id),
            str(citation.page_number or ""),
            _normalise(citation.section_path or ""),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def validate_requirement_candidate(
    candidate: RequirementCandidate,
    active_version: int,
    chunks: Iterable[object],
) -> ValidatedRequirement:
    """Validate one model candidate without model calls or persistence."""

    citation = candidate.citation
    if citation.document_version_id != active_version:
        raise CitationGateError("wrong_version")
    if citation.page_number is None and not citation.section_path:
        raise CitationGateError("missing_page")
    quote = citation.quote.strip()
    if not quote:
        raise CitationGateError("empty_quote")

    matching_chunks = []
    for chunk in chunks:
        chunk_version_id = _chunk_value(chunk, "document_version_id")
        if chunk_version_id is None:
            raise CitationGateError("wrong_version")
        page_matches = (
            citation.page_number is None
            or _chunk_value(chunk, "page_number") == citation.page_number
        )
        version_matches = chunk_version_id == active_version
        section_matches = (
            not citation.section_path
            or _chunk_value(chunk, "section_path") == citation.section_path
        )
        if page_matches and section_matches and version_matches:
            matching_chunks.append(chunk)
    if not matching_chunks:
        raise CitationGateError("missing_page")
    if not any(
        _quote_exists(quote, str(_chunk_value(chunk, "text") or ""))
        for chunk in matching_chunks
    ):
        raise CitationGateError("quote_not_found")
    return ValidatedRequirement(candidate=candidate, fingerprint=requirement_fingerprint(candidate))


def validate_assessment(
    candidate: AssessmentCandidate,
    active_versions: set[int],
) -> AssessmentCandidate:
    """Reject unsupported passes and citations outside active evidence scope."""

    if (
        candidate.evidence_state is EvidenceState.MATCHED
        and not candidate.evidence
    ):
        raise UnsupportedPassError("matched assessment requires evidence")
    if any(
        citation.document_version_id not in active_versions
        for citation in candidate.evidence
    ):
        raise StaleEvidenceError("assessment cites an inactive document version")
    return candidate
