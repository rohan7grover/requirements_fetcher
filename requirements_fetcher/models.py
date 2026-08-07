from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(StrictModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
        if not slug:
            raise ValueError("project.name must contain letters or numbers")
        return slug


class TargetConfig(StrictModel):
    url: HttpUrl
    allowed_domains: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or "/" in domain or ":" in domain:
                raise ValueError(f"Invalid allowed domain: {value!r}")
            if domain not in normalized:
                normalized.append(domain)
        return normalized


class ScopeConfig(StrictModel):
    description: str = Field(min_length=3, max_length=10_000)
    workflows: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class SourcesConfig(StrictModel):
    documentation: list[HttpUrl] = Field(default_factory=list)
    api_specs: list[HttpUrl] = Field(default_factory=list)
    discovery: bool = True


class BrowserConfig(StrictModel):
    headed: bool = False
    max_pages: int = Field(default=6, ge=1, le=20)
    max_actions: int = Field(default=8, ge=1, le=30)
    navigation_timeout_ms: int = Field(default=20_000, ge=3_000, le=60_000)


class LLMProfile(str, Enum):
    DEVELOPMENT = "development"
    SHOWCASE = "showcase"


class LLMConfig(StrictModel):
    provider: Literal["gemini"] = "gemini"
    profile: LLMProfile = LLMProfile.DEVELOPMENT
    lightweight_model: str = "gemini-3.5-flash-lite"
    development_model: str = "gemini-3.5-flash-lite"
    showcase_model: str = "gemini-3.6-flash"
    max_input_tokens: int = Field(default=30_000, ge=2_000, le=200_000)
    max_output_tokens: int = Field(default=6_000, ge=1_000, le=32_000)

    @property
    def synthesis_model(self) -> str:
        if self.profile == LLMProfile.SHOWCASE:
            return self.showcase_model
        return self.development_model


class OutputConfig(StrictModel):
    root_directory: Path = Path("output")


class AppConfig(StrictModel):
    project: ProjectConfig
    target: TargetConfig
    scope: ScopeConfig
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def add_and_validate_target_domain(self) -> "AppConfig":
        target_host = (urlparse(str(self.target.url)).hostname or "").lower()
        if not self.target.allowed_domains:
            self.target.allowed_domains = [target_host]
        if not domain_is_allowed(target_host, self.target.allowed_domains):
            raise ValueError("target.allowed_domains must include the target URL hostname")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "AppConfig":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Configuration file does not exist: {path}") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be a YAML object")
        return cls.model_validate(raw)


def domain_is_allowed(hostname: str, allowed_domains: list[str]) -> bool:
    host = hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


class EvidenceType(str, Enum):
    SCREENSHOT = "screenshot"
    PAGE_OBSERVATION = "page_observation"
    NETWORK = "network"
    DOCUMENTATION = "documentation"
    API_SPEC = "api_spec"


class EvidenceRecord(StrictModel):
    id: str
    type: EvidenceType
    source: str
    summary: str
    artifact: str
    collected_at: str


class EvidenceBasis(str, Enum):
    OBSERVED = "observed"
    DOCUMENTED = "documented"
    INFERRED = "inferred"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Traceable(StrictModel):
    basis: EvidenceBasis
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list)


class UIComponent(Traceable):
    name: str
    type: str
    purpose: str
    data_fields: list[str] = Field(default_factory=list)
    interactions: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


class FrontendPage(Traceable):
    name: str
    route: str | None = None
    purpose: str
    layout: list[str] = Field(default_factory=list)
    components: list[UIComponent] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


class Workflow(Traceable):
    name: str
    actor: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str]
    outcomes: list[str] = Field(default_factory=list)


class FrontendRequirements(StrictModel):
    pages: list[FrontendPage] = Field(min_length=1)
    workflows: list[Workflow] = Field(min_length=1)


class APIParameter(StrictModel):
    name: str
    location: Literal["path", "query", "header", "body"]
    type: str
    required: bool = False
    description: str = ""


class APIEndpoint(Traceable):
    method: str
    path: str
    purpose: str
    authentication: str
    parameters: list[APIParameter] = Field(default_factory=list)
    request_body: str | None = None
    response_shape: str
    errors: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)

    @field_validator("method")
    @classmethod
    def uppercase_method(cls, value: str) -> str:
        return value.upper()


class BusinessRule(Traceable):
    description: str
    category: Literal[
        "validation",
        "authorization",
        "filtering",
        "sorting",
        "pagination",
        "state_transition",
        "side_effect",
        "other",
    ]


class BackendRequirements(StrictModel):
    api_endpoints: list[APIEndpoint] = Field(min_length=1)
    business_rules: list[BusinessRule] = Field(min_length=1)


class DatabaseField(StrictModel):
    name: str
    type: str
    required: bool
    unique: bool = False
    description: str = ""


class DatabaseEntity(Traceable):
    name: str
    purpose: str
    fields: list[DatabaseField]


class DatabaseRelationship(Traceable):
    description: str
    from_entity: str
    to_entity: str
    cardinality: str


class DatabaseIndex(Traceable):
    entity: str
    fields: list[str]
    unique: bool = False
    reason: str


class DatabaseRequirements(StrictModel):
    entities: list[DatabaseEntity] = Field(min_length=1)
    relationships: list[DatabaseRelationship] = Field(default_factory=list)
    indexes: list[DatabaseIndex] = Field(default_factory=list)


class Assumption(StrictModel):
    description: str
    reason: str
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list)


class GeneratedRequirements(StrictModel):
    frontend: FrontendRequirements
    backend: BackendRequirements
    database: DatabaseRequirements
    assumptions: list[Assumption] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class ProjectResult(StrictModel):
    name: str
    target_url: str
    scope: str
    generated_at: str


class GenerationResult(StrictModel):
    provider: Literal["gemini"] = "gemini"
    profile: LLMProfile
    lightweight_model: str
    synthesis_model: str
    warnings: list[str] = Field(default_factory=list)


class RequirementsDocument(StrictModel):
    project: ProjectResult
    generation: GenerationResult
    frontend: FrontendRequirements
    backend: BackendRequirements
    database: DatabaseRequirements
    assumptions: list[Assumption] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class BrowserActionName(str, Enum):
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    GO_BACK = "go_back"
    STOP = "stop"


class BrowserAction(StrictModel):
    action: BrowserActionName
    element_id: str | None = None
    value: str | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "BrowserAction":
        needs_element = self.action in {
            BrowserActionName.CLICK,
            BrowserActionName.TYPE,
            BrowserActionName.SELECT,
        }
        if needs_element and not self.element_id:
            raise ValueError(f"{self.action.value} requires element_id")
        if self.action in {BrowserActionName.TYPE, BrowserActionName.SELECT} and self.value is None:
            raise ValueError(f"{self.action.value} requires value")
        return self


JsonDict = dict[str, object]
UrlField = Annotated[str, Field(min_length=1)]
