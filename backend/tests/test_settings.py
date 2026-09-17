from collections.abc import Iterator

import pytest

from app.settings import get_settings

SETTINGS_ENV_VARS = (
    "APP_NAME",
    "DATABASE_URL",
    "STORAGE_ROOT",
    "MODEL_PROVIDER",
    "EXTRACTION_MODEL",
    "REVIEW_MODEL",
    "MAX_AGENT_TURNS",
    "MAX_TOOL_CALLS",
)


@pytest.fixture(autouse=True)
def isolate_settings_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Iterator[None]:
    for variable in SETTINGS_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_expose_safe_defaults() -> None:
    settings = get_settings()

    assert settings.app_name == "BidGuard API"
    assert settings.database_url == "sqlite:///./bidguard.db"
    assert settings.storage_root.name == "uploads"
    assert settings.model_provider == "openai"
    assert settings.extraction_model == ""
    assert settings.review_model == ""
    assert settings.max_agent_turns == 12
    assert settings.max_tool_calls == 40
