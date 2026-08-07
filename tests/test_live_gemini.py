import os
from pathlib import Path

import pytest

from requirements_fetcher.llm import GeminiClient
from requirements_fetcher.models import AppConfig
from requirements_fetcher.storage import RunPaths


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("GEMINI_API_KEY"),
    reason="requires RUN_LIVE_TESTS=1 and GEMINI_API_KEY",
)
async def test_gemini_structured_action_smoke(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "project": {"name": "live-gemini"},
            "target": {"url": "https://example.com"},
            "scope": {"description": "Inspect the example page"},
            "output": {"root_directory": str(tmp_path)},
        }
    )
    client = GeminiClient(config.llm, RunPaths.create(config))
    try:
        action = await client.choose_browser_action(
            "No interactive elements are available. Return the stop action."
        )
    finally:
        await client.close()

    assert action.action.value == "stop"

