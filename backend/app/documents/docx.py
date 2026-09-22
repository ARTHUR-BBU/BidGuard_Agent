from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from docx import Document as WordDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.documents.parser import DocumentParseError, chunk_text, normalize_whitespace
from app.domain.schemas import ParseCoverageIssue, ParsedChunk, ParsedDocument

MAX_DOCX_MEMBERS = 10_000
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 1_000
MAX_DOCX_BLOCKS = 100_000
MAX_EXTRACTED_TEXT_CHARACTERS = 5_000_000
_HEADING_PATTERN = re.compile(r"^Heading\s+([1-9][0-9]*)$", re.IGNORECASE)
_OBJECT_TAGS = {"drawing", "object", "pict", "altChunk"}


def _validate_package(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise DocumentParseError("resource_limit_exceeded")
            names = {item.filename for item in members}
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                raise DocumentParseError("invalid_document")
            total = 0
            for member in members:
                parts = PurePosixPath(member.filename).parts
                if member.filename.startswith("/") or ".." in parts:
                    raise DocumentParseError("invalid_document")
                if member.flag_bits & 0x1:
                    raise DocumentParseError("encrypted_document")
                total += member.file_size
                if (
                    member.file_size > MAX_DOCX_MEMBER_BYTES
                    or total > MAX_DOCX_UNCOMPRESSED_BYTES
                ):
                    raise DocumentParseError("resource_limit_exceeded")
                compressed = max(member.compress_size, 1)
                if member.file_size / compressed > MAX_DOCX_COMPRESSION_RATIO:
                    raise DocumentParseError("resource_limit_exceeded")
            return names
    except DocumentParseError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise DocumentParseError("invalid_document") from error


def _non_body_story_issues(
    path: Path, package_names: set[str]
) -> list[ParseCoverageIssue]:
    story_names = sorted(
        name
        for name in package_names
        if (
            name.startswith(("word/header", "word/footer"))
            and name.endswith(".xml")
        )
        or name in {
            "word/footnotes.xml",
            "word/endnotes.xml",
            "word/comments.xml",
        }
    )
    issues: list[ParseCoverageIssue] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for object_index, name in enumerate(story_names):
                root = ElementTree.fromstring(archive.read(name))
                meaningful = any(
                    str(element.tag).rsplit("}", 1)[-1]
                    in {"t", "tbl", "drawing", "object", "pict", "altChunk"}
                    and (element.text or "").strip()
                    for element in root.iter()
                ) or any(
                    str(element.tag).rsplit("}", 1)[-1]
                    in {"tbl", "drawing", "object", "pict", "altChunk"}
                    for element in root.iter()
                )
                if meaningful:
                    issues.append(
                        ParseCoverageIssue(
                            code="non_body_story_not_extracted",
                            section_path=f"${name}",
                            object_index=object_index,
                        )
                    )
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise DocumentParseError("invalid_document") from error
    return issues


def _heading_level(paragraph: Paragraph) -> int | None:
    style_name = paragraph.style.name if paragraph.style is not None else ""
    match = _HEADING_PATTERN.fullmatch(style_name)
    return int(match.group(1)) if match else None


def _object_codes(block: Any) -> list[str]:
    codes: list[str] = []
    for element in block._element.iter():
        local_name = str(element.tag).rsplit("}", 1)[-1]
        if local_name in _OBJECT_TAGS:
            code = (
                "embedded_object_not_extracted"
                if local_name in {"object", "altChunk"}
                else "image_not_extracted"
            )
            if code not in codes:
                codes.append(code)
    return codes


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [normalize_whitespace(cell.text) for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return " ".join(rows)


def parse_docx(path: Path) -> ParsedDocument:
    package_names = _validate_package(path)
    try:
        document = WordDocument(str(path))
    except (OSError, PackageNotFoundError, ValueError, zipfile.BadZipFile) as error:
        raise DocumentParseError("invalid_document") from error

    heading_stack: list[tuple[int, str]] = []
    groups: list[tuple[int, str | None, list[str]]] = []
    current_section_ordinal: int | None = None
    issues = _non_body_story_issues(path, package_names)
    extracted_characters = 0
    for object_index, block in enumerate(document.iter_inner_content()):
        if object_index >= MAX_DOCX_BLOCKS:
            raise DocumentParseError("resource_limit_exceeded")
        section_path = " > ".join(text for _, text in heading_stack) or None
        if isinstance(block, Paragraph):
            text = normalize_whitespace(block.text)
            level = _heading_level(block)
            if level is not None and text:
                heading_stack = [
                    (existing_level, heading)
                    for existing_level, heading in heading_stack
                    if existing_level < level
                ]
                heading_stack.append((level, text))
                section_path = " > ".join(
                    heading for _, heading in heading_stack
                )
                current_section_ordinal = len(groups) + 1
                groups.append((current_section_ordinal, section_path, []))
            object_codes = _object_codes(block)
        elif isinstance(block, Table):
            text = _table_text(block)
            object_codes = ["table_structure_not_preserved", *_object_codes(block)]
        else:
            continue
        if current_section_ordinal is None and (text or object_codes):
            current_section_ordinal = len(groups) + 1
            groups.append((current_section_ordinal, section_path, []))
        issue_location = section_path or "$document"
        for code in object_codes:
            issues.append(
                ParseCoverageIssue(
                    code=code,
                    section_path=issue_location,
                    section_ordinal=current_section_ordinal,
                    object_index=object_index,
                )
            )
        if not text:
            continue
        extracted_characters += len(text)
        if extracted_characters > MAX_EXTRACTED_TEXT_CHARACTERS:
            raise DocumentParseError("resource_limit_exceeded")
        groups[-1][2].append(text)

    chunks: list[ParsedChunk] = []
    parsed_sections: list[int] = []
    for section_ordinal, section_path, texts in groups:
        section_had_chunk = False
        for piece in chunk_text(" ".join(texts)):
            section_had_chunk = True
            chunks.append(
                ParsedChunk(
                    chunk_index=len(chunks),
                    page_number=None,
                    section_path=section_path,
                    section_ordinal=section_ordinal,
                    text=piece,
                )
            )
        if section_had_chunk:
            parsed_sections.append(section_ordinal)
    return ParsedDocument(
        chunks=chunks,
        coverage_issues=issues,
        total_sections=len(groups),
        parsed_sections=parsed_sections,
    )
