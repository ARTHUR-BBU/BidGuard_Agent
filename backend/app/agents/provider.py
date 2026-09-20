"""Centralized, fail-closed model-provider configuration.

This module is deliberately small. It does not execute an Agent and it does
not decide any Evidence, Assessment, or DisplayStatus. It only translates
approved application configuration into the installed Agents SDK's run
configuration.
"""

from __future__ import annotations

from typing import Literal

from agents import ModelRetrySettings, ModelSettings, OpenAIProvider, RunConfig

from app.settings import Settings

ModelPurpose = Literal["extraction", "review"]


class ModelConfigurationError(RuntimeError):
    """A safe, stable configuration error suitable for user-facing logs."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def resolve_model_name(purpose: ModelPurpose, settings: Settings) -> str:
    """Resolve one explicitly requested model without provider fallback."""

    if purpose not in ("extraction", "review"):
        raise ModelConfigurationError("MODEL_PURPOSE_INVALID")

    model = (
        settings.extraction_model
        if purpose == "extraction"
        else settings.review_model
    ).strip()
    if not model:
        raise ModelConfigurationError("MODEL_NOT_CONFIGURED")
    return model


def build_run_config(
    settings: Settings,
    *,
    purpose: ModelPurpose = "review",
) -> RunConfig:
    """Build the single SDK boundary used by future Agent executions.

    The SDK reads ``OPENAI_API_KEY`` from the process environment through its
    provider. This function intentionally never copies, logs, or returns that
    secret. Retry is opt-in and set to zero here so callers must choose an
    explicit retry policy in a later governed task.
    """

    provider_name = settings.model_provider.strip().lower()
    if provider_name != "openai":
        raise ModelConfigurationError("MODEL_PROVIDER_UNSUPPORTED")

    model_name = resolve_model_name(purpose, settings)
    return RunConfig(
        model=model_name,
        model_provider=OpenAIProvider(),
        model_settings=ModelSettings(
            timeout=15.0,
            retry=ModelRetrySettings(max_retries=0),
        ),
        tracing_disabled=True,
        trace_include_sensitive_data=False,
        workflow_name="BidGuard bounded smoke",
    )

