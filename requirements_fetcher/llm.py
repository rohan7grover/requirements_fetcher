from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from requirements_fetcher.models import BrowserAction, GeneratedRequirements, LLMConfig
from requirements_fetcher.storage import RunPaths, write_json


T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    def __init__(self, config: LLMConfig, paths: RunPaths) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.config = config
        self.paths = paths
        self.client = genai.Client(api_key=api_key)
        self.usage: list[dict[str, Any]] = []

    async def choose_browser_action(self, prompt: str) -> BrowserAction:
        return await self._generate_structured(
            model=self.config.lightweight_model,
            prompt=prompt,
            schema=BrowserAction,
            purpose="browser_action",
            max_output_tokens=512,
        )

    async def generate_requirements(
        self, prompt: str, validation_feedback: str | None = None
    ) -> GeneratedRequirements:
        if validation_feedback:
            prompt += (
                "\n\nThe previous response failed local validation. Correct these issues while "
                f"preserving evidence fidelity:\n{validation_feedback[:4_000]}"
            )
        return await self._generate_structured(
            model=self.config.synthesis_model,
            prompt=prompt,
            schema=GeneratedRequirements,
            purpose="requirements_synthesis",
            max_output_tokens=self.config.max_output_tokens,
        )

    async def count_tokens(self, model: str, contents: str) -> int:
        response = await self.client.aio.models.count_tokens(model=model, contents=contents)
        total = int(getattr(response, "total_tokens", 0) or 0)
        self.usage.append(
            {
                "purpose": "input_token_count",
                "model": model,
                "prompt_tokens": total,
                "output_tokens": 0,
                "total_tokens": total,
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        return total

    async def _generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
        purpose: str,
        max_output_tokens: int,
    ) -> T:
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=gemini_response_schema(schema),
                max_output_tokens=max_output_tokens,
            ),
        )
        self._record_usage(purpose, model, response)
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        if isinstance(parsed, dict):
            return schema.model_validate(parsed)
        text = response.text or ""
        try:
            return schema.model_validate_json(text)
        except ValidationError:
            try:
                return schema.model_validate(json.loads(text))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"Gemini returned invalid structured output for {purpose}") from exc

    def _record_usage(self, purpose: str, model: str, response: Any) -> None:
        metadata = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(metadata, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(metadata, "candidates_token_count", 0) or 0)
        thought_tokens = int(getattr(metadata, "thoughts_token_count", 0) or 0)
        total_tokens = int(getattr(metadata, "total_token_count", 0) or 0)
        self.usage.append(
            {
                "purpose": purpose,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "thought_tokens": thought_tokens,
                "total_tokens": total_tokens,
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )

    def save_usage(self) -> None:
        generated_calls = [item for item in self.usage if item["purpose"] != "input_token_count"]
        totals = {
            "prompt_tokens": sum(item["prompt_tokens"] for item in generated_calls),
            "output_tokens": sum(item["output_tokens"] for item in generated_calls),
            "thought_tokens": sum(item.get("thought_tokens", 0) for item in generated_calls),
            "total_tokens": sum(item["total_tokens"] for item in generated_calls),
        }
        write_json(
            self.paths.evidence / "llm-usage.json",
            {"calls": self.usage, "generation_totals": totals},
        )

    async def close(self) -> None:
        try:
            await self.client.aio.aclose()
        except (AttributeError, TypeError):
            try:
                self.client.close()
            except AttributeError:
                pass


def gemini_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert Pydantic JSON Schema into the Gemini-supported subset.

    Gemini does not accept Pydantic's `additionalProperties: false`; local Pydantic
    validation remains the authority for rejecting unknown fields in the response.
    """
    raw = model.model_json_schema()
    definitions = raw.pop("$defs", {})

    def normalize(value: Any, resolving: set[str] | None = None) -> Any:
        if isinstance(value, list):
            return [normalize(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            active = resolving or set()
            if name in active:
                return {}
            target = definitions.get(name)
            if isinstance(target, dict):
                merged = deepcopy(target)
                merged.update({key: item for key, item in value.items() if key != "$ref"})
                return normalize(merged, active | {name})
        return {
            key: normalize(item, resolving)
            for key, item in value.items()
            if key not in {"$defs", "$ref", "additionalProperties", "default", "title"}
        }

    schema = normalize(raw)
    if not isinstance(schema, dict):
        raise ValueError("Could not create Gemini response schema")
    return schema
