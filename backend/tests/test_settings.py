from app.settings import get_settings


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
