"""Deterministic, model-independent retrieval of parsed document chunks.

This module returns candidates only.  It does not create an EvidenceLink, decide
whether a requirement is met, or infer which document version is current.  The
caller must provide the exact version ids authorized by its ReviewContext.
"""

from __future__ import annotations

import heapq
import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from sqlalchemy import Select, select

from app.db import GuardedSession
from app.persistence.models import Document, DocumentChunk, DocumentVersion

_SEARCHABLE_PARSE_STATUSES = ("parsed", "partial_failure")
_MAX_SEARCH_LIMIT = 100
_SEARCH_BATCH_SIZE = 256
_SEARCH_SCOPE_ERROR_CODE = "search_scope_invalid"

# Keep contiguous Chinese phrases together while splitting Latin identifiers
# and numbers at punctuation.  This is intentionally small and deterministic;
# it is not a semantic segmenter and does not call a model or an external index.
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A traceable candidate chunk, never a validated EvidenceLink."""

    chunk_id: int
    document_version_id: int
    document_id: int
    project_id: int | None
    version_number: int
    document_role: str
    chunk_index: int
    page_number: int | None
    section_path: str | None
    text: str
    score: float
    parse_status: str
    coverage_complete: bool


class SearchScopeError(ValueError):
    """Raised when the requested search scope is not owned by the project."""

    code = _SEARCH_SCOPE_ERROR_CODE


def tokenize(text: str) -> frozenset[str]:
    """Return normalized unique lexical terms with stable Unicode handling."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return frozenset(_TOKEN_RE.findall(normalized))


def overlap_score(query: str, text: str) -> float:
    """Return the fraction of unique query terms present in a chunk."""

    query_terms = tokenize(query)
    if not query_terms:
        return 0.0
    text_terms = tokenize(text)
    matched = sum(
        1
        for term in query_terms
        if term in text_terms
        or (
            _contains_cjk(term)
            and any(term in candidate for candidate in text_terms)
        )
    )
    return matched / len(query_terms)


def _contains_cjk(term: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in term
    )


def _allowed_ids(values: Iterable[int]) -> tuple[int, ...]:
    # Preserve caller order only for predictable SQL parameters; ordering of
    # results is determined by the explicit stable ranking below. Invalid IDs
    # are rejected rather than silently dropped, which keeps the scope fail
    # closed.
    values_tuple = tuple(values)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in values_tuple
    ):
        raise SearchScopeError(_SEARCH_SCOPE_ERROR_CODE)
    return tuple(dict.fromkeys(values_tuple))


def _validate_scope(
    session: GuardedSession,
    project_id: int,
    allowed_ids: Collection[int],
) -> None:
    owned_ids = set(
        session.scalars(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.project_id == project_id)
            .where(DocumentVersion.id.in_(allowed_ids))
        )
    )
    if owned_ids != set(allowed_ids):
        raise SearchScopeError(_SEARCH_SCOPE_ERROR_CODE)


def _search_statement(
    project_id: int,
    allowed_ids: Collection[int],
    roles: Collection[str] | None,
) -> Select[tuple[object, ...]]:
    statement = (
        select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.document_version_id.label("document_version_id"),
            DocumentVersion.document_id.label("document_id"),
            Document.project_id.label("project_id"),
            DocumentVersion.version_number.label("version_number"),
            Document.role.label("document_role"),
            DocumentChunk.chunk_index.label("chunk_index"),
            DocumentChunk.page_number.label("page_number"),
            DocumentChunk.section_path.label("section_path"),
            DocumentChunk.text.label("text"),
            DocumentVersion.parse_status.label("parse_status"),
            DocumentVersion.parse_coverage.label("parse_coverage"),
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == DocumentChunk.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.project_id == project_id)
        .where(DocumentChunk.document_version_id.in_(allowed_ids))
        .where(DocumentVersion.parse_status.in_(_SEARCHABLE_PARSE_STATUSES))
        .order_by(DocumentChunk.id)
    )
    if roles is not None:
        statement = statement.where(Document.role.in_(roles))
    return statement


def search_chunks(
    session: GuardedSession,
    query: str,
    *,
    project_id: int,
    allowed_document_version_ids: Iterable[int],
    document_roles: Iterable[str] | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Search only authorized parsed versions and return stable top candidates.

    ``project_id`` is a server-side authorization boundary, and every supplied
    version id must belong to it. ``allowed_document_version_ids`` is an
    additional exact scope within that project. This function deliberately does
    not infer the latest/current version; callers must pass the versions
    selected by the current project/review context.
    ``partial_failure`` versions are searchable, but every result is marked
    ``coverage_complete=False`` so a later gate can refuse a completeness claim.
    Pending, parsing, failed, and needs-OCR versions are excluded.
    """

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAX_SEARCH_LIMIT
    ):
        raise ValueError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}")

    allowed_ids = _allowed_ids(allowed_document_version_ids)
    if not allowed_ids:
        return []
    if not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0:
        raise SearchScopeError(_SEARCH_SCOPE_ERROR_CODE)
    _validate_scope(session, project_id, allowed_ids)

    query_terms = tokenize(query)
    if not query_terms:
        return []

    roles = None
    if document_roles is not None:
        roles = frozenset(str(role) for role in document_roles)
        if not roles:
            return []

    # The heap bounds in-memory result retention to ``limit`` while SQLAlchemy
    # streams chunks in batches.  We never materialize all document text.
    best: list[tuple[float, int, int, int, SearchResult]] = []
    statement = _search_statement(project_id, allowed_ids, roles)
    for row in session.execute(statement).mappings().yield_per(_SEARCH_BATCH_SIZE):
        text = str(row["text"])
        score = overlap_score(query, text)
        if score <= 0:
            continue
        result = SearchResult(
            chunk_id=int(row["chunk_id"]),
            document_version_id=int(row["document_version_id"]),
            document_id=int(row["document_id"]),
            project_id=(
                int(row["project_id"]) if row["project_id"] is not None else None
            ),
            version_number=int(row["version_number"]),
            document_role=str(row["document_role"]),
            chunk_index=int(row["chunk_index"]),
            page_number=(
                int(row["page_number"]) if row["page_number"] is not None else None
            ),
            section_path=(
                str(row["section_path"])
                if row["section_path"] is not None
                else None
            ),
            text=text,
            score=score,
            parse_status=str(row["parse_status"]),
            coverage_complete=_coverage_complete(
                str(row["parse_status"]), row["parse_coverage"]
            ),
        )
        # Negated tie-breakers make the heap root the least desirable item;
        # final output is sorted using the human-readable ascending keys.
        rank = (score, -result.document_version_id, -result.chunk_index, -result.chunk_id)
        item = (*rank, result)
        if len(best) < limit:
            heapq.heappush(best, item)
        elif item[:4] > best[0][:4]:
            heapq.heapreplace(best, item)

    return [
        item[4]
        for item in sorted(
            best,
            key=lambda item: (
                -item[4].score,
                item[4].document_version_id,
                item[4].chunk_index,
                item[4].chunk_id,
            ),
        )
    ]


def _coverage_complete(parse_status: str, parse_coverage: object) -> bool:
    """Defensively derive completeness from persisted Task 6 coverage.

    JSON is untrusted persistence, so malformed values must become an
    incomplete candidate rather than raising during retrieval or being treated
    as complete. PDFs need a complete physical-page partition; DOCX files need
    a complete section range. The only coverage issue compatible with a
    complete result is Task 6's known ``blank_page`` issue, and only when its
    page is present in a credible PDF page partition.
    """

    try:
        if parse_status != "parsed" or not isinstance(parse_coverage, dict):
            return False
        if not _valid_common_coverage(parse_coverage):
            return False

        total_pages = parse_coverage.get("total_pages")
        total_sections = parse_coverage.get("total_sections")
        if total_pages is not None and total_sections is not None:
            return False
        if total_pages is not None:
            return _valid_complete_pdf_coverage(parse_coverage, total_pages)
        if total_sections is not None:
            return _valid_complete_docx_coverage(parse_coverage, total_sections)
        return False
    except Exception:  # noqa: BLE001 - persisted JSON must fail closed.
        return False


def _valid_common_coverage(coverage: dict[object, object]) -> bool:
    required = {"coverage_issues", "needs_ocr"}
    if not required.issubset(coverage):
        return False
    if not isinstance(coverage["needs_ocr"], bool):
        return False
    issues = coverage["coverage_issues"]
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            return False
        code = issue.get("code")
        if not isinstance(code, str) or not code.strip():
            return False
        for field in ("page_number", "section_ordinal"):
            value = issue.get(field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                return False
        object_index = issue.get("object_index")
        if object_index is not None and (
            not isinstance(object_index, int)
            or isinstance(object_index, bool)
            or object_index < 0
        ):
            return False
        section_path = issue.get("section_path")
        if section_path is not None and (
            not isinstance(section_path, str) or not section_path.strip()
        ):
            return False
        if not any(
            issue.get(field) is not None
            for field in ("page_number", "section_ordinal", "section_path")
        ):
            return False
    return True


def _valid_int_list(
    value: object,
    *,
    maximum: int | None,
) -> set[int] | None:
    if not isinstance(value, list):
        return None
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in value
    ):
        return None
    result = set(value)
    if len(result) != len(value) or value != sorted(value):
        return None
    if maximum is not None and any(item > maximum for item in result):
        return None
    return result


def _valid_total(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _valid_complete_pdf_coverage(
    coverage: dict[object, object], total_pages_value: object
) -> bool:
    total_pages = _valid_total(total_pages_value)
    if total_pages is None:
        return False
    page_fields = ("parsed_pages", "blank_pages", "failed_pages", "ocr_pages")
    if not all(field in coverage for field in page_fields):
        return False
    page_sets: dict[str, set[int]] = {}
    for field in page_fields:
        values = _valid_int_list(coverage[field], maximum=total_pages)
        if values is None:
            return False
        page_sets[field] = values
    if any(
        page_sets[left] & page_sets[right]
        for index, left in enumerate(page_fields)
        for right in page_fields[index + 1 :]
    ):
        return False
    expected = set(range(1, total_pages + 1))
    if set().union(*page_sets.values()) != expected:
        return False
    ocr_pages = page_sets["ocr_pages"]
    if page_sets["failed_pages"] or ocr_pages:
        return False
    if coverage["needs_ocr"] != bool(ocr_pages):
        return False
    issues = coverage["coverage_issues"]
    assert isinstance(issues, list)  # validated by _valid_common_coverage
    issue_pages: set[int] = set()
    for issue in issues:
        assert isinstance(issue, dict)
        page_number = issue.get("page_number")
        if page_number is not None and page_number > total_pages:
            return False
        if issue.get("section_ordinal") is not None:
            return False
        if issue.get("code") != "blank_page":
            return False
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            return False
        issue_pages.add(page_number)
    return issue_pages.issubset(page_sets["blank_pages"])


def _valid_complete_docx_coverage(
    coverage: dict[object, object], total_sections_value: object
) -> bool:
    total_sections = _valid_total(total_sections_value)
    if total_sections is None or "parsed_sections" not in coverage:
        return False
    parsed_sections = _valid_int_list(
        coverage["parsed_sections"], maximum=total_sections
    )
    if parsed_sections is None:
        return False
    if parsed_sections != set(range(1, total_sections + 1)):
        return False
    for field in ("parsed_pages", "blank_pages", "failed_pages", "ocr_pages"):
        if field in coverage and _valid_int_list(coverage[field], maximum=None) != set():
            return False
    if coverage["needs_ocr"]:
        return False
    issues = coverage["coverage_issues"]
    assert isinstance(issues, list)  # validated by _valid_common_coverage
    for issue in issues:
        assert isinstance(issue, dict)
        if issue.get("page_number") is not None:
            return False
        section_ordinal = issue.get("section_ordinal")
        if section_ordinal is not None and section_ordinal > total_sections:
            return False
        if issue.get("code") != "blank_page":
            return False
    return True
