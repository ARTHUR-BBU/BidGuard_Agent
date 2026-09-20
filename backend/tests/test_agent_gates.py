from __future__ import annotations

import pytest

from app.agents.gates import (
    CitationGateError,
    requirement_fingerprint,
    validate_requirement_candidate,
)
from app.domain.enums import RequirementKind
from app.domain.schemas import RequirementCandidate, SourceCitation


def _candidate(
    *,
    version_id: int = 1,
    page: int | None = 1,
    section: str | None = None,
    quote: str = "投标人必须提供营业执照",
    text: str = "提供营业执照",
) -> RequirementCandidate:
    return RequirementCandidate(
        text=text,
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
        citation=SourceCitation(
            document_version_id=version_id,
            page_number=page,
            section_path=section,
            quote=quote,
        ),
    )


def _chunks() -> list[dict[str, object]]:
    return [
        {
            "document_version_id": 1,
            "page_number": 1,
            "section_path": None,
            "text": "投标人必须提供营业执照和法定代表人身份证明。",
        }
    ]


def test_valid_candidate_is_accepted_with_location_fingerprint() -> None:
    candidate = _candidate()
    validated = validate_requirement_candidate(candidate, 1, _chunks())

    assert validated.candidate == candidate
    assert len(validated.fingerprint) == 64
    assert validated.fingerprint == requirement_fingerprint(candidate)
    assert validated.fingerprint != requirement_fingerprint(
        _candidate(page=2)
    )


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (_candidate(version_id=2), "wrong_version"),
        (_candidate(page=9), "missing_page"),
        (_candidate(quote="这段文字不在原文中"), "quote_not_found"),
    ],
)
def test_invalid_citation_is_rejected(candidate, code: str) -> None:
    with pytest.raises(CitationGateError, match=code) as error:
        validate_requirement_candidate(candidate, 1, _chunks())
    assert error.value.code == code


def test_missing_location_is_rejected() -> None:
    candidate = _candidate(page=None, section=None)
    with pytest.raises(CitationGateError, match="missing_page"):
        validate_requirement_candidate(candidate, 1, _chunks())


def test_empty_quote_is_rejected_even_for_constructed_bad_model() -> None:
    candidate = RequirementCandidate.model_construct(
        text="提供营业执照",
        kind=RequirementKind.QUALIFICATION,
        mandatory=True,
        requested_evidence=[],
        citation=SourceCitation.model_construct(
            document_version_id=1,
            page_number=1,
            section_path=None,
            quote="   ",
        ),
    )
    with pytest.raises(CitationGateError, match="empty_quote"):
        validate_requirement_candidate(candidate, 1, _chunks())


def test_chunk_from_another_version_cannot_support_candidate() -> None:
    candidate = _candidate(version_id=1)
    chunks = [
        {
            "document_version_id": 2,
            "page_number": 1,
            "section_path": None,
            "text": "投标人必须提供营业执照",
        }
    ]
    with pytest.raises(CitationGateError, match="missing_page"):
        validate_requirement_candidate(candidate, 1, chunks)


def test_chunk_without_version_identity_is_rejected() -> None:
    candidate = _candidate(version_id=1)
    chunks = [{"page_number": 1, "section_path": None, "text": candidate.citation.quote}]
    with pytest.raises(CitationGateError, match="wrong_version"):
        validate_requirement_candidate(candidate, 1, chunks)
