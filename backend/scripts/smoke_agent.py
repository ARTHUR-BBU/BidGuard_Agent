"""Run one bounded, non-business Agent connectivity smoke.

Success prints exactly ``OK``. Failure prints only a stable safe reason code.
This command never receives tender, proposal, company, or other business text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python scripts/smoke_agent.py`` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import Agent, Runner

from app.agents.provider import ModelConfigurationError, build_run_config
from app.settings import Settings


def run_smoke(
    settings: Settings,
    *,
    runner: object = Runner,
    provider: str | None = None,
    model: str | None = None,
) -> bool:
    """Run one SDK turn and return whether a non-empty result was returned."""

    run_config = build_run_config(
        settings,
        purpose="review",
        provider=provider,
        model=model,
    )
    agent = Agent(
        name="BidGuard connectivity smoke",
        instructions="Return a short readiness acknowledgement.",
        tools=[],
    )
    result = runner.run_sync(  # type: ignore[attr-defined]
        agent,
        "Reply with a readiness acknowledgement only.",
        max_turns=1,
        run_config=run_config,
    )
    return bool(result.final_output)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    settings = Settings()
    try:
        if not run_smoke(settings, provider=args.provider, model=args.model):
            print("ERROR EMPTY_MODEL_RESPONSE")
            return 1
    except ModelConfigurationError as error:
        print(f"ERROR {error.code}")
        return 1
    except Exception:  # noqa: BLE001
        # Never expose exception text: SDK errors can contain URLs, headers,
        # request metadata, or provider-specific sensitive information.
        print("ERROR MODEL_REQUEST_FAILED")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
