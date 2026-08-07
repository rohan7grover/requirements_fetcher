from pathlib import Path

import pytest

from requirements_fetcher.llm import gemini_response_schema
from requirements_fetcher.models import BrowserAction, GeneratedRequirements
from requirements_fetcher.utils import (
    json_shape,
    load_gemini_key_from_env_files,
    redact,
    redact_url,
    scope_keywords,
)


def test_redact_removes_nested_secrets() -> None:
    value = {
        "username": "demo",
        "authorization": "Bearer secret",
        "nested": {"access_token": "abc", "safe": 3},
    }

    assert redact(value) == {
        "username": "demo",
        "authorization": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]", "safe": 3},
    }


def test_redact_url_preserves_safe_query_parameters() -> None:
    url = redact_url("https://example.com/items?status=open&token=secret#section")

    assert "status=open" in url
    assert "secret" not in url
    assert "%5BREDACTED%5D" in url
    assert "#section" not in url


def test_json_shape_removes_values() -> None:
    assert json_shape({"id": 4, "title": "Secret title", "active": True}) == {
        "id": "integer",
        "title": "string",
        "active": "boolean",
    }


def test_scope_keywords_excludes_common_words() -> None:
    keywords = scope_keywords("Clone the issue list and filter issues by label")

    assert "issue" in keywords
    assert "filter" in keywords
    assert "the" not in keywords


def test_loads_gemini_key_from_env_file_without_overwriting_process_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\nGEMINI_API_KEY=file-key\n", encoding="utf-8")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    load_gemini_key_from_env_files([env_file])
    import os

    assert os.environ["GEMINI_API_KEY"] == "file-key"

    monkeypatch.setenv("GEMINI_API_KEY", "process-key")
    load_gemini_key_from_env_files([env_file])
    assert os.environ["GEMINI_API_KEY"] == "process-key"


def test_gemini_response_schema_removes_unsupported_strict_model_metadata() -> None:
    schema = gemini_response_schema(GeneratedRequirements)
    action_schema = gemini_response_schema(BrowserAction)

    serialized = str(schema) + str(action_schema)
    assert "additionalProperties" not in serialized
    assert "$defs" not in serialized
    assert "$ref" not in serialized
    assert schema["type"] == "object"
