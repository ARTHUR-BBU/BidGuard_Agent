from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BidGuard API"
    database_url: str = "sqlite:///./bidguard.db"
    storage_root: Path = Path("./uploads")
    model_provider: str = "openai"
    extraction_model: str = ""
    review_model: str = ""
    max_agent_turns: int = 12
    max_tool_calls: int = 40

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
