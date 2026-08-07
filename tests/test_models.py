from pathlib import Path

import pytest
from pydantic import ValidationError

from requirements_fetcher.models import AppConfig, LLMProfile


def minimal_config() -> dict:
    return {
        "project": {"name": "My Feature"},
        "target": {"url": "https://example.com/issues"},
        "scope": {"description": "Analyze issue listing and filters"},
    }


def test_minimal_config_applies_safe_defaults() -> None:
    config = AppConfig.model_validate(minimal_config())

    assert config.project.name == "my-feature"
    assert config.target.allowed_domains == ["example.com"]
    assert config.browser.max_actions == 8
    assert config.llm.profile == LLMProfile.DEVELOPMENT
    assert config.llm.synthesis_model == "gemini-3.5-flash-lite"


def test_showcase_profile_selects_showcase_model() -> None:
    raw = minimal_config()
    raw["llm"] = {"profile": "showcase", "showcase_model": "gemini-test-showcase"}
    config = AppConfig.model_validate(raw)

    assert config.llm.synthesis_model == "gemini-test-showcase"


def test_target_domain_must_be_allowed() -> None:
    raw = minimal_config()
    raw["target"]["allowed_domains"] = ["other.example"]

    with pytest.raises(ValidationError, match="must include the target URL hostname"):
        AppConfig.model_validate(raw)


def test_browser_limits_are_bounded() -> None:
    raw = minimal_config()
    raw["browser"] = {"max_pages": 21, "max_actions": 31}

    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_from_yaml_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- an\n- object\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML object"):
        AppConfig.from_yaml(path)

