import json
from pathlib import Path

import pytest

from requirements_fetcher.browser import BrowserCollectionResult
from requirements_fetcher.models import AppConfig, GeneratedRequirements
from requirements_fetcher.pipeline import _remove_invalid_evidence_ids, run_analysis
from requirements_fetcher.source_collectors import SourceCollectionResult
from requirements_fetcher.storage import write_json


def test_invalid_evidence_references_are_removed_recursively() -> None:
    payload = {
        "frontend": {
            "pages": [
                {
                    "evidence_ids": ["ev-0001", "made-up"],
                    "components": [{"evidence_ids": ["ev-0002", "bad"]}],
                }
            ]
        }
    }

    cleaned, removed = _remove_invalid_evidence_ids(payload, {"ev-0001", "ev-0002"})

    assert removed == 2
    assert cleaned["frontend"]["pages"][0]["evidence_ids"] == ["ev-0001"]
    assert cleaned["frontend"]["pages"][0]["components"][0]["evidence_ids"] == ["ev-0002"]


def generated_requirements() -> GeneratedRequirements:
    trace = {"basis": "inferred", "confidence": "medium", "evidence_ids": []}
    return GeneratedRequirements.model_validate(
        {
            "frontend": {
                "pages": [
                    {
                        "name": "Issue List",
                        "route": "/issues",
                        "purpose": "List issues",
                        "components": [],
                        "states": ["loaded"],
                        **trace,
                    }
                ],
                "workflows": [
                    {
                        "name": "View issues",
                        "actor": "User",
                        "steps": ["Open the issue list"],
                        **trace,
                    }
                ],
            },
            "backend": {
                "api_endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/issues",
                        "purpose": "Return issues",
                        "authentication": "Public",
                        "response_shape": "Issue collection",
                        **trace,
                    }
                ],
                "business_rules": [
                    {
                        "description": "Return public issues",
                        "category": "authorization",
                        **trace,
                    }
                ],
            },
            "database": {
                "entities": [
                    {
                        "name": "Issue",
                        "purpose": "Persist an issue",
                        "fields": [
                            {
                                "name": "id",
                                "type": "uuid",
                                "required": True,
                                "unique": True,
                            }
                        ],
                        **trace,
                    }
                ]
            },
            "unknowns": ["Original database is private"],
        }
    )


@pytest.mark.asyncio
async def test_pipeline_writes_machine_and_human_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeGemini:
        def __init__(self, config: object, paths: object) -> None:
            self.config = config
            self.paths = paths

        async def count_tokens(self, model: str, contents: str) -> int:
            return 1_000

        async def generate_requirements(
            self, prompt: str, validation_feedback: str | None = None
        ) -> GeneratedRequirements:
            return generated_requirements()

        def save_usage(self) -> None:
            write_json(self.paths.evidence / "llm-usage.json", {"calls": []})

        async def close(self) -> None:
            return None

    class FakeSources:
        def __init__(self, *args: object) -> None:
            pass

        async def collect(self) -> SourceCollectionResult:
            return SourceCollectionResult()

    class FakeBrowser:
        def __init__(self, *args: object) -> None:
            pass

        async def collect(self) -> BrowserCollectionResult:
            return BrowserCollectionResult(
                observations=[
                    {
                        "url": "https://example.com/issues",
                        "title": "Issues",
                        "visible_text": "Issue list",
                        "elements": [],
                        "screenshot_evidence_id": None,
                    }
                ]
            )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("requirements_fetcher.pipeline.GeminiClient", FakeGemini)
    monkeypatch.setattr("requirements_fetcher.pipeline.SourceCollector", FakeSources)
    monkeypatch.setattr("requirements_fetcher.pipeline.BrowserCollector", FakeBrowser)
    config = AppConfig.model_validate(
        {
            "project": {"name": "pipeline-test"},
            "target": {"url": "https://example.com/issues"},
            "scope": {"description": "Inspect issue listing"},
            "output": {"root_directory": str(tmp_path)},
        }
    )

    output = await run_analysis(config, progress=lambda message: None)

    payload = json.loads((output / "requirements.json").read_text(encoding="utf-8"))
    markdown = (output / "requirements.md").read_text(encoding="utf-8")
    assert payload["frontend"]["pages"][0]["name"] == "Issue List"
    assert "# pipeline-test Requirements" in markdown
    assert (output / "evidence" / "index.json").exists()
    assert (output / "evidence" / "llm-usage.json").exists()
