from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.provider import (
    ModelConfigurationError,
    build_run_config,
    resolve_model_name,
)
from app.settings import Settings


def _load_smoke_module():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "smoke_agent.py"
    spec = importlib.util.spec_from_file_location("bidguard_smoke_agent", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_model_name_requires_the_requested_model() -> None:
    settings = Settings(extraction_model="", review_model="")

    with pytest.raises(ModelConfigurationError, match="MODEL_NOT_CONFIGURED"):
        resolve_model_name("extraction", settings)
    with pytest.raises(ModelConfigurationError, match="MODEL_NOT_CONFIGURED"):
        resolve_model_name("review", settings)


def test_resolve_model_name_keeps_extraction_and_review_separate() -> None:
    settings = Settings(extraction_model="extract-model", review_model="review-model")

    assert resolve_model_name("extraction", settings) == "extract-model"
    assert resolve_model_name("review", settings) == "review-model"


def test_unknown_provider_fails_closed_without_fallback() -> None:
    settings = Settings(
        model_provider="other-provider",
        extraction_model="extract-model",
        review_model="review-model",
    )

    with pytest.raises(ModelConfigurationError, match="MODEL_PROVIDER_UNSUPPORTED"):
        build_run_config(settings)


def test_build_run_config_centralizes_model_and_openai_provider() -> None:
    settings = Settings(
        model_provider="openai",
        extraction_model="extract-model",
        review_model="review-model",
    )

    extraction_config = build_run_config(settings, purpose="extraction")
    review_config = build_run_config(settings, purpose="review")

    assert extraction_config.model == "extract-model"
    assert review_config.model == "review-model"
    assert type(extraction_config.model_provider).__name__ == "OpenAIProvider"


def test_build_run_config_rejects_missing_model_for_execution() -> None:
    settings = Settings(extraction_model="", review_model="review-model")

    with pytest.raises(ModelConfigurationError, match="MODEL_NOT_CONFIGURED"):
        build_run_config(settings, purpose="extraction")


def test_easyrouter_builds_explicit_chat_completions_provider() -> None:
    settings = Settings(
        model_provider="easyrouter",
        easyrouter_api_key="test-key",
        easyrouter_base_url="https://easyrouter.io/v1",
        easyrouter_review_model="deepseek-v4-flash",
    )

    config = build_run_config(settings, purpose="review")

    assert config.model == "deepseek-v4-flash"
    provider = config.model_provider
    assert type(provider).__name__ == "OpenAIProvider"
    assert vars(provider)["_stored_base_url"] == "https://easyrouter.io/v1"
    assert vars(provider)["_stored_api_key"] == "test-key"
    assert vars(provider)["_use_responses"] is False


def test_easyrouter_can_be_selected_explicitly_without_changing_openai_default() -> None:
    settings = Settings(
        model_provider="openai",
        review_model="openai-review",
        easyrouter_api_key="test-key",
        easyrouter_review_model="deepseek-v4-flash",
    )

    config = build_run_config(
        settings,
        provider="easyrouter",
        model="deepseek-v4-flash",
    )

    assert config.model == "deepseek-v4-flash"
    assert type(config.model_provider).__name__ == "OpenAIProvider"
    assert vars(config.model_provider)["_use_responses"] is False


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        (
            Settings(
                model_provider="easyrouter",
                easyrouter_review_model="deepseek-v4-flash",
            ),
            "MODEL_API_KEY_NOT_CONFIGURED",
        ),
        (
            Settings(
                model_provider="easyrouter",
                easyrouter_api_key="test-key",
                easyrouter_base_url="http://easyrouter.io/v1",
                easyrouter_review_model="deepseek-v4-flash",
            ),
            "MODEL_BASE_URL_INVALID",
        ),
        (
            Settings(
                model_provider="easyrouter",
                easyrouter_api_key="test-key",
            ),
            "MODEL_NOT_CONFIGURED",
        ),
    ],
)
def test_easyrouter_configuration_errors_fail_closed(settings: Settings, code: str) -> None:
    with pytest.raises(ModelConfigurationError, match=code):
        build_run_config(settings)


def test_smoke_accepts_explicit_provider_and_model_without_business_text() -> None:
    smoke = _load_smoke_module()

    class FakeRunner:
        @staticmethod
        def run_sync(agent, prompt, *, max_turns, run_config):
            assert max_turns == 1
            assert agent.tools == []
            assert prompt == "Reply with a readiness acknowledgement only."
            assert run_config.model == "deepseek-v4-flash"
            return SimpleNamespace(final_output="OK")

    settings = Settings(
        model_provider="openai",
        review_model="openai-review",
        easyrouter_api_key="test-key",
    )
    assert (
        smoke.run_smoke(
            settings,
            runner=FakeRunner,
            provider="easyrouter",
            model="deepseek-v4-flash",
        )
        is True
    )


def test_smoke_uses_sync_runner_and_accepts_structured_result() -> None:
    smoke = _load_smoke_module()
    calls: list[tuple[object, str, int, object]] = []

    class FakeRunner:
        @staticmethod
        def run_sync(agent, prompt, *, max_turns, run_config):
            calls.append((agent, prompt, max_turns, run_config))
            assert run_config.model == "review-model"
            assert run_config.model_settings is not None
            assert run_config.model_settings.timeout == 15.0
            assert run_config.model_settings.retry is not None
            assert run_config.model_settings.retry.max_retries == 0
            assert agent.tools == []
            assert prompt == "Reply with a readiness acknowledgement only."
            return SimpleNamespace(final_output={"status": "ready"})

    settings = Settings(review_model="review-model")

    assert smoke.run_smoke(settings, runner=FakeRunner) is True
    assert len(calls) == 1
    assert calls[0][2] == 1


def test_smoke_script_has_safe_failure_output_without_model_configuration() -> None:
    backend_dir = Path(__file__).parents[1]
    environment = os.environ.copy()
    for variable in ("EXTRACTION_MODEL", "REVIEW_MODEL", "OPENAI_API_KEY"):
        environment.pop(variable, None)

    result = subprocess.run(
        [sys.executable, "scripts/smoke_agent.py"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode != 0
    assert result.stdout.strip() == "ERROR MODEL_NOT_CONFIGURED"
    assert result.stderr == ""
    assert "OPENAI_API_KEY" not in result.stdout
    assert "Authorization" not in result.stdout
