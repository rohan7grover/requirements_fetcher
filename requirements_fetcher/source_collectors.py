from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from bs4 import BeautifulSoup

from requirements_fetcher.models import AppConfig, EvidenceType
from requirements_fetcher.storage import EvidenceStore, RunPaths, write_json
from requirements_fetcher.utils import relevance_score, safe_filename, scope_keywords


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


@dataclass
class SourceCollectionResult:
    documents: list[dict[str, str]] = field(default_factory=list)
    api_operations: list[dict[str, Any]] = field(default_factory=list)


class SourceCollector:
    def __init__(self, config: AppConfig, paths: RunPaths, evidence: EvidenceStore) -> None:
        self.config = config
        self.paths = paths
        self.evidence = evidence
        scope_text = " ".join(
            [config.scope.description, *config.scope.workflows, *config.scope.exclude]
        )
        self.keywords = scope_keywords(scope_text)

    async def collect(self) -> SourceCollectionResult:
        result = SourceCollectionResult()
        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            document_urls = [str(url) for url in self.config.sources.documentation]
            api_urls = [str(url) for url in self.config.sources.api_specs]
            if self.config.sources.discovery:
                discovered_docs, discovered_api = await self._discover(client)
                document_urls.extend(discovered_docs)
                api_urls.extend(discovered_api)

            for url in _deduplicate(document_urls):
                document = await self._collect_document(client, url, len(result.documents) + 1)
                if document:
                    result.documents.append(document)

            for url in _deduplicate(api_urls):
                operations = await self._collect_api_spec(client, url)
                result.api_operations.extend(operations)

        result.api_operations = _deduplicate_operations(result.api_operations)[:50]
        if result.api_operations:
            artifact = self.paths.api / "relevant-operations.json"
            write_json(artifact, result.api_operations)
            evidence_id = self.evidence.add(
                EvidenceType.API_SPEC,
                "multiple API specification sources",
                f"{len(result.api_operations)} scope-relevant API operations",
                artifact,
            )
            for operation in result.api_operations:
                operation["evidence_id"] = evidence_id
            write_json(artifact, result.api_operations)
        return result

    async def _discover(self, client: httpx.AsyncClient) -> tuple[list[str], list[str]]:
        target = urlparse(str(self.config.target.url))
        origin = f"{target.scheme}://{target.netloc}"
        docs: list[str] = []
        api: list[str] = []

        llms_url = urljoin(origin, "/llms.txt")
        response = await self._safe_get(client, llms_url, warn=False)
        if response is not None and response.status_code == 200 and len(response.text) <= 2_000_000:
            docs.append(llms_url)
            for link in re.findall(r'https?://[^\s)>\]}"\']+', response.text):
                if _same_origin(link, origin) and relevance_score(link, self.keywords) > 0:
                    docs.append(link.rstrip(".,;"))
                    if len(docs) >= 6:
                        break

        sitemap_url = urljoin(origin, "/sitemap.xml")
        response = await self._safe_get(client, sitemap_url, warn=False)
        if response is not None and response.status_code == 200 and len(response.content) <= 5_000_000:
            try:
                root = ET.fromstring(response.content)
                candidates = [node.text or "" for node in root.findall(".//{*}loc")]
                ranked = sorted(
                    (
                        (relevance_score(candidate, self.keywords), candidate)
                        for candidate in candidates
                        if _same_origin(candidate, origin)
                    ),
                    reverse=True,
                )
                docs.extend(candidate for score, candidate in ranked[:5] if score > 0)
            except ET.ParseError:
                self.evidence.warn(f"Could not parse discovered sitemap: {sitemap_url}")

        for path in ("/openapi.json", "/swagger.json", "/v3/api-docs"):
            candidate = urljoin(origin, path)
            response = await self._safe_get(client, candidate, warn=False)
            if response is not None and response.status_code == 200 and _looks_like_api_spec(response):
                api.append(candidate)
        return docs, api

    async def _collect_document(
        self, client: httpx.AsyncClient, url: str, sequence: int
    ) -> dict[str, str] | None:
        response = await self._safe_get(client, url)
        if response is None or response.status_code >= 400:
            return None
        if len(response.content) > 5_000_000:
            self.evidence.warn(f"Skipped oversized documentation source: {url}")
            return None
        text = clean_document(response.text, response.headers.get("content-type", ""))
        if len(text) < 100:
            self.evidence.warn(f"Documentation source contained too little readable text: {url}")
            return None
        text = text[:60_000]
        name = safe_filename(Path(urlparse(url).path).name or f"document-{sequence}")
        artifact = self.paths.documentation / f"{sequence:03d}-{name}.md"
        artifact.write_text(f"# Source\n\n{url}\n\n{text}\n", encoding="utf-8")
        evidence_id = self.evidence.add(
            EvidenceType.DOCUMENTATION,
            url,
            f"Documentation collected from {urlparse(url).netloc}",
            artifact,
        )
        return {"url": url, "text": text, "evidence_id": evidence_id}

    async def _collect_api_spec(self, client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
        response = await self._safe_get(client, url)
        if response is None or response.status_code >= 400:
            return []
        if len(response.content) > 30_000_000:
            self.evidence.warn(f"Skipped oversized API specification: {url}")
            return []
        try:
            spec = response.json()
        except (json.JSONDecodeError, ValueError):
            try:
                spec = yaml.safe_load(response.text)
            except yaml.YAMLError:
                self.evidence.warn(f"Could not parse API specification: {url}")
                return []
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            self.evidence.warn(f"Source was not an OpenAPI document: {url}")
            return []

        ranked: list[tuple[int, dict[str, Any]]] = []
        for path, path_item in spec["paths"].items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                searchable = " ".join(
                    [
                        str(path),
                        str(operation.get("summary", "")),
                        str(operation.get("description", "")),
                        " ".join(map(str, operation.get("tags", []))),
                        str(operation.get("operationId", "")),
                    ]
                )
                score = relevance_score(searchable, self.keywords)
                if score <= 0:
                    continue
                normalized = {
                    "source_url": url,
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation.get("operationId"),
                    "summary": operation.get("summary", ""),
                    "description": str(operation.get("description", ""))[:2_000],
                    "tags": operation.get("tags", []),
                    "parameters": operation.get("parameters", []),
                    "request_body": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                }
                ranked.append((score, normalized))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            self.evidence.warn(f"No scope-relevant operations found in API specification: {url}")
        return [operation for _, operation in ranked[:50]]

    async def _safe_get(
        self, client: httpx.AsyncClient, url: str, *, warn: bool = True
    ) -> httpx.Response | None:
        try:
            response = await client.get(url, headers={"User-Agent": "requirements-fetcher/0.1"})
            if warn and response.status_code >= 400:
                self.evidence.warn(f"Source returned HTTP {response.status_code}: {url}")
            return response
        except httpx.HTTPError as exc:
            if warn:
                self.evidence.warn(f"Could not fetch source {url}: {exc}")
            return None


def clean_document(content: str, content_type: str) -> str:
    if "html" not in content_type.lower() and not content.lstrip().startswith("<"):
        return _normalize_lines(content)
    soup = BeautifulSoup(content, "html.parser")
    for element in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        element.decompose()
    lines: list[str] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code", "th", "td"]):
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name and element.name.startswith("h"):
            level = int(element.name[1])
            lines.append(f"{'#' * level} {text}")
        elif element.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return _normalize_lines("\n\n".join(lines))


def _normalize_lines(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    output: list[str] = []
    for line in lines:
        if not line and (not output or not output[-1]):
            continue
        output.append(line)
    return "\n".join(output).strip()


def _same_origin(url: str, origin: str) -> bool:
    try:
        parsed = urlparse(url)
        expected = urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == expected.netloc
    except ValueError:
        return False


def _looks_like_api_spec(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type and "yaml" not in content_type:
        return False
    sample = response.text[:5_000]
    return "openapi" in sample or "swagger" in sample or '"paths"' in sample


def _deduplicate(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _deduplicate_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        key = (str(operation.get("method")), str(operation.get("path")))
        if key not in seen:
            seen.add(key)
            result.append(operation)
    return result
