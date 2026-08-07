from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from requirements_fetcher.browser import BrowserCollectionError, BrowserCollectionResult, BrowserCollector
from requirements_fetcher.llm import GeminiClient
from requirements_fetcher.models import (
    AppConfig,
    EvidenceType,
    GeneratedRequirements,
    GenerationResult,
    ProjectResult,
    RequirementsDocument,
)
from requirements_fetcher.renderer import render_markdown
from requirements_fetcher.source_collectors import SourceCollectionResult, SourceCollector
from requirements_fetcher.storage import EvidenceStore, RunPaths, write_json
from requirements_fetcher.utils import compact_json


ProgressCallback = Callable[[str], None]


async def run_analysis(config: AppConfig, progress: ProgressCallback = print) -> Path:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("GEMINI_API_KEY is required")
    paths = RunPaths.create(config)
    evidence = EvidenceStore(paths)
    llm = GeminiClient(config.llm, paths)
    try:
        progress("[1/5] Configuration loaded")
        sources = await SourceCollector(config, paths, evidence).collect()
        progress(
            f"[2/5] Sources collected: {len(sources.documents)} documents, "
            f"{len(sources.api_operations)} API operations"
        )

        browser = await BrowserCollector(config, paths, evidence, llm).collect()
        progress(
            f"[3/5] Browser exploration completed: {len(browser.observations)} states, "
            f"{len(browser.actions)} actions"
        )
        evidence.save_index()

        prompt = await _build_synthesis_prompt(config, sources, browser, evidence, llm)
        generated = await _generate_with_retry(llm, prompt)
        cleaned_payload, removed = _remove_invalid_evidence_ids(
            generated.model_dump(mode="json"), evidence.valid_ids
        )
        if removed:
            evidence.warn(f"Removed {removed} invalid evidence references from generated requirements")
        generated = GeneratedRequirements.model_validate(cleaned_payload)
        document = RequirementsDocument(
            project=ProjectResult(
                name=config.project.name,
                target_url=str(config.target.url),
                scope=config.scope.description,
                generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
            generation=GenerationResult(
                profile=config.llm.profile,
                lightweight_model=config.llm.lightweight_model,
                synthesis_model=config.llm.synthesis_model,
                warnings=evidence.warnings,
            ),
            frontend=generated.frontend,
            backend=generated.backend,
            database=generated.database,
            assumptions=generated.assumptions,
            unknowns=generated.unknowns,
        )
        write_json(paths.root / "requirements.json", document.model_dump(mode="json"))
        (paths.root / "requirements.md").write_text(render_markdown(document), encoding="utf-8")
        progress("[4/5] Requirements generated and validated")
        progress(f"[5/5] Results written to {paths.root}")
        return paths.root
    except BrowserCollectionError:
        raise
    finally:
        evidence.save_index()
        llm.save_usage()
        await llm.close()


async def _generate_with_retry(llm: GeminiClient, prompt: str) -> GeneratedRequirements:
    try:
        return await llm.generate_requirements(prompt)
    except (ValidationError, ValueError) as first_error:
        try:
            return await llm.generate_requirements(prompt, validation_feedback=str(first_error))
        except (ValidationError, ValueError) as second_error:
            raise RuntimeError(
                "Gemini requirements output failed validation after one retry: "
                f"{second_error}"
            ) from second_error


async def _build_synthesis_prompt(
    config: AppConfig,
    sources: SourceCollectionResult,
    browser: BrowserCollectionResult,
    evidence: EvidenceStore,
    llm: GeminiClient,
) -> str:
    documents = [
        {
            "url": item["url"],
            "evidence_id": item["evidence_id"],
            "text": item["text"][:12_000],
        }
        for item in sources.documents[:8]
    ]
    observations = [_compact_observation(item) for item in browser.observations[:10]]
    api_operations = sources.api_operations[:40]
    network = browser.response_schemas[:40]
    records = [record.model_dump(mode="json") for record in evidence.records]

    def make_prompt() -> str:
        bundle = {
            "evidence_index": records,
            "documentation": documents,
            "api_operations": api_operations,
            "browser_observations": observations,
            "network_response_schemas": network,
        }
        return f"""Create implementation-ready requirements for a functionally similar web feature.

Requested feature: {config.scope.description}
Requested workflows: {compact_json(config.scope.workflows)}
Explicit exclusions: {compact_json(config.scope.exclude)}
Target URL: {config.target.url}

Rules:
- Produce concrete frontend pages, components, states, and workflows.
- Produce compatible backend endpoints and business rules, even when the original private backend is not visible.
- Produce an implementation-equivalent database design; never claim it is the target's actual private schema.
- Mark direct UI/network observations as observed, official documentation as documented, and design deductions as inferred.
- Use high confidence only for directly supported claims.
- Every evidence_id must exist in the evidence index below.
- Do not invent evidence IDs.
- Include at least one page, workflow, endpoint, business rule, and database entity.
- Put unresolved behavior in unknowns rather than presenting it as fact.

Evidence bundle:
{json.dumps(bundle, ensure_ascii=False, default=str)}
"""

    prompt = make_prompt()
    token_count = await llm.count_tokens(config.llm.synthesis_model, prompt)
    reductions = 0
    while token_count > config.llm.max_input_tokens:
        reductions += 1
        if len(api_operations) > 8:
            del api_operations[max(8, len(api_operations) // 2) :]
        elif len(network) > 10:
            del network[max(10, len(network) // 2) :]
        elif documents and any(len(item["text"]) > 1_000 for item in documents):
            for item in documents:
                item["text"] = item["text"][: max(1_000, len(item["text"]) // 2)]
        elif observations and any(len(item["visible_text"]) > 600 for item in observations):
            for item in observations:
                item["visible_text"] = item["visible_text"][:600]
                item["elements"] = item["elements"][:20]
        elif len(documents) > 1:
            documents.pop()
        elif len(observations) > 1:
            observations.pop()
        elif len(records) > 20:
            del records[20:]
        else:
            raise RuntimeError(
                "Could not reduce evidence enough to meet the configured synthesis input token budget"
            )
        prompt = make_prompt()
        token_count = await llm.count_tokens(config.llm.synthesis_model, prompt)
    if reductions:
        evidence.warn(
            f"Trimmed low-priority evidence to fit synthesis budget: {token_count} tokens"
        )
    return prompt


def _compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": observation.get("url"),
        "title": observation.get("title"),
        "visible_text": str(observation.get("visible_text", ""))[:4_000],
        "elements": list(observation.get("elements", []))[:60],
        "screenshot_evidence_id": observation.get("screenshot_evidence_id"),
    }


def _remove_invalid_evidence_ids(value: Any, valid_ids: set[str]) -> tuple[Any, int]:
    removed = 0
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                valid = [candidate for candidate in item if candidate in valid_ids]
                removed += len(item) - len(valid)
                cleaned[key] = valid
            else:
                cleaned_item, nested_removed = _remove_invalid_evidence_ids(item, valid_ids)
                removed += nested_removed
                cleaned[key] = cleaned_item
        return cleaned, removed
    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for item in value:
            cleaned_item, nested_removed = _remove_invalid_evidence_ids(item, valid_ids)
            removed += nested_removed
            cleaned_list.append(cleaned_item)
        return cleaned_list, removed
    return value, removed
