from __future__ import annotations

import re
from pathlib import Path

from app.domain.schemas import ParsedDocument

DEFAULT_CHUNK_SIZE = 3_000
DEFAULT_CHUNK_OVERLAP = 300

SAFE_PARSE_MESSAGES = {
    "invalid_document": "Document format is invalid or unsupported",
    "resource_limit_exceeded": "Document exceeds safe parsing limits",
    "encrypted_document": "Encrypted documents are not supported",
    "source_integrity_failed": "Stored document failed integrity verification",
    "parse_failed": "Document parsing failed",
}


class DocumentParseError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in SAFE_PARSE_MESSAGES else "parse_failed"
        super().__init__(SAFE_PARSE_MESSAGES[self.code])


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk size must be positive and greater than overlap")
    normalized = normalize_whitespace(text)
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == len(normalized):
            break
        next_start = end - overlap
        if next_start <= start:
            raise RuntimeError("chunking did not advance")
        start = next_start
    return chunks


def parse_document(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from app.documents.pdf import parse_pdf

        return parse_pdf(path)
    if suffix == ".docx":
        from app.documents.docx import parse_docx

        return parse_docx(path)
    raise DocumentParseError("invalid_document")
