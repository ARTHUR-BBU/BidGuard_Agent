from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db import Base


class SchemaMigrationError(RuntimeError):
    """Raised when an old local database cannot be upgraded without data loss."""


_IDENTITY_INSERT_TRIGGER = "trg_documents_identity_insert"
_IDENTITY_UPDATE_TRIGGER = "trg_documents_identity_update"
_VERSION_SIZE_INSERT_TRIGGER = "trg_document_versions_size_insert"
_VERSION_SIZE_UPDATE_TRIGGER = "trg_document_versions_size_update"
_CHUNK_CONTENT_INSERT_TRIGGER = "trg_document_chunks_content_insert"
_CHUNK_CONTENT_UPDATE_TRIGGER = "trg_document_chunks_content_update"


def _row_ids(connection: Connection, statement: str) -> list[int]:
    return [int(row[0]) for row in connection.exec_driver_sql(statement).all()]


def _format_ids(ids: Sequence[int]) -> str:
    return ", ".join(str(item) for item in ids[:10])


def _stop(reason: str) -> None:
    raise SchemaMigrationError(
        "BidGuard could not safely upgrade the local database: "
        f"{reason}. No document rows were deleted or merged. "
        "Resolve the listed records or restore a valid backup, then start again."
    )


def _unique_column_sets(connection: Connection, table: str) -> set[tuple[str, ...]]:
    inspector = inspect(connection)
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


def _has_current_identity_check(connection: Connection) -> bool:
    for constraint in inspect(connection).get_check_constraints("documents"):
        sql = " ".join(str(constraint.get("sqltext", "")).lower().split())
        if (
            "company_content_sha256" in sql
            and "role = 'company'" in sql
            and "'tender'" in sql
            and "'proposal'" in sql
        ):
            return True
    return False


def _has_check_fragments(
    connection: Connection, table: str, fragments: Sequence[str]
) -> bool:
    normalized = [fragment.lower() for fragment in fragments]
    return any(
        all(fragment in sql for fragment in normalized)
        for constraint in inspect(connection).get_check_constraints(table)
        if (sql := " ".join(str(constraint.get("sqltext", "")).lower().split()))
    )


def _trigger_names(connection: Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
        )
    }


def _migration_needed(connection: Connection) -> bool:
    document_columns = {
        str(column["name"])
        for column in inspect(connection).get_columns("documents")
    }
    if "company_content_sha256" not in document_columns:
        return True
    version_columns = {
        str(column["name"])
        for column in inspect(connection).get_columns("document_versions")
    }
    if not {
        "size_bytes",
        "parse_error_code",
        "parse_error",
        "parse_coverage",
        "parse_attempt_id",
        "parse_attempt_started_at",
    }.issubset(version_columns):
        return True

    document_uniques = _unique_column_sets(connection, "documents")
    version_uniques = _unique_column_sets(connection, "document_versions")
    trigger_names = _trigger_names(connection)
    identity_is_enforced = _has_current_identity_check(connection) or {
        _IDENTITY_INSERT_TRIGGER,
        _IDENTITY_UPDATE_TRIGGER,
    }.issubset(trigger_names)
    version_size_is_enforced = _has_check_fragments(
        connection, "document_versions", ("size_bytes", "> 0")
    ) or {_VERSION_SIZE_INSERT_TRIGGER, _VERSION_SIZE_UPDATE_TRIGGER}.issubset(
        trigger_names
    )
    chunk_content_is_enforced = (
        _has_check_fragments(connection, "document_chunks", ("chunk_index", ">= 0"))
        and _has_check_fragments(connection, "document_chunks", ("trim(text)", "> 0"))
    ) or {_CHUNK_CONTENT_INSERT_TRIGGER, _CHUNK_CONTENT_UPDATE_TRIGGER}.issubset(
        trigger_names
    )
    return not (
        ("project_id", "role") in document_uniques
        and ("company_content_sha256",) in document_uniques
        and ("document_id", "sha256") in version_uniques
        and identity_is_enforced
        and version_size_is_enforced
        and chunk_content_is_enforced
    )


def _validate_legacy_data(connection: Connection, *, has_digest_column: bool) -> None:
    invalid_identity_ids = _row_ids(
        connection,
        """
        SELECT id
        FROM documents
        WHERE CASE
            WHEN role = 'company' AND project_id IS NULL THEN 0
            WHEN role IN ('tender', 'proposal') AND project_id IS NOT NULL THEN 0
            ELSE 1
        END = 1
        ORDER BY id
        LIMIT 11
        """,
    )
    if invalid_identity_ids:
        _stop(
            "invalid document identities at document ids "
            f"{_format_ids(invalid_identity_ids)}"
        )

    if has_digest_column:
        project_digest_ids = _row_ids(
            connection,
            """
            SELECT id
            FROM documents
            WHERE role IN ('tender', 'proposal')
              AND company_content_sha256 IS NOT NULL
            ORDER BY id
            LIMIT 11
            """,
        )
        if project_digest_ids:
            _stop(
                "project documents unexpectedly contain company content digests at "
                f"document ids {_format_ids(project_digest_ids)}"
            )

    duplicate_project_rows = connection.exec_driver_sql(
        """
        SELECT project_id, role
        FROM documents
        WHERE project_id IS NOT NULL
        GROUP BY project_id, role
        HAVING count(*) > 1
        ORDER BY project_id, role
        LIMIT 11
        """
    ).all()
    if duplicate_project_rows:
        conflicts = ", ".join(
            f"project {row[0]} / {row[1]}" for row in duplicate_project_rows[:10]
        )
        _stop(f"duplicate project document roles: {conflicts}")

    invalid_company_versions = connection.exec_driver_sql(
        """
        SELECT d.id, count(v.id)
        FROM documents AS d
        LEFT JOIN document_versions AS v ON v.document_id = d.id
        WHERE d.role = 'company'
        GROUP BY d.id
        HAVING count(v.id) <> 1
        ORDER BY d.id
        LIMIT 11
        """
    ).all()
    if invalid_company_versions:
        details = ", ".join(
            f"document {row[0]} has {row[1]}" for row in invalid_company_versions[:10]
        )
        _stop(
            "each legacy company document must have exactly one document version; "
            f"{details}"
        )

    duplicate_company_rows = connection.exec_driver_sql(
        """
        SELECT v.sha256
        FROM documents AS d
        JOIN document_versions AS v ON v.document_id = d.id
        WHERE d.role = 'company'
        GROUP BY v.sha256
        HAVING count(DISTINCT d.id) > 1
        ORDER BY v.sha256
        LIMIT 11
        """
    ).all()
    if duplicate_company_rows:
        digests = ", ".join(str(row[0])[:12] for row in duplicate_company_rows[:10])
        _stop(f"duplicate company document content digests: {digests}")

    duplicate_version_rows = connection.exec_driver_sql(
        """
        SELECT document_id, sha256
        FROM document_versions
        GROUP BY document_id, sha256
        HAVING count(*) > 1
        ORDER BY document_id, sha256
        LIMIT 11
        """
    ).all()
    if duplicate_version_rows:
        conflicts = ", ".join(
            f"document {row[0]} / {str(row[1])[:12]}"
            for row in duplicate_version_rows[:10]
        )
        _stop(f"duplicate document version content: {conflicts}")

    if has_digest_column:
        mismatched_company_ids = _row_ids(
            connection,
            """
            SELECT d.id
            FROM documents AS d
            JOIN document_versions AS v ON v.document_id = d.id
            WHERE d.role = 'company'
              AND d.company_content_sha256 IS NOT NULL
              AND d.company_content_sha256 <> v.sha256
            ORDER BY d.id
            LIMIT 11
            """,
        )
        if mismatched_company_ids:
            _stop(
                "company content digest does not match its document version at "
                f"document ids {_format_ids(mismatched_company_ids)}"
            )


def _install_identity_triggers(connection: Connection) -> None:
    valid_identity = """
        (NEW.role = 'company'
            AND NEW.project_id IS NULL
            AND NEW.company_content_sha256 IS NOT NULL)
        OR
        (NEW.role IN ('tender', 'proposal')
            AND NEW.project_id IS NOT NULL
            AND NEW.company_content_sha256 IS NULL)
    """
    for operation, trigger_name in (
        ("INSERT", _IDENTITY_INSERT_TRIGGER),
        ("UPDATE", _IDENTITY_UPDATE_TRIGGER),
    ):
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {trigger_name}
            BEFORE {operation} ON documents
            FOR EACH ROW
            WHEN COALESCE(({valid_identity}), 0) = 0
            BEGIN
                SELECT RAISE(ABORT, 'valid document identity required');
            END
            """
        )


def _install_parser_integrity_triggers(connection: Connection) -> None:
    trigger_specs = (
        (
            "document_versions",
            _VERSION_SIZE_INSERT_TRIGGER,
            _VERSION_SIZE_UPDATE_TRIGGER,
            "NEW.size_bytes IS NOT NULL AND NEW.size_bytes <= 0",
            "document version size must be positive",
        ),
        (
            "document_chunks",
            _CHUNK_CONTENT_INSERT_TRIGGER,
            _CHUNK_CONTENT_UPDATE_TRIGGER,
            "NEW.chunk_index < 0 OR length(trim(NEW.text)) = 0",
            "valid document chunk content required",
        ),
    )
    for table, insert_name, update_name, invalid_when, message in trigger_specs:
        for operation, trigger_name in (
            ("INSERT", insert_name),
            ("UPDATE", update_name),
        ):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS {trigger_name}
                BEFORE {operation} ON {table}
                FOR EACH ROW
                WHEN {invalid_when}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )


def _upgrade_legacy_sqlite(connection: Connection) -> None:
    if not _migration_needed(connection):
        return

    columns = {
        str(column["name"])
        for column in inspect(connection).get_columns("documents")
    }
    has_digest_column = "company_content_sha256" in columns
    _validate_legacy_data(connection, has_digest_column=has_digest_column)

    if not has_digest_column:
        connection.exec_driver_sql(
            "ALTER TABLE documents ADD COLUMN company_content_sha256 VARCHAR(64)"
        )

    version_columns = {
        str(column["name"])
        for column in inspect(connection).get_columns("document_versions")
    }
    missing_version_columns = {
        "size_bytes": "INTEGER",
        "parse_error_code": "VARCHAR(80)",
        "parse_error": "TEXT",
        "parse_coverage": "JSON",
        "parse_attempt_id": "VARCHAR(36)",
        "parse_attempt_started_at": "DATETIME",
    }
    for column_name, column_type in missing_version_columns.items():
        if column_name not in version_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE document_versions ADD COLUMN {column_name} {column_type}"
            )

    connection.exec_driver_sql(
        """
        UPDATE documents
        SET company_content_sha256 = (
            SELECT v.sha256
            FROM document_versions AS v
            WHERE v.document_id = documents.id
        )
        WHERE role = 'company' AND company_content_sha256 IS NULL
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_project_role_migrated
        ON documents (project_id, role)
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_company_content_migrated
        ON documents (company_content_sha256)
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_document_versions_digest_migrated
        ON document_versions (document_id, sha256)
        """
    )
    if not _has_current_identity_check(connection):
        _install_identity_triggers(connection)
    _install_parser_integrity_triggers(connection)

    if _migration_needed(connection):
        _stop("the upgraded schema did not pass its post-migration verification")


def ensure_schema(engine: Engine) -> None:
    """Create the current schema and safely upgrade the one supported SQLite legacy.

    PostgreSQL and other server databases only use SQLAlchemy's normal schema
    bootstrap. The lightweight migration exists solely for the local SQLite MVP.
    """

    if engine.dialect.name != "sqlite":
        Base.metadata.create_all(engine)
        return

    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(connection)
            _upgrade_legacy_sqlite(connection)
            connection.commit()
        except SchemaMigrationError:
            connection.rollback()
            raise
        except SQLAlchemyError as exc:
            connection.rollback()
            raise SchemaMigrationError(
                "BidGuard could not safely upgrade the local database because "
                f"SQLite reported: {exc}. No document rows were intentionally "
                "deleted or merged; restore a valid backup or resolve the schema "
                "conflict before restarting."
            ) from exc
