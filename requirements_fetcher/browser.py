from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import Browser, Error as PlaywrightError, Locator, Page, Response, async_playwright

from requirements_fetcher.llm import GeminiClient
from requirements_fetcher.models import (
    AppConfig,
    BrowserAction,
    BrowserActionName,
    EvidenceType,
    domain_is_allowed,
)
from requirements_fetcher.storage import EvidenceStore, RunPaths, write_json
from requirements_fetcher.utils import (
    compact_json,
    content_fingerprint,
    json_shape,
    redact,
    redact_url,
    scope_keywords,
)


INTERACTIVE_SELECTOR = ", ".join(
    [
        "a",
        "button",
        "input",
        "textarea",
        "select",
        "[role='button']",
        "[role='link']",
        "[role='tab']",
        "[role='menuitem']",
        "[role='option']",
    ]
)

DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|submit|save|send|publish|purchase|pay|checkout|confirm|archive|"
    r"subscribe|unsubscribe|follow|unfollow|react|assign|lock|unlock|reopen|merge|approve|reject|edit|update)\b|"
    r"close\s+issue",
    re.IGNORECASE,
)
SEARCH_FILTER_RE = re.compile(r"\b(search|filter|find|query|sort|state|status|label)\b", re.IGNORECASE)


class BrowserCollectionError(RuntimeError):
    pass


@dataclass
class BrowserCollectionResult:
    observations: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    workflow_coverage: list[dict[str, Any]] = field(default_factory=list)
    network_requests: list[dict[str, Any]] = field(default_factory=list)
    response_schemas: list[dict[str, Any]] = field(default_factory=list)


class NetworkRecorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._seen_requests: set[str] = set()
        self._seen_responses: set[str] = set()

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        if response.request.resource_type not in {"fetch", "xhr"}:
            return
        task = asyncio.create_task(self._record_response(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record_response(self, response: Response) -> None:
        request = response.request
        safe_url = redact_url(request.url)
        descriptor = _network_request_descriptor(request.url)
        request_key = f"{request.method} {safe_url}"
        if request_key not in self._seen_requests and len(self.requests) < 100:
            self._seen_requests.add(request_key)
            post_shape: Any = None
            if request.post_data:
                try:
                    post_shape = json_shape(redact(json.loads(request.post_data)))
                except (json.JSONDecodeError, TypeError):
                    post_shape = "string"
            self.requests.append(
                {
                    "method": request.method,
                    **descriptor,
                    "resource_type": request.resource_type,
                    "body_shape": post_shape,
                }
            )

        content_type = (response.headers.get("content-type") or "").lower()
        response_key = f"{response.status} {request_key}"
        if "json" not in content_type or response_key in self._seen_responses or len(self.responses) >= 100:
            return
        self._seen_responses.add(response_key)
        try:
            body = await response.json()
        except (PlaywrightError, json.JSONDecodeError, ValueError):
            return
        self.responses.append(
            {
                "method": request.method,
                **descriptor,
                "status": response.status,
                "content_type": content_type.split(";", 1)[0],
                "shape": json_shape(redact(body)),
            }
        )

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _network_request_descriptor(url: str) -> dict[str, Any]:
    """Describe browser traffic without presenting private transports as public APIs."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    encoded_payload = query.get("body", [None])[0]
    if parsed.path.endswith("/_graphql") and encoded_payload:
        try:
            payload = json.loads(encoded_payload)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and payload.get("persistedQueryName"):
            return {
                "url": parsed.path,
                "origin": f"{parsed.scheme}://{parsed.netloc}",
                "transport_kind": "observed_internal_graphql",
                "replayable": False,
                "operation_name": str(payload["persistedQueryName"]),
                "payload_location": "query.body",
                "payload_shape": json_shape(redact(payload)),
                "note": (
                    "Observed website transport; may require browser session headers/cookies. "
                    "Do not treat as a documented public API contract."
                ),
            }
    return {"url": redact_url(url), "transport_kind": "browser_fetch"}


class BrowserCollector:
    def __init__(
        self,
        config: AppConfig,
        paths: RunPaths,
        evidence: EvidenceStore,
        llm: GeminiClient,
    ) -> None:
        self.config = config
        self.paths = paths
        self.evidence = evidence
        self.llm = llm
        self.result = BrowserCollectionResult()
        self._seen_fingerprints: set[str] = set()
        self._observations_by_fingerprint: dict[str, dict[str, Any]] = {}
        self._screenshot_evidence_by_hash: dict[str, str] = {}
        self._seen_pages: set[str] = set()
        self._scope_keywords = scope_keywords(
            config.scope.description,
            *config.scope.workflows,
        )

    async def collect(self) -> BrowserCollectionResult:
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=not self.config.browser.headed)
                try:
                    await self._explore(browser)
                finally:
                    await browser.close()
        except PlaywrightError as exc:
            raise BrowserCollectionError(f"Browser exploration failed: {exc}") from exc
        return self.result

    async def _explore(self, browser: Browser) -> None:
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent="requirements-fetcher/0.1 (feature analysis)",
        )
        page = await context.new_page()
        page.set_default_timeout(self.config.browser.navigation_timeout_ms)
        recorder = NetworkRecorder()
        recorder.attach(page)
        try:
            response = await page.goto(
                str(self.config.target.url),
                wait_until="domcontentloaded",
                timeout=self.config.browser.navigation_timeout_ms,
            )
            if response is not None and response.status >= 400:
                raise BrowserCollectionError(
                    f"Target returned HTTP {response.status}: {self.config.target.url}"
                )
            await self._wait_for_ui_stable(page)
        except PlaywrightError as exc:
            raise BrowserCollectionError(f"Could not load target URL: {exc}") from exc

        try:
            self.result.workflow_coverage = [
                {"index": index, "workflow": workflow, "status": "pending"}
                for index, workflow in enumerate(self.config.scope.workflows)
            ]
            active_workflow = 0
            successful_actions_for_workflow = 0
            previous_fingerprint: str | None = None

            for decision in range(self.config.browser.max_actions + 1):
                if "/login" in urlparse(page.url).path.lower() or await page.locator(
                    "input[type='password']"
                ).count():
                    raise BrowserCollectionError(
                        "The target requires authentication; authenticated exploration is outside v1"
                    )
                current_host = (urlparse(page.url).hostname or "").lower()
                if not domain_is_allowed(current_host, self.config.target.allowed_domains):
                    self.evidence.warn(f"Stopped after navigation outside allowed domains: {page.url}")
                    break

                normalized_page = _normalize_page_url(page.url)
                if normalized_page not in self._seen_pages:
                    if len(self._seen_pages) >= self.config.browser.max_pages:
                        self.evidence.warn("Browser page limit reached")
                        break
                    self._seen_pages.add(normalized_page)

                observation, element_map = await self._observe(page, decision)
                fingerprint = observation["fingerprint"]
                if fingerprint not in self._seen_fingerprints:
                    observation = await self._record_observation(page, observation)
                    self._seen_fingerprints.add(fingerprint)
                    self._observations_by_fingerprint[fingerprint] = observation
                    self.result.observations.append(observation)
                else:
                    observation["screenshot_evidence_id"] = self._observations_by_fingerprint[
                        fingerprint
                    ].get("screenshot_evidence_id")

                if previous_fingerprint == fingerprint and self.result.actions:
                    last_action = self.result.actions[-1]
                    if last_action.get("executed") and last_action.get("action") not in {
                        BrowserActionName.COMPLETE_WORKFLOW.value,
                        BrowserActionName.STOP.value,
                    }:
                        last_action["result"] += "; no material page-state change detected"
                        successful_actions_for_workflow = max(
                            0, successful_actions_for_workflow - 1
                        )
                previous_fingerprint = fingerprint

                if decision >= self.config.browser.max_actions:
                    self.evidence.warn("Browser action limit reached")
                    break

                prompt = self._action_prompt(observation, active_workflow)
                try:
                    action = await self.llm.choose_browser_action(prompt)
                except Exception as exc:
                    self.evidence.warn(f"Browser action selection stopped: {exc}")
                    break

                action_record = action.model_dump(mode="json")
                action_record["step"] = decision + 1
                action_record["url"] = page.url

                if action.action == BrowserActionName.COMPLETE_WORKFLOW:
                    if active_workflow >= len(self.result.workflow_coverage):
                        action_record["executed"] = False
                        action_record["result"] = "No pending workflow to complete"
                    elif active_workflow > 0 and successful_actions_for_workflow == 0:
                        action_record["executed"] = False
                        action_record["result"] = (
                            "Workflow completion rejected: no successful exploration action "
                            "was performed for this workflow"
                        )
                    else:
                        coverage = self.result.workflow_coverage[active_workflow]
                        coverage.update(
                            {
                                "status": "covered",
                                "reason": action.reason,
                                "url": observation["url"],
                                "screenshot_evidence_id": observation.get(
                                    "screenshot_evidence_id"
                                ),
                            }
                        )
                        action_record["executed"] = True
                        action_record["result"] = (
                            f"Marked requested workflow {active_workflow + 1} as covered"
                        )
                        active_workflow += 1
                        successful_actions_for_workflow = 0
                    self.result.actions.append(action_record)
                    if active_workflow >= len(self.result.workflow_coverage):
                        break
                    continue

                if action.action == BrowserActionName.STOP and active_workflow < len(
                    self.result.workflow_coverage
                ):
                    action_record["executed"] = False
                    action_record["result"] = (
                        "Stop rejected because requested workflows remain pending"
                    )
                    self.result.actions.append(action_record)
                    continue

                success, message = await self._execute_action(page, action, element_map)
                action_record["executed"] = success
                action_record["result"] = message
                self.result.actions.append(action_record)
                if action.action == BrowserActionName.STOP:
                    break
                if success:
                    successful_actions_for_workflow += 1
                    await self._wait_for_ui_stable(page)
                else:
                    self.evidence.warn(f"Rejected browser action: {message}")
            pending = [
                item["workflow"]
                for item in self.result.workflow_coverage
                if item["status"] != "covered"
            ]
            if pending:
                self.evidence.warn(
                    "Browser could not cover requested workflows: " + "; ".join(pending)
                )
        finally:
            await recorder.flush()
            self.result.network_requests = recorder.requests
            self.result.response_schemas = recorder.responses
            await context.close()
            self._save_artifacts()

    async def _wait_for_ui_stable(self, page: Page) -> None:
        """Wait for client-rendered controls to settle after navigation or interaction."""
        try:
            await page.wait_for_selector("body", state="visible", timeout=5_000)
        except PlaywrightError:
            return
        await page.wait_for_timeout(800)
        previous: tuple[int, int] | None = None
        stable_ticks = 0
        for _ in range(12):
            try:
                body_length = await page.locator("body").evaluate(
                    "element => (element.innerText || '').length"
                )
                control_count = await page.locator(INTERACTIVE_SELECTOR).count()
            except PlaywrightError:
                return
            signature = (int(body_length), control_count)
            if signature == previous:
                stable_ticks += 1
                if stable_ticks >= 3:
                    return
            else:
                stable_ticks = 0
                previous = signature
            await page.wait_for_timeout(350)

    async def _observe(self, page: Page, step: int) -> tuple[dict[str, Any], dict[str, Locator]]:
        title = await page.title()
        try:
            visible_text = await page.locator("body").inner_text(timeout=5_000)
        except PlaywrightError:
            visible_text = ""
        visible_text = re.sub(r"\s+", " ", visible_text).strip()[:10_000]
        locators = page.locator(INTERACTIVE_SELECTOR)
        count = min(await locators.count(), 500)
        candidates: list[tuple[dict[str, Any], Locator, int]] = []
        for index in range(count):
            locator = locators.nth(index)
            try:
                if not await locator.is_visible(timeout=150):
                    continue
                element = await _describe_element(locator)
            except PlaywrightError:
                continue
            if not _element_has_identity(element):
                continue
            candidates.append((element, locator, index))

        candidates.sort(
            key=lambda item: _element_priority(
                item[0], page.url, self._scope_keywords, item[2]
            ),
            reverse=True,
        )
        elements: list[dict[str, Any]] = []
        element_map: dict[str, Locator] = {}
        seen_descriptions: set[str] = set()
        for element, locator, _ in candidates[:80]:
            description_key = compact_json(element)
            if description_key in seen_descriptions:
                continue
            seen_descriptions.add(description_key)
            element_id = f"el-{len(elements) + 1:03d}"
            element["id"] = element_id
            elements.append(element)
            element_map[element_id] = locator
        fingerprint_basis = compact_json(
            {
                "text": visible_text,
                "elements": elements,
            }
        )
        return (
            {
                "step": step,
                "url": redact_url(page.url),
                "title": title,
                "visible_text": visible_text,
                "elements": elements,
                "screenshot_evidence_id": None,
                "fingerprint": content_fingerprint(page.url, fingerprint_basis),
            },
            element_map,
        )

    async def _record_observation(
        self, page: Page, observation: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            # Move away from the last clicked link so hover previews do not obscure evidence.
            await page.mouse.move(1, 1)
            await page.wait_for_timeout(250)
            screenshot_bytes = await page.screenshot(full_page=True)
        except PlaywrightError:
            screenshot_bytes = await page.screenshot(full_page=False)

        screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()
        observation["screenshot_sha256"] = screenshot_hash
        existing_evidence_id = self._screenshot_evidence_by_hash.get(screenshot_hash)
        if existing_evidence_id:
            observation["screenshot_evidence_id"] = existing_evidence_id
            observation["screenshot_duplicate_of"] = existing_evidence_id
            return observation

        sequence = len(self._screenshot_evidence_by_hash) + 1
        screenshot = self.paths.screenshots / f"{sequence:03d}-{_page_label(observation['title'])}.png"
        screenshot.write_bytes(screenshot_bytes)
        evidence_id = self.evidence.add(
            EvidenceType.SCREENSHOT,
            page.url,
            f"Browser state: {observation['title'] or page.url}",
            screenshot,
        )
        self._screenshot_evidence_by_hash[screenshot_hash] = evidence_id
        observation["screenshot_evidence_id"] = evidence_id
        return observation

    def _action_prompt(self, observation: dict[str, Any], active_workflow: int) -> str:
        history = self.result.actions[-5:]
        workflow = (
            self.result.workflow_coverage[active_workflow]["workflow"]
            if active_workflow < len(self.result.workflow_coverage)
            else None
        )
        return f"""You are safely exploring a public web feature to gather implementation requirements.

Feature scope: {self.config.scope.description}
Requested workflows: {compact_json(self.config.scope.workflows)}
Workflow coverage: {compact_json(self.result.workflow_coverage)}
CURRENT WORKFLOW TO PROVE: {workflow or 'No configured workflow; inspect the feature generally'}
Excluded behavior: {compact_json(self.config.scope.exclude)}
Current page: {observation['url']}
Page title: {observation['title']}
Visible text summary: {observation['visible_text'][:2_500]}
Interactive elements: {compact_json(observation['elements'])}
Recent actions: {compact_json(history)}

Choose exactly one useful next action from click, type, select, go_back, complete_workflow, or stop.
Use only an element_id listed above. Type only into search/filter inputs. Select only filters or sorting.
Do not submit forms, create or modify data, authenticate, purchase, publish, save, send, delete, or leave the allowed feature scope.
Work only on the CURRENT WORKFLOW. Prefer controls and links whose URL/text directly match it.
Use complete_workflow only when this page directly proves the workflow's end result, not merely because a related control exists.
For filtering/search workflows, actually apply a safe filter/search and observe its result before completing it.
For detail workflows, open a real item and observe its detail content before completing it.
Never choose stop while a requested workflow is pending. Do not repeat an action that had no material page-state change.
"""

    async def _execute_action(
        self, page: Page, action: BrowserAction, elements: dict[str, Locator]
    ) -> tuple[bool, str]:
        if action.action == BrowserActionName.STOP:
            return True, "Exploration completed by model"
        if action.action == BrowserActionName.GO_BACK:
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=8_000)
                return True, "Navigated back"
            except PlaywrightError as exc:
                return False, f"Could not navigate back: {exc}"

        locator = elements.get(action.element_id or "")
        if locator is None:
            return False, f"Unknown element_id: {action.element_id}"
        try:
            details = await _describe_element(locator)
        except PlaywrightError as exc:
            return False, f"Element is no longer available: {exc}"
        label = " ".join(
            str(details.get(key) or "")
            for key in ("text", "aria_label", "placeholder", "name", "href")
        )
        safety_error = action_safety_error(action, details)
        if safety_error:
            return False, safety_error

        try:
            if action.action == BrowserActionName.CLICK:
                href = details.get("href")
                if href:
                    destination = urljoin(page.url, str(href))
                    if _normalize_page_url(destination) == _normalize_page_url(page.url):
                        return False, f"Same-page navigation rejected: {destination}"
                    host = (urlparse(destination).hostname or "").lower()
                    if not domain_is_allowed(host, self.config.target.allowed_domains):
                        return False, f"Cross-domain navigation rejected: {destination}"
                await locator.click(timeout=8_000)
            elif action.action == BrowserActionName.TYPE:
                await locator.fill((action.value or "")[:100])
                await locator.press("Enter")
            elif action.action == BrowserActionName.SELECT:
                await locator.select_option(label=(action.value or "")[:100])
            host = (urlparse(page.url).hostname or "").lower()
            if not domain_is_allowed(host, self.config.target.allowed_domains):
                await page.go_back(wait_until="domcontentloaded", timeout=8_000)
                return False, "Action navigated outside allowed domains and was reverted"
            return True, f"Executed {action.action.value}"
        except PlaywrightError as exc:
            return False, f"Playwright could not execute action: {exc}"

    def _save_artifacts(self) -> None:
        observations_path = self.paths.browser / "observations.json"
        actions_path = self.paths.browser / "actions.json"
        coverage_path = self.paths.browser / "workflow-coverage.json"
        requests_path = self.paths.network / "requests.json"
        responses_path = self.paths.network / "response-schemas.json"
        write_json(observations_path, self.result.observations)
        write_json(actions_path, self.result.actions)
        write_json(coverage_path, self.result.workflow_coverage)
        write_json(requests_path, self.result.network_requests)
        write_json(responses_path, self.result.response_schemas)
        if self.result.observations:
            self.evidence.add(
                EvidenceType.PAGE_OBSERVATION,
                str(self.config.target.url),
                f"{len(self.result.observations)} observed browser states",
                observations_path,
            )
        if self.result.network_requests or self.result.response_schemas:
            self.evidence.add(
                EvidenceType.NETWORK,
                str(self.config.target.url),
                (
                    f"{len(self.result.network_requests)} fetch/XHR requests and "
                    f"{len(self.result.response_schemas)} JSON response schemas"
                ),
                responses_path,
            )


async def _describe_element(locator: Locator) -> dict[str, Any]:
    tag = await locator.evaluate("element => element.tagName.toLowerCase()")
    text = re.sub(r"\s+", " ", (await locator.inner_text(timeout=1_000) or "")).strip()[:200]
    details = {
        "tag": tag,
        "role": await locator.get_attribute("role"),
        "type": await locator.get_attribute("type"),
        "text": text,
        "aria_label": await locator.get_attribute("aria-label"),
        "placeholder": await locator.get_attribute("placeholder"),
        "name": await locator.get_attribute("name"),
        "href": await locator.get_attribute("href"),
    }
    return {key: value for key, value in details.items() if value not in (None, "")}


def _element_has_identity(element: dict[str, Any]) -> bool:
    return any(
        str(element.get(key) or "").strip()
        for key in ("text", "aria_label", "placeholder", "name", "href")
    )


def _element_priority(
    element: dict[str, Any], current_url: str, keywords: list[str], dom_index: int
) -> int:
    label = " ".join(
        str(element.get(key) or "")
        for key in ("text", "aria_label", "placeholder", "name", "href")
    ).lower()
    score = sum(8 for keyword in keywords if keyword in label)
    if SEARCH_FILTER_RE.search(label):
        score += 18
    href = str(element.get("href") or "")
    if href:
        destination = urljoin(current_url, href)
        if _normalize_page_url(destination) == _normalize_page_url(current_url):
            score -= 50
        else:
            score += 5
        if re.search(r"/[^/?#]+/\d+(?:[/?#]|$)", urlparse(destination).path):
            score += 15
    if element.get("tag") in {"input", "select", "textarea"}:
        score += 5
    if DESTRUCTIVE_RE.search(label):
        score -= 30
    return score * 1_000 - dom_index


def _page_label(title: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return (value[:50] or "page").rstrip("-")


def _normalize_page_url(url: str) -> str:
    parsed = urlparse(url)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    if parsed.fragment:
        normalized += f"#{parsed.fragment}"
    return normalized


def action_safety_error(action: BrowserAction, details: dict[str, Any]) -> str | None:
    label = " ".join(
        str(details.get(key) or "")
        for key in ("text", "aria_label", "placeholder", "name", "href")
    )
    if details.get("type") == "submit" or DESTRUCTIVE_RE.search(label):
        return f"Unsafe or data-changing control rejected: {label[:120]}"
    if details.get("tag") != "a" and re.search(r"\b(create|new|add|post)\b", label, re.IGNORECASE):
        return f"Potential data-changing control rejected: {label[:120]}"
    if action.action == BrowserActionName.TYPE:
        if not SEARCH_FILTER_RE.search(label) and details.get("type") != "search":
            return "Typing is permitted only in search/filter controls"
    if action.action == BrowserActionName.SELECT:
        if details.get("tag") != "select" or not SEARCH_FILTER_RE.search(label):
            return "Selection is permitted only in filter/sort controls"
    return None
