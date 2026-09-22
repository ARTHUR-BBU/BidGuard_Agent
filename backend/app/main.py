from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.documents import router as documents_router
from .api.health import router as health_router
from .api.projects import router as projects_router
from .db import engine
from .middleware.body_limit import UploadBodyLimitMiddleware
from .persistence import models as _models  # noqa: F401
from .persistence.schema import ensure_schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_schema(engine)
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="BidGuard API", lifespan=lifespan)
    application.add_middleware(UploadBodyLimitMiddleware)
    application.include_router(health_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    application.include_router(documents_router, prefix="/api")
    return application


app = create_app()
