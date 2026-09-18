from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.documents.parser import DocumentParseError, chunk_text, normalize_whitespace
from app.domain.schemas import ParseCoverageIssue, ParsedChunk, ParsedDocument

MIN_VISIBLE_CHARACTERS = 30
HEADER_WITH_IMAGE_THRESHOLD = 100
MAX_PDF_PAGES = 2_000
MAX_EXTRACTED_TEXT_CHARACTERS = 5_000_000


def _visible_character_count(text: str) -> int:
    return sum(
        1
        for character in text
        if not character.isspace() and not unicodedata.category(character).startswith("C")
    )


def _page_has_images(page: Any) -> bool:
    try:
        resources = page.get("/Resources")
        if resources is not None:
            resources = resources.get_object()
            xobjects = resources.get("/XObject")
            if xobjects is not None:
                for value in xobjects.get_object().values():
                    obj = value.get_object()
                    if str(obj.get("/Subtype")) == "/Image":
                        return True
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    try:
        return bool(page.images)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        reader = PdfReader(str(path), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                if reader.decrypt("") == 0:
                    raise DocumentParseError("encrypted_document")
            except DocumentParseError:
                raise
            except Exception as error:
                raise DocumentParseError("encrypted_document") from error
        total_pages = len(reader.pages)
    except DocumentParseError:
        raise
    except (OSError, PdfReadError, ValueError) as error:
        raise DocumentParseError("invalid_document") from error
    if total_pages > MAX_PDF_PAGES:
        raise DocumentParseError("resource_limit_exceeded")

    chunks: list[ParsedChunk] = []
    parsed_pages: list[int] = []
    blank_pages: list[int] = []
    failed_pages: list[int] = []
    ocr_pages: list[int] = []
    issues: list[ParseCoverageIssue] = []
    extracted_characters = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
            normalized = normalize_whitespace(raw_text)
            has_image = _page_has_images(page)
        except Exception:  # noqa: BLE001 - page plugins can raise arbitrary errors.
            failed_pages.append(page_number)
            issues.append(
                ParseCoverageIssue(
                    code="page_extraction_failed", page_number=page_number
                )
            )
            continue
        visible = _visible_character_count(normalized)
        extracted_characters += len(normalized)
        if extracted_characters > MAX_EXTRACTED_TEXT_CHARACTERS:
            raise DocumentParseError("resource_limit_exceeded")
        if has_image:
            issues.append(
                ParseCoverageIssue(
                    code="image_not_extracted", page_number=page_number
                )
            )
        if visible == 0 and not has_image:
            blank_pages.append(page_number)
            issues.append(
                ParseCoverageIssue(code="blank_page", page_number=page_number)
            )
            continue
        if visible < MIN_VISIBLE_CHARACTERS or (has_image and visible < HEADER_WITH_IMAGE_THRESHOLD):
            ocr_pages.append(page_number)
            issues.append(
                ParseCoverageIssue(
                    code="low_text_requires_ocr", page_number=page_number
                )
            )
            continue
        parsed_pages.append(page_number)
        for piece in chunk_text(normalized):
            chunks.append(
                ParsedChunk(
                    chunk_index=len(chunks),
                    page_number=page_number,
                    section_path=None,
                    text=piece,
                )
            )
    return ParsedDocument(
        chunks=chunks,
        total_pages=total_pages,
        parsed_pages=parsed_pages,
        blank_pages=blank_pages,
        failed_pages=failed_pages,
        ocr_pages=ocr_pages,
        coverage_issues=issues,
        needs_ocr=bool(ocr_pages),
    )
