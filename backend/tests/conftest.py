from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool

from app.db import Base, GuardedSession, build_engine
from app.persistence import models as _models  # noqa: F401


@pytest.fixture
def db_session() -> Iterator[GuardedSession]:
    engine = build_engine(
        "sqlite://",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with GuardedSession(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
