from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, build_engine
from app.persistence import models as _models  # noqa: F401


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = build_engine(
        "sqlite://",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
