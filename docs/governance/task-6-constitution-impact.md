# Task 6 Constitution Impact Review

> Status: Implemented; pending independent re-review
>
> Date: 2026-09-18
>
> Scope: PDF and DOCX parsing with traceable evidence locations

## Decision

**Constitution impact: Yes.**

Task 6 creates the parsed text and location records that later requirement and evidence decisions will rely on. It therefore changes the implementation of Evidence and Coverage, although it does not give an LLM any new authority and does not call an LLM.

## Authority and data flow

The immutable uploaded `DocumentVersion` remains the source. Deterministic code verifies its configured storage root, byte length and SHA-256 digest before parsing. Parsers may produce only candidate text chunks, source locations and structured coverage limitations. They do not create Requirements, EvidenceLinks, Assessments or formal display statuses.

Parsed chunks are tied to one document version. PDF chunks retain a page number and never cross page boundaries. DOCX chunks retain only headings that actually occur in the document; they do not invent page numbers or missing headings. Every physical DOCX heading starts a new section with a stable one-based ordinal, so two different sections with the same displayed heading remain independently locatable. A DOCX with body content before any heading, including a document with no headings, treats that body as one physical section with a `None` display path and a stable ordinal. A newer parse attempt token prevents an older attempt from overwriting newer results.

## Coverage and failure gates

- PDF coverage separately records parsed, blank, failed and OCR-required pages.
- PDF attachment content is not parsed and is reported as `embedded_attachment_not_extracted`.
- This parser cannot prove that a PDF contains no table. It therefore reports `pdf_table_detection_unavailable` instead of claiming complete table coverage.
- DOCX reports total and successfully parsed body sections. Tables are flattened in document order and reported as `table_structure_not_preserved`.
- DOCX headers, footers, footnotes, endnotes and comments containing meaningful content are reported as `non_body_story_not_extracted`.
- Images and embedded objects are reported rather than silently treated as checked.
- A genuinely blank PDF page is distinct from a page that failed extraction. If all pages fail extraction, the result is `incomplete_coverage`, not `no_extractable_text`.
- Input size, page count, extracted text, archive expansion, archive member, PDF stream output, page-tree and form-invocation limits fail closed with a stable safe error code.
- Parsing occurs outside the database transaction. Successful replacement is atomic. A failed reparse retains the last successful snapshot. Pending, failed and expired parsing leases can be retried; completed and partial snapshots are not silently rerun.

## Verification

Regression tests cover real openable PDF and DOCX fixtures plus focused probes for Chinese headings, tables, scan/OCR classification, PDF attachments, images, headers and footers, resource limits, source integrity, old SQLite migration, concurrent parse attempts, stale lease recovery and successful-snapshot preservation.

The real local `backend/bidguard.db` is excluded from tests and must remain byte-for-byte unchanged during development verification. No OpenAI API call or API key access is part of Task 6.

## Remaining bounded limitations

Task 6 does not perform OCR, reconstruct PDF or DOCX table geometry, extract embedded attachments, or parse non-body DOCX stories. These limitations are represented in persisted Coverage and force an incomplete or partial result where relevant. Later stages must not treat such a result as proof that the omitted material was checked.
