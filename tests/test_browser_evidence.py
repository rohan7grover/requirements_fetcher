import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from feature_blueprint.browser import (
    BrowserCollectionResult,
    BrowserCollector,
    _network_request_descriptor,
)
from feature_blueprint.models import AppConfig
from feature_blueprint.storage import EvidenceStore, RunPaths


class _FakeMouse:
    async def move(self, x: int, y: int) -> None:
        return None


class _FakePage:
    url = "https://example.com/issues"
    mouse = _FakeMouse()

    async def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    async def screenshot(self, *, full_page: bool) -> bytes:
        return b"identical-png-bytes"


@pytest.mark.asyncio
async def test_identical_screenshots_reuse_evidence_and_file(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "project": {"name": "screenshot-dedup"},
            "target": {"url": "https://example.com/issues"},
            "scope": {"description": "Inspect issues"},
            "output": {"root_directory": str(tmp_path)},
        }
    )
    paths = RunPaths.create(config)
    collector = BrowserCollector.__new__(BrowserCollector)
    collector.paths = paths
    collector.evidence = EvidenceStore(paths)
    collector.result = BrowserCollectionResult()
    collector._screenshot_evidence_by_hash = {}

    first = await collector._record_observation(_FakePage(), {"title": "Issues"})
    second = await collector._record_observation(_FakePage(), {"title": "Issues"})

    assert first["screenshot_evidence_id"] == second["screenshot_evidence_id"]
    assert second["screenshot_duplicate_of"] == first["screenshot_evidence_id"]
    assert len(list(paths.screenshots.glob("*.png"))) == 1
    assert len(collector.evidence.records) == 1


def test_persisted_graphql_is_labelled_non_replayable() -> None:
    payload = {
        "persistedQueryName": "IssueViewerViewQuery",
        "query": "abc123",
        "variables": {"owner": "openai", "number": 42},
    }
    url = "https://github.com/_graphql?" + urlencode({"body": json.dumps(payload)})

    descriptor = _network_request_descriptor(url)

    assert descriptor["url"] == "/_graphql"
    assert descriptor["origin"] == "https://github.com"
    assert descriptor["transport_kind"] == "observed_internal_graphql"
    assert descriptor["operation_name"] == "IssueViewerViewQuery"
    assert descriptor["replayable"] is False
    assert "abc123" not in json.dumps(descriptor)
