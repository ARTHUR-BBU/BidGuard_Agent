import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from .api.decisions import router as decisions_router
from .api.documents import router as documents_router
from .api.health import router as health_router
from .api.projects import router as projects_router
from .api.reviews import router as reviews_router
from .db import GuardedSession, engine
from .jobs.worker import ReviewWorker
from .middleware.body_limit import UploadBodyLimitMiddleware
from .persistence import models as _models  # noqa: F401
from .persistence.schema import ensure_schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_schema(engine)
    # Build the worker's session factory from the active engine at startup.
    # Tests (and embedded deployments) can replace ``app.main.engine`` before
    # creating the app; resolving the factory here keeps the worker on that
    # same database instead of accidentally using the module-global default.
    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=GuardedSession,
    )
    worker = ReviewWorker(session_factory)
    worker.recover_interrupted_jobs()
    worker_task = asyncio.create_task(worker.run_forever())
    try:
        yield
    finally:
        worker.request_stop()
        await worker_task


def create_app() -> FastAPI:
    application = FastAPI(title="BidGuard API", lifespan=lifespan)
    application.add_middleware(UploadBodyLimitMiddleware)
    application.include_router(health_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    application.include_router(documents_router, prefix="/api")
    application.include_router(reviews_router, prefix="/api")
    application.include_router(decisions_router, prefix="/api")
    return application


app = create_app()
