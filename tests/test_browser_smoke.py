import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from requirements_fetcher.browser import BrowserCollector
from requirements_fetcher.models import AppConfig, BrowserAction
from requirements_fetcher.storage import EvidenceStore, RunPaths


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<!doctype html><html><head><title>Issue list</title></head>
        <body><h1>Issues</h1><input type="search" placeholder="Search issues">
        <a href="/issues/1">First issue</a></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _StopLLM:
    async def choose_browser_action(self, prompt: str) -> BrowserAction:
        return BrowserAction(action="stop", reason="Enough evidence")


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="requires RUN_BROWSER_TESTS=1 and installed Chromium",
)
async def test_browser_collects_observation_and_screenshot(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        config = AppConfig.model_validate(
            {
                "project": {"name": "browser-smoke"},
                "target": {"url": f"http://{host}:{port}/issues"},
                "scope": {"description": "Inspect issue listing"},
                "browser": {"headed": False, "max_actions": 1},
                "output": {"root_directory": str(tmp_path)},
            }
        )
        paths = RunPaths.create(config)
        evidence = EvidenceStore(paths)
        result = await BrowserCollector(config, paths, evidence, _StopLLM()).collect()

        assert result.observations[0]["title"] == "Issue list"
        assert result.actions[0]["action"] == "stop"
        assert list(paths.screenshots.glob("*.png"))
    finally:
        server.shutdown()
        server.server_close()

