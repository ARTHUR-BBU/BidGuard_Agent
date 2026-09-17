from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.health import router as health_router
from .db import Base, engine
from .persistence import models as _models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="BidGuard API", lifespan=lifespan)
    application.include_router(health_router, prefix="/api")
    return application


app = create_app()
