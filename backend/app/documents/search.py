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
    # results is determined by the explicit stable ranking below.
    return tuple(dict.fromkeys(value for value in values if isinstance(value, int)))


def _search_statement(
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
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == DocumentChunk.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
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
    allowed_document_version_ids: Iterable[int],
    document_roles: Iterable[str] | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Search only authorized parsed versions and return stable top candidates.

    ``allowed_document_version_ids`` is an exact authorization boundary.  This
    function deliberately does not infer the latest/current version; callers
    must pass the versions selected by the current project/review context.
    ``partial_failure`` versions are searchable, but every result is marked
    ``coverage_complete=False`` so a later gate can refuse a completeness claim.
    Pending, parsing, failed, and needs-OCR versions are excluded.
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}")

    query_terms = tokenize(query)
    if not query_terms:
        return []

    allowed_ids = _allowed_ids(allowed_document_version_ids)
    if not allowed_ids:
        return []

    roles = None
    if document_roles is not None:
        roles = frozenset(str(role) for role in document_roles)
        if not roles:
            return []

    # The heap bounds in-memory result retention to ``limit`` while SQLAlchemy
    # streams chunks in batches.  We never materialize all document text.
    best: list[tuple[float, int, int, int, SearchResult]] = []
    statement = _search_statement(allowed_ids, roles)
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
            coverage_complete=str(row["parse_status"]) == "parsed",
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
