from types import SimpleNamespace

import pytest
from google.genai import errors

from feature_blueprint.llm import GeminiClient
from feature_blueprint.models import BrowserAction


@pytest.mark.asyncio
async def test_structured_generation_retries_transient_api_errors(monkeypatch) -> None:
    class FakeModels:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise errors.ServerError(503, {"message": "busy"})
            return SimpleNamespace(
                parsed=BrowserAction(action="stop", reason="done"),
                usage_metadata=None,
            )

    async def no_wait(_: int) -> None:
        return None

    models = FakeModels()
    client = GeminiClient.__new__(GeminiClient)
    client.client = SimpleNamespace(aio=SimpleNamespace(models=models))
    client.usage = []
    monkeypatch.setattr("feature_blueprint.llm.asyncio.sleep", no_wait)

    action = await client._generate_structured(
        model="test-model",
        prompt="choose",
        schema=BrowserAction,
        purpose="browser_action",
        max_output_tokens=100,
    )

    assert action.action == "stop"
    assert models.calls == 3
