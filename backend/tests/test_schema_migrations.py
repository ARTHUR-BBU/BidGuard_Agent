from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.main import create_app
from app.persistence.schema import SchemaMigrationError, ensure_schema


def _legacy_engine(tmp_path: Path) -> Iterator[Engine]:
    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE bid_projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR(300) NOT NULL,
                deadline_at DATETIME,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                project_id INTEGER REFERENCES bid_projects(id) ON DELETE CASCADE,
                role VARCHAR(30) NOT NULL,
                display_name VARCHAR(500) NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT ck_documents_project_or_company
                    CHECK (project_id IS NOT NULL OR role = 'company')
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE document_versions (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL
                    REFERENCES documents(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                storage_path VARCHAR(1000) NOT NULL,
                parse_status VARCHAR(40) NOT NULL,
                uploaded_at DATETIME NOT NULL,
                CONSTRAINT uq_document_versions_number
                    UNIQUE (document_id, version_number),
                CONSTRAINT ck_document_versions_number_positive
                    CHECK (version_number >= 1)
            )
            """
        )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def legacy_engine(tmp_path: Path) -> Iterator[Engine]:
    yield from _legacy_engine(tmp_path)


def _insert_project(connection: Connection, project_id: int = 1) -> None:
    connection.exec_driver_sql(
        "INSERT INTO bid_projects (id, name, created_at) VALUES (?, ?, ?)",
        (project_id, "数据治理平台", "2030-01-01 00:00:00"),
    )


def _insert_document(
    connection: Connection,
    *,
    document_id: int,
    project_id: int | None,
    role: str,
) -> None:
    connection.exec_driver_sql(
        """
        INSERT INTO documents (id, project_id, role, display_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            document_id,
            project_id,
            role,
            f"document-{document_id}.pdf",
            "2030-01-01 00:00:00",
        ),
    )


def _insert_version(
    connection: Connection,
    *,
    version_id: int,
    document_id: int,
    version_number: int,
    digest: str,
) -> None:
    connection.exec_driver_sql(
        """
        INSERT INTO document_versions (
            id, document_id, version_number, sha256, storage_path,
            parse_status, uploaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            document_id,
            version_number,
            digest,
            f"/tmp/{digest}.pdf",
            "pending",
            "2030-01-01 00:00:00",
        ),
    )


def _unique_column_sets(engine: Engine, table: str) -> set[tuple[str, ...]]:
    inspector = inspect(engine)
    constraints = {
        tuple(str(column) for column in item["column_names"])
        for item in inspector.get_unique_constraints(table)
    }
    indexes = {
        tuple(str(column) for column in item["column_names"])
        for item in inspector.get_indexes(table)
        if item["unique"]
    }
    return constraints | indexes


def _schema_snapshot(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(
            connection.exec_driver_sql(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).tuples()
        )


def test_empty_legacy_schema_is_upgraded_and_migration_is_idempotent(
    legacy_engine: Engine,
) -> None:
    ensure_schema(legacy_engine)

    assert "company_content_sha256" in {
        column["name"] for column in inspect(legacy_engine).get_columns("documents")
    }
    version_columns = {
        column["name"]
        for column in inspect(legacy_engine).get_columns("document_versions")
    }
    assert {
        "size_bytes",
        "parse_error_code",
        "parse_error",
        "parse_coverage",
        "parse_attempt_id",
        "parse_attempt_started_at",
    }.issubset(version_columns)
    assert "section_ordinal" in {
        column["name"]
        for column in inspect(legacy_engine).get_columns("document_chunks")
    }
    assert ("project_id", "role") in _unique_column_sets(legacy_engine, "documents")
    assert ("company_content_sha256",) in _unique_column_sets(
        legacy_engine, "documents"
    )
    assert ("document_id", "sha256") in _unique_column_sets(
        legacy_engine, "document_versions"
    )

    first_snapshot = _schema_snapshot(legacy_engine)
    ensure_schema(legacy_engine)
    assert _schema_snapshot(legacy_engine) == first_snapshot

    with legacy_engine.begin() as connection:
        _insert_project(connection)
        _insert_document(connection, document_id=1, project_id=1, role="tender")
        _insert_version(
            connection,
            version_id=1,
            document_id=1,
            version_number=1,
            digest="a" * 64,
        )
    with pytest.raises(IntegrityError), legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE document_versions SET size_bytes = 0 WHERE id = 1"
        )
    with pytest.raises(IntegrityError), legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO document_chunks (
                document_version_id, chunk_index, text
            ) VALUES (1, -1, '')
            """
        )


def test_valid_legacy_data_is_preserved_and_company_digest_is_backfilled(
    legacy_engine: Engine,
) -> None:
    company_digest = "a" * 64
    with legacy_engine.begin() as connection:
        _insert_project(connection)
        _insert_document(
            connection, document_id=1, project_id=1, role="tender"
        )
        _insert_document(
            connection, document_id=2, project_id=None, role="company"
        )
        _insert_version(
            connection,
            version_id=1,
            document_id=1,
            version_number=1,
            digest="b" * 64,
        )
        _insert_version(
            connection,
            version_id=2,
            document_id=2,
            version_number=1,
            digest=company_digest,
        )

    ensure_schema(legacy_engine)

    with legacy_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, project_id, role, company_content_sha256 "
                "FROM documents ORDER BY id"
            )
        ).all()
        version_count = connection.scalar(
            text("SELECT count(*) FROM document_versions")
        )

    assert rows == [
        (1, 1, "tender", None),
        (2, None, "company", company_digest),
    ]
    assert version_count == 2


def test_migrated_legacy_schema_enforces_document_identity_on_write(
    legacy_engine: Engine,
) -> None:
    ensure_schema(legacy_engine)

    with legacy_engine.begin() as connection:
        _insert_project(connection)
        connection.exec_driver_sql(
            """
            INSERT INTO documents (
                id, project_id, role, company_content_sha256,
                display_name, created_at
            ) VALUES (1, 1, 'tender', NULL, 'tender.pdf', '2030-01-01 00:00:00')
            """
        )

    with (
        legacy_engine.begin() as connection,
        pytest.raises(IntegrityError, match="valid document identity"),
    ):
        connection.exec_driver_sql(
            """
            INSERT INTO documents (
                project_id, role, company_content_sha256, display_name, created_at
            ) VALUES (1, 'company', ?, 'invalid.pdf', '2030-01-01 00:00:00')
            """,
            ("c" * 64,),
        )

    with (
        legacy_engine.begin() as connection,
        pytest.raises(IntegrityError, match="valid document identity"),
    ):
        connection.exec_driver_sql(
            "UPDATE documents SET role = 'company' WHERE id = 1"
        )

    with (
        legacy_engine.begin() as connection,
        pytest.raises(IntegrityError, match="UNIQUE constraint failed"),
    ):
        connection.exec_driver_sql(
            """
            INSERT INTO documents (
                project_id, role, company_content_sha256,
                display_name, created_at
            ) VALUES (1, 'tender', NULL, 'duplicate.pdf', '2030-01-01 00:00:00')
            """
        )

    with legacy_engine.begin() as connection:
        _insert_version(
            connection,
            version_id=1,
            document_id=1,
            version_number=1,
            digest="f" * 64,
        )
    with (
        legacy_engine.begin() as connection,
        pytest.raises(IntegrityError, match="UNIQUE constraint failed"),
    ):
        _insert_version(
            connection,
            version_id=2,
            document_id=1,
            version_number=2,
            digest="f" * 64,
        )


@pytest.mark.parametrize(
    ("prepare", "expected_message"),
    [
        ("duplicate_project_role", "duplicate project document roles"),
        ("duplicate_company_content", "duplicate company document content"),
        ("duplicate_version_digest", "duplicate document version content"),
        ("missing_company_version", "exactly one document version"),
        ("ambiguous_company_versions", "exactly one document version"),
        ("invalid_identity", "invalid document identities"),
    ],
)
def test_legacy_conflicts_stop_startup_without_deleting_or_merging_rows(
    legacy_engine: Engine,
    prepare: str,
    expected_message: str,
) -> None:
    with legacy_engine.begin() as connection:
        _insert_project(connection)
        if prepare == "duplicate_project_role":
            _insert_document(connection, document_id=1, project_id=1, role="tender")
            _insert_document(connection, document_id=2, project_id=1, role="tender")
        elif prepare == "duplicate_company_content":
            for document_id in (1, 2):
                _insert_document(
                    connection,
                    document_id=document_id,
                    project_id=None,
                    role="company",
                )
                _insert_version(
                    connection,
                    version_id=document_id,
                    document_id=document_id,
                    version_number=1,
                    digest="d" * 64,
                )
        elif prepare == "duplicate_version_digest":
            _insert_document(connection, document_id=1, project_id=1, role="tender")
            for version_id in (1, 2):
                _insert_version(
                    connection,
                    version_id=version_id,
                    document_id=1,
                    version_number=version_id,
                    digest="e" * 64,
                )
        elif prepare == "missing_company_version":
            _insert_document(
                connection, document_id=1, project_id=None, role="company"
            )
        elif prepare == "ambiguous_company_versions":
            _insert_document(
                connection, document_id=1, project_id=None, role="company"
            )
            for version_id in (1, 2):
                _insert_version(
                    connection,
                    version_id=version_id,
                    document_id=1,
                    version_number=version_id,
                    digest=str(version_id) * 64,
                )
        else:
            _insert_document(connection, document_id=1, project_id=1, role="company")

    with pytest.raises(SchemaMigrationError, match=expected_message):
        ensure_schema(legacy_engine)

    with legacy_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM documents")) >= 1
        assert "company_content_sha256" not in {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(documents)")
        }


def test_current_schema_is_left_unchanged(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    try:
        ensure_schema(engine)
        first_snapshot = _schema_snapshot(engine)

        ensure_schema(engine)

        assert _schema_snapshot(engine) == first_snapshot
        assert inspect(engine).get_table_names() != []
    finally:
        engine.dispose()


def test_application_lifespan_upgrades_a_legacy_database(
    legacy_engine: Engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.main.engine", legacy_engine)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200

    assert "company_content_sha256" in {
        column["name"] for column in inspect(legacy_engine).get_columns("documents")
    }


def test_non_sqlite_bootstrap_does_not_run_sqlite_migration_sql(monkeypatch) -> None:
    calls: list[object] = []

    class DialectStub:
        name = "postgresql"

    class EngineStub:
        dialect = DialectStub()

    fake_engine = EngineStub()
    monkeypatch.setattr(
        Base.metadata,
        "create_all",
        lambda bind: calls.append(bind),
    )

    ensure_schema(fake_engine)  # type: ignore[arg-type]

    assert calls == [fake_engine]
