from pathlib import Path

import httpx
import pytest

from requirements_fetcher.models import AppConfig
from requirements_fetcher.source_collectors import SourceCollector, clean_document
from requirements_fetcher.storage import EvidenceStore, RunPaths


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "project": {"name": "issues"},
            "target": {"url": "https://example.com/issues"},
            "scope": {"description": "Issue list labels and comments"},
            "output": {"root_directory": str(tmp_path)},
        }
    )


def test_clean_document_removes_navigation_and_scripts() -> None:
    html = """
    <html><body><nav>Skip me</nav><main><h1>Issues</h1>
    <p>Manage labels and comments.</p><script>secret()</script></main></body></html>
    """

    cleaned = clean_document(html, "text/html")

    assert "# Issues" in cleaned
    assert "Manage labels" in cleaned
    assert "Skip me" not in cleaned
    assert "secret" not in cleaned


@pytest.mark.asyncio
async def test_openapi_collection_filters_operations_by_scope(tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/issues": {
                "get": {"summary": "List issues", "responses": {"200": {"description": "ok"}}}
            },
            "/billing": {
                "get": {"summary": "Billing details", "responses": {"200": {"description": "ok"}}}
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=spec, request=request)

    config = make_config(tmp_path)
    paths = RunPaths.create(config)
    collector = SourceCollector(config, paths, EvidenceStore(paths))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        operations = await collector._collect_api_spec(client, "https://example.com/openapi.json")

    assert [(item["method"], item["path"]) for item in operations] == [("GET", "/issues")]

