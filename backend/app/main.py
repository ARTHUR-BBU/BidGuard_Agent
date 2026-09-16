from fastapi import FastAPI

from .api.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="BidGuard API")
    application.include_router(health_router, prefix="/api")
    return application


app = create_app()
