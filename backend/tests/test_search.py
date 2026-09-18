from __future__ import annotations

import pytest

from app.documents.search import (
    SearchResult,
    SearchScopeError,
    _coverage_complete,
    overlap_score,
    search_chunks,
    tokenize,
)
from app.persistence.models import BidProject, Document, DocumentChunk, DocumentVersion


def _version(
    project: BidProject,
    *,
    role: str,
    number: int,
    document: Document | None = None,
    status: str = "parsed",
    chunks: list[tuple[str, int | None, str | None]] | None = None,
) -> DocumentVersion:
    if document is None:
        document = Document(
            project=project,
            role=role,
            display_name=f"{role}-{number}.pdf",
        )
    version = DocumentVersion(
        document=document,
        version_number=number,
        sha256=f"{number:064x}",
        storage_path=f"/tmp/{role}-{number}.pdf",
        parse_status=status,
    )
    version.chunks = [
        DocumentChunk(
            chunk_index=index,
            text=text,
            page_number=page,
            section_path=section,
        )
        for index, (text, page, section) in enumerate(chunks or [])
    ]
    return version


def test_tokenize_normalizes_unicode_case_and_keeps_chinese_phrases() -> None:
    assert tokenize("  信息系统项目管理师  PROJECT-MANAGER  ") == frozenset(
        {"信息系统项目管理师", "project", "manager"}
    )


def test_overlap_score_is_stable_and_empty_query_is_zero() -> None:
    assert overlap_score("项目经理 项目经理", "项目经理负责交付") == 1.0
    assert overlap_score("   ", "项目经理负责交付") == 0.0
    assert overlap_score("项目经理 证书", "项目经理负责交付") == 0.5


def test_search_returns_traceable_ranked_results_and_role_filter(db_session) -> None:
    project = BidProject(name="Search project")
    db_session.add(project)
    tender = _version(
        project,
        role="tender",
        number=1,
        chunks=[
            ("项目经理需要具备信息系统项目管理师资格。", 3, "资格条件"),
            ("公司注册资本满足要求。", 4, "商务条件"),
        ],
    )
    proposal = _version(
        project,
        role="proposal",
        number=1,
        chunks=[("项目经理由张三担任。", 2, "项目团队")],
    )
    db_session.add_all([tender, proposal])
    db_session.commit()

    results = search_chunks(
        db_session,
        "信息系统项目管理师 项目经理",
        project_id=project.id,
        allowed_document_version_ids=[tender.id],
        document_roles=["tender"],
        limit=10,
    )

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, SearchResult)
    assert result.document_version_id == tender.id
    assert result.version_number == 1
    assert result.document_role == "tender"
    assert result.page_number == 3
    assert result.section_path == "资格条件"
    assert result.chunk_index == 0
    assert result.score == 1.0
    assert result.parse_status == "parsed"


def test_search_is_strictly_scoped_to_allowed_versions_and_does_not_infer_latest(
    db_session,
) -> None:
    project = BidProject(name="Version project")
    db_session.add(project)
    document = Document(project=project, role="proposal", display_name="proposal.pdf")
    old = _version(
        project,
        role="proposal",
        number=1,
        document=document,
        chunks=[("交付周期十二个月。", 1, "旧版本")],
    )
    current = _version(
        project,
        role="proposal",
        number=2,
        document=document,
        chunks=[("交付周期十八个月。", 1, "当前版本")],
    )
    db_session.add_all([old, current])
    db_session.commit()

    old_results = search_chunks(
        db_session,
        "交付周期",
        project_id=project.id,
        allowed_document_version_ids=[old.id],
    )
    assert [item.document_version_id for item in old_results] == [old.id]

    current_results = search_chunks(
        db_session,
        "交付周期",
        project_id=project.id,
        allowed_document_version_ids=[current.id],
    )
    assert [item.document_version_id for item in current_results] == [current.id]


def test_search_excludes_unparsed_versions_and_marks_partial_coverage(db_session) -> None:
    project = BidProject(name="Coverage project")
    db_session.add(project)
    document = Document(project=project, role="tender", display_name="tender.pdf")
    pending = _version(
        project,
        role="tender",
        number=1,
        document=document,
        status="pending",
        chunks=[("项目经理要求。", 1, "要求")],
    )
    partial = _version(
        project,
        role="tender",
        number=2,
        document=document,
        status="partial_failure",
        chunks=[("项目经理要求。", 1, "要求")],
    )
    failed = _version(
        project,
        role="tender",
        number=3,
        document=document,
        status="failed",
        chunks=[("项目经理要求。", 1, "要求")],
    )
    db_session.add_all([pending, partial, failed])
    db_session.commit()

    results = search_chunks(
        db_session,
        "项目经理",
        project_id=project.id,
        allowed_document_version_ids=[pending.id, partial.id, failed.id],
    )

    assert [item.document_version_id for item in results] == [partial.id]
    assert results[0].coverage_complete is False
    assert results[0].parse_status == "partial_failure"


def test_search_discards_zero_scores_applies_limit_and_has_stable_ties(db_session) -> None:
    project = BidProject(name="Limit project")
    db_session.add(project)
    version = _version(
        project,
        role="proposal",
        number=1,
        chunks=[
            ("项目经理在第一页。", 1, "A"),
            ("项目经理在第二页。", 2, "B"),
            ("完全无关的内容。", 3, "C"),
        ],
    )
    db_session.add(version)
    db_session.commit()

    first = search_chunks(
        db_session,
        "项目经理",
        project_id=project.id,
        allowed_document_version_ids=[version.id],
        limit=1,
    )
    second = search_chunks(
        db_session,
        "项目经理",
        project_id=project.id,
        allowed_document_version_ids=[version.id],
        limit=10,
    )

    assert len(first) == 1
    assert [item.chunk_index for item in second] == [0, 1]
    assert all(item.score > 0 for item in second)
    assert second[0].score == second[1].score


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_search_rejects_invalid_limit(db_session, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        search_chunks(
            db_session,
            "项目经理",
            project_id=1,
            allowed_document_version_ids=[],
            limit=limit,
        )


def test_search_empty_query_and_empty_scope_do_not_read_documents(db_session) -> None:
    with pytest.raises(SearchScopeError):
        search_chunks(
            db_session,
            "   ",
            project_id=1,
            allowed_document_version_ids=[999],
        )
    assert search_chunks(
        db_session,
        "项目经理",
        project_id=1,
        allowed_document_version_ids=[],
    ) == []


def test_search_rejects_missing_and_cross_project_version_ids(db_session) -> None:
    first_project = BidProject(name="First project")
    second_project = BidProject(name="Second project")
    db_session.add_all([first_project, second_project])
    first_version = _version(
        first_project,
        role="proposal",
        number=1,
        chunks=[("项目经理。", 1, "团队")],
    )
    second_version = _version(
        second_project,
        role="proposal",
        number=1,
        chunks=[("项目经理。", 1, "团队")],
    )
    db_session.add_all([first_version, second_version])
    db_session.commit()

    with pytest.raises(SearchScopeError) as cross_project:
        search_chunks(
            db_session,
            "项目经理",
            project_id=first_project.id,
            allowed_document_version_ids=[second_version.id],
        )
    assert cross_project.value.code == "search_scope_invalid"

    with pytest.raises(SearchScopeError) as missing:
        search_chunks(
            db_session,
            "项目经理",
            project_id=first_project.id,
            allowed_document_version_ids=[first_version.id, 99999],
        )
    assert missing.value.code == "search_scope_invalid"


def test_search_coverage_is_defensive_and_blank_pages_are_complete(db_session) -> None:
    project = BidProject(name="Coverage semantics project")
    db_session.add(project)
    document = Document(project=project, role="tender", display_name="tender.pdf")
    complete = _version(
        project,
        role="tender",
        number=1,
        document=document,
        chunks=[("项目经理要求。", 1, "要求")],
    )
    complete.parse_coverage = {
        "total_pages": 2,
        "total_sections": None,
        "parsed_pages": [1],
        "blank_pages": [2],
        "failed_pages": [],
        "ocr_pages": [],
        "coverage_issues": [{"code": "blank_page", "page_number": 2}],
        "needs_ocr": False,
    }
    failed_coverage = _version(
        project,
        role="tender",
        number=2,
        document=document,
        chunks=[("项目经理要求。", 1, "要求")],
    )
    failed_coverage.parse_coverage = {
        "total_pages": 2,
        "total_sections": None,
        "parsed_pages": [1],
        "blank_pages": [],
        "coverage_issues": [],
        "failed_pages": [2],
        "ocr_pages": [],
        "needs_ocr": False,
    }
    missing_coverage = _version(
        project,
        role="tender",
        number=3,
        document=document,
        chunks=[("项目经理要求。", 1, "要求")],
    )
    partial = _version(
        project,
        role="tender",
        number=4,
        document=document,
        status="partial_failure",
        chunks=[("项目经理要求。", 1, "要求")],
    )
    partial.parse_coverage = {
        "total_pages": 2,
        "total_sections": None,
        "parsed_pages": [1],
        "blank_pages": [],
        "failed_pages": [],
        "ocr_pages": [2],
        "coverage_issues": [{"code": "unrecognized_table", "page_number": 2}],
        "needs_ocr": True,
    }
    db_session.add_all([complete, failed_coverage, missing_coverage, partial])
    db_session.commit()

    results = search_chunks(
        db_session,
        "项目经理",
        project_id=project.id,
        allowed_document_version_ids=[
            complete.id,
            failed_coverage.id,
            missing_coverage.id,
            partial.id,
        ],
    )

    by_version = {result.document_version_id: result for result in results}
    assert by_version[complete.id].coverage_complete is True
    assert by_version[failed_coverage.id].coverage_complete is False
    assert by_version[missing_coverage.id].coverage_complete is False
    assert by_version[partial.id].coverage_complete is False


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        [],
        {"total_pages": 1, "coverage_issues": [], "needs_ocr": False},
        {
            "total_pages": 1,
            "parsed_pages": "1",
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": False,
        },
        {
            "total_pages": 1,
            "parsed_pages": [True],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": False,
        },
        {
            "total_pages": 1,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [{"code": "", "page_number": 1}],
            "needs_ocr": False,
        },
        {
            "total_pages": 1,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [{"code": "blank_page"}],
            "needs_ocr": False,
        },
        {
            "total_pages": 1,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [
                {"code": "blank_page", "page_number": 1, "section_ordinal": 2}
            ],
            "needs_ocr": False,
        },
        {
            "total_pages": 1,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": True,
        },
        {
            "total_pages": 2,
            "parsed_pages": [1],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": False,
        },
        {
            "total_sections": 1,
            "parsed_sections": [2],
            "coverage_issues": [],
            "needs_ocr": False,
        },
        {
            "total_sections": 1,
            "parsed_sections": [1],
            "coverage_issues": [{"code": "blank_page", "section_ordinal": 2}],
            "needs_ocr": False,
        },
    ],
)
def test_malformed_coverage_never_claims_completeness(coverage: object) -> None:
    assert _coverage_complete("parsed", coverage) is False


def test_valid_docx_section_coverage_can_be_complete() -> None:
    assert _coverage_complete(
        "parsed",
        {
            "total_pages": None,
            "total_sections": 2,
            "parsed_sections": [1, 2],
            "parsed_pages": [],
            "blank_pages": [],
            "failed_pages": [],
            "ocr_pages": [],
            "coverage_issues": [],
            "needs_ocr": False,
        },
    ) is True
