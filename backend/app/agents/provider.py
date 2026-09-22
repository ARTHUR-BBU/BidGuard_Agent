"""Centralized, fail-closed model-provider configuration.

This module is deliberately small. It does not execute an Agent and it does
not decide any Evidence, Assessment, or DisplayStatus. It only translates
approved application configuration into the installed Agents SDK's run
configuration.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from agents import ModelRetrySettings, ModelSettings, OpenAIProvider, RunConfig

from app.agents.contracts import AgentRuntimeLimits
from app.settings import Settings

ModelPurpose = Literal["extraction", "review"]
ModelProviderName = Literal["openai", "easyrouter"]


class ModelConfigurationError(RuntimeError):
    """A safe, stable configuration error suitable for user-facing logs."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _normalise_provider(provider: str) -> ModelProviderName:
    provider_name = provider.strip().lower()
    if provider_name not in ("openai", "easyrouter"):
        raise ModelConfigurationError("MODEL_PROVIDER_UNSUPPORTED")
    return provider_name  # type: ignore[return-value]


def resolve_model_name(
    purpose: ModelPurpose,
    settings: Settings,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Resolve one explicitly requested model without provider fallback.

    ``provider`` and ``model`` are optional explicit overrides for bounded
    commands such as the connectivity smoke. They never trigger fallback.
    """

    if purpose not in ("extraction", "review"):
        raise ModelConfigurationError("MODEL_PURPOSE_INVALID")

    provider_name = _normalise_provider(provider or settings.model_provider)
    if model is not None:
        selected_model = model.strip()
    elif provider_name == "easyrouter":
        selected_model = (
            settings.easyrouter_extraction_model
            if purpose == "extraction"
            else settings.easyrouter_review_model
        ).strip()
    else:
        selected_model = (
            settings.extraction_model
            if purpose == "extraction"
            else settings.review_model
        ).strip()
    if not selected_model:
        raise ModelConfigurationError("MODEL_NOT_CONFIGURED")
    return selected_model


def build_run_config(
    settings: Settings,
    *,
    purpose: ModelPurpose = "review",
    provider: str | None = None,
    model: str | None = None,
    limits: AgentRuntimeLimits | None = None,
) -> RunConfig:
    """Build the single SDK boundary used by future Agent executions.

    The SDK reads ``OPENAI_API_KEY`` from the process environment through its
    provider. This function intentionally never copies, logs, or returns that
    secret. Retry is opt-in and set to zero here so callers must choose an
    explicit retry policy in a later governed task.
    """

    runtime_limits = limits or AgentRuntimeLimits(
        max_turns=settings.max_agent_turns,
        max_tool_calls=settings.max_tool_calls,
    )
    provider_name = _normalise_provider(provider or settings.model_provider)

    model_name = resolve_model_name(
        purpose,
        settings,
        provider=provider_name,
        model=model,
    )
    if provider_name == "easyrouter":
        if not settings.easyrouter_api_key.strip():
            raise ModelConfigurationError("MODEL_API_KEY_NOT_CONFIGURED")
        parsed_url = urlparse(settings.easyrouter_base_url.strip())
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ModelConfigurationError("MODEL_BASE_URL_INVALID")
        model_provider = OpenAIProvider(
            api_key=settings.easyrouter_api_key,
            base_url=settings.easyrouter_base_url.strip(),
            use_responses=False,
        )
    else:
        model_provider = OpenAIProvider()
    return RunConfig(
        model=model_name,
        model_provider=model_provider,
        model_settings=ModelSettings(
            timeout=runtime_limits.timeout_seconds,
            max_tokens=runtime_limits.max_output_tokens,
            retry=ModelRetrySettings(max_retries=runtime_limits.max_retries),
        ),
        tracing_disabled=True,
        trace_include_sensitive_data=False,
        workflow_name="BidGuard bounded smoke",
    )
