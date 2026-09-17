from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, create_engine, event
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool
from sqlalchemy.types import TypeDecorator

from app.settings import get_settings

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        normalized = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(
        self,
        value: datetime | None,
        _dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    supports_autocommit = hasattr(dbapi_connection, "autocommit")
    previous_transaction_mode = (
        dbapi_connection.autocommit
        if supports_autocommit
        else dbapi_connection.isolation_level
    )
    try:
        if supports_autocommit:
            dbapi_connection.autocommit = True
        else:
            dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
    finally:
        if supports_autocommit:
            dbapi_connection.autocommit = previous_transaction_mode
        else:
            dbapi_connection.isolation_level = previous_transaction_mode


def build_engine(
    database_url: str | None = None,
    *,
    poolclass: type[Pool] | None = None,
) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine_options: dict[str, Any] = {"connect_args": connect_args}
    if poolclass is not None:
        engine_options["poolclass"] = poolclass
    configured_engine = create_engine(url, **engine_options)
    if configured_engine.dialect.name == "sqlite":
        event.listen(configured_engine, "connect", _enable_sqlite_foreign_keys)
    return configured_engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:  # noqa: UP043
    with SessionLocal() as session:
        yield session
