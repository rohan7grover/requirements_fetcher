# Requirements Fetcher — System Design

## 1. Summary

Requirements Fetcher analyzes a specific feature of an existing web application and produces an implementation-ready knowledge base for a separate code-generation service.

For example, a user can ask the system to analyze GitHub Issues. The system collects evidence from the live application, available documentation, and API descriptions. It then uses an LLM to produce requirements for:

- Frontend pages, components, states, and workflows
- Backend APIs, business rules, validation, and permissions
- Database entities, fields, relationships, and constraints

The system is generic. GitHub Issues is an example target, not a hardcoded integration.

```text
User input
    ↓
Documentation and API collection
    ↓
Browser exploration and evidence capture
    ↓
LLM-based requirements generation
    ↓
requirements.json + requirements.md + evidence
```

## 2. Objective

Given a feature description and an accessible application URL, generate a structured full-stack specification that another service can use to build a functionally similar feature.

The system is successful when it can:

1. Accept a generic configuration file.
2. Inspect the requested feature using a browser.
3. Collect relevant documentation and API information when supplied or discoverable.
4. Store screenshots and other evidence.
5. Generate valid frontend, backend, and database requirements.
6. Clearly separate observed facts from inferred design decisions.

## 3. Scope

### Current scope

- Command-line interface
- YAML configuration file
- Public web applications
- Public web applications that do not require login
- Documentation page collection
- OpenAPI/Swagger collection when a specification is available
- Browser screenshots and page observations
- Fetch/XHR network request capture
- LLM-based requirements generation
- JSON and Markdown outputs
- Local filesystem storage

### Non-goals

- Code generation
- Web dashboard
- Cloud deployment
- Multiple concurrent analysis jobs
- Vector database or RAG system
- Guaranteed discovery of all product documentation
- Authenticated exploration and automatic CAPTCHA handling
- Automatic submission of destructive or irreversible actions
- Exact reconstruction of private backend code or database schemas

## 4. User Experience

### 4.1 What the user must provide

Only two inputs are required:

1. **Target URL:** A URL that opens the feature to analyze.
2. **Feature scope:** A plain-language description of what should be cloned.

Example minimal configuration:

```yaml
project:
  name: github-issues-clone

target:
  url: https://github.com/openai/openai-python/issues

scope:
  description: >
    Analyze the issue list, status and label filters,
    issue detail page, comments, and create-issue form.
```

The URL alone is not sufficient because a web application can contain many unrelated features. The scope tells the system where to focus.

### 4.2 Optional user input

The user can improve accuracy by providing:

- Important workflows to inspect
- Known documentation pages
- OpenAPI or Swagger specification URLs
- GraphQL documentation or schema URLs
- Allowed domains
- Features that must be excluded
- Maximum browser actions or pages

Example complete configuration:

```yaml
project:
  name: issue-tracker-clone

target:
  url: https://github.com/openai/openai-python/issues
  allowed_domains:
    - github.com
    - api.github.com

scope:
  description: >
    Clone issue listing, filtering, issue details,
    comments, labels, and the create-issue form.
  workflows:
    - View the issue list
    - Filter open issues by label
    - Open an issue and read its comments
    - Inspect the new-issue form without submitting it
  exclude:
    - Repository settings
    - Pull requests
    - Destructive actions

sources:
  documentation:
    - https://docs.github.com/en/issues
    - https://docs.github.com/en/rest/issues
  api_specs:
    - https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json

browser:
  headed: false
  max_pages: 8
  max_actions: 12

llm:
  provider: gemini
  profile: development
  lightweight_model: gemini-3.5-flash-lite
  development_model: gemini-3.5-flash-lite
  showcase_model: gemini-3.6-flash
  max_input_tokens: 30000
  max_output_tokens: 6000

output:
  root_directory: output
```

API keys are provided through environment variables and are never stored in the configuration file:

```bash
export GEMINI_API_KEY="..."
```

For local development, the CLI also loads `GEMINI_API_KEY` from an ignored `.env` file in the current directory or alongside the configuration file. An explicitly exported environment variable takes precedence.

The development profile uses Gemini 3.5 Flash-Lite for cost-efficient structured work. A higher-capability profile can be selected without changing the code:

```yaml
llm:
  provider: gemini
  profile: showcase
```

The higher-capability profile uses Gemini 3.6 Flash for requirements synthesis when additional reasoning quality is valuable. Browser-action model selection remains independently configurable.

### 4.3 Running an analysis

```bash
python -m requirements_fetcher analyze config.yaml
```

Example terminal output:

```text
[1/5] Configuration loaded
[2/5] Documentation and API sources collected
[3/5] Browser exploration completed: 6 pages, 9 actions
[4/5] Requirements generated and validated
[5/5] Results written to output/github-issues-clone-20260807-143000/
```

Authenticated exploration is not currently supported. The tool reports a limitation when the requested feature cannot be reached as a public page.

## 5. Generic System Behavior

The core system contains no GitHub-specific selectors, endpoints, or data models. It works with generic web concepts:

- Pages and URLs
- Links, buttons, tabs, dialogs, and forms
- Accessibility labels and visible text
- HTTP requests and JSON responses
- OpenAPI operations and schemas
- Documentation pages
- User workflows and state transitions

Some targets may eventually benefit from optional product-specific adapters, but adapters are not required by the core pipeline.

## 6. Sources of Knowledge

The system can use the following sources, ordered roughly by reliability:

| Source | Information obtained |
|---|---|
| User-provided scope | What must and must not be analyzed |
| Official product documentation | Workflows, permissions, validation, and intended behavior |
| OpenAPI/Swagger specification | API paths, methods, parameters, and schemas |
| GraphQL schema/documentation | Queries, mutations, objects, and relationships |
| Live application UI | Pages, components, controls, states, and navigation |
| Browser network activity | Actual requests, response shapes, errors, filtering, and pagination |
| Official SDK types | Request and response models |
| Public source code/tests | Additional behavior when the target is open source |
| LLM inference | Missing backend and database design, marked as inferred |

The system prioritizes user-supplied sources, then attempts lightweight discovery such as `llms.txt`, `sitemap.xml`, and common OpenAPI locations. It does not attempt an unrestricted internet-wide search.

## 7. Processing Flow

### Step 1: Load and validate configuration

The system verifies that:

- The target URL is valid.
- A non-empty feature scope is present.
- Exploration limits are positive and bounded.
- Allowed domains include the target domain.
- The requested LLM provider is configured.

### Step 2: Collect documentation and API specifications

For each supplied or discovered source, the system:

- Downloads the content.
- Converts useful documentation into clean Markdown.
- Parses OpenAPI JSON or YAML.
- Retains operations related to the requested feature.
- Records the original source URL.

### Step 3: Explore the application

Playwright opens the target URL and performs a limited exploration.

For each meaningful page or state, it records:

- Current URL and page title
- Screenshot
- Visible text summary
- Interactive elements
- Form fields, labels, placeholders, and required markers
- Navigation caused by an action
- Loading, empty, disabled, success, and error states when observed
- Relevant fetch/XHR requests and responses

The LLM may choose from a constrained set of safe actions such as click, type, select, go back, or stop. The application never executes arbitrary LLM-generated JavaScript.

### Step 4: Normalize evidence

All collected information is converted to common evidence records:

```json
{
  "id": "ev-014",
  "type": "network_response",
  "source": "https://example.com/api/issues?status=open",
  "summary": "Returns a paginated collection of open issues",
  "artifact": "evidence/network/res-014.json",
  "collected_at": "2026-08-07T14:30:00+05:30"
}
```

A screenshot is one type of evidence. Evidence also includes page observations, documentation, API descriptions, actions, and network records.

Screenshots are deduplicated by their rendered PNG hash. Network calls to undocumented persisted GraphQL transports are retained for data-shape evidence but marked non-replayable; they must not be presented as public API contracts for the generated clone.

### Step 5: Generate requirements

The LLM receives:

- User scope
- Normalized evidence summaries
- Relevant documentation
- Relevant API operations
- Browser observations

It returns data matching a strict schema. Every important requirement includes:

- Its description
- Whether it was observed or inferred
- Confidence level
- References to supporting evidence

### Step 6: Validate and save results

The generated JSON is validated before being saved. A Markdown version is rendered from the validated JSON so the two formats remain consistent.

## 8. Requirements Produced

### 8.1 Frontend requirements

The frontend section describes:

- Pages and routes
- Layout regions
- Components and component hierarchy
- Displayed data
- Buttons, menus, tabs, dialogs, and forms
- Navigation and interactions
- Loading, empty, success, disabled, and error states
- Search, filters, sorting, and pagination
- Form validation visible to the user
- Screenshots that act as visual references

How it helps: the downstream generator can create routes, components, forms, client-side state, and API integrations.

### 8.2 Backend requirements

The backend section describes:

- Required API operations
- Request parameters and bodies
- Response shapes
- Authentication and authorization
- Validation rules
- Business rules
- State transitions
- Filtering, sorting, and pagination behavior
- Expected errors
- Side effects and external integrations when observed

How it helps: the downstream generator can create controllers, services, request validation, authorization checks, and API tests.

### 8.3 Database requirements

The database section describes:

- Entities
- Fields and probable types
- Required and optional fields
- Relationships
- Unique and foreign-key constraints
- Status values
- Timestamps and audit history
- Suggested indexes based on filters and lookup behavior

How it helps: the downstream generator can create models, migrations, relationships, constraints, and indexes.

The original application's database is private and cannot normally be observed. Database requirements are therefore an implementation-equivalent proposal and are marked as inferred unless supported by public source code or schema documentation.

## 9. Final Output

Each run creates an independent output directory:

```text
output/
└── github-issues-clone-20260807-143000/
    ├── input.yaml
    ├── requirements.json
    ├── requirements.md
    └── evidence/
        ├── index.json
        ├── llm-usage.json
        ├── documentation/
        │   ├── issues-user-guide.md
        │   └── issues-api-reference.md
        ├── api/
        │   └── relevant-operations.json
        ├── browser/
        │   ├── observations.json
        │   ├── actions.json
        │   └── workflow-coverage.json
        ├── network/
        │   ├── requests.json
        │   └── response-schemas.json
        └── screenshots/
            ├── 001-issue-list.png
            ├── 002-filter-menu.png
            └── 003-issue-detail.png
```

### 9.1 `requirements.json`

This is the primary machine-readable output consumed by the future code-generation service.

Simplified example:

```json
{
  "project": {
    "name": "github-issues-clone",
    "target_url": "https://github.com/openai/openai-python/issues",
    "scope": "Issue list, filtering, details, comments, and create form"
  },
  "generation": {
    "provider": "gemini",
    "profile": "development",
    "synthesis_model": "gemini-3.5-flash-lite"
  },
  "frontend": {
    "pages": [
      {
        "name": "Issue List",
        "route": "/issues",
        "components": [
          "SearchInput",
          "StatusFilter",
          "LabelFilter",
          "IssueList",
          "IssueRow",
          "Pagination"
        ],
        "states": ["loading", "loaded", "empty", "error"],
        "evidence_ids": ["ev-001", "ev-004"]
      }
    ],
    "workflows": [
      {
        "name": "Filter issues by status",
        "steps": [
          "Open the status filter",
          "Select a status",
          "Display matching issues"
        ],
        "source": "observed",
        "confidence": "high",
        "evidence_ids": ["ev-006", "ev-007"]
      }
    ]
  },
  "backend": {
    "api_endpoints": [
      {
        "method": "GET",
        "path": "/api/issues",
        "purpose": "List and filter issues",
        "query_parameters": ["status", "label", "sort", "page"],
        "source": "inferred_from_observed_network_call",
        "confidence": "high",
        "evidence_ids": ["ev-007"]
      }
    ],
    "business_rules": [
      {
        "description": "An issue title is required",
        "source": "observed",
        "confidence": "high",
        "evidence_ids": ["ev-012"]
      }
    ]
  },
  "database": {
    "entities": [
      {
        "name": "Issue",
        "fields": [
          {"name": "id", "type": "uuid", "required": true},
          {"name": "number", "type": "integer", "required": true},
          {"name": "title", "type": "string", "required": true},
          {"name": "body", "type": "text", "required": false},
          {"name": "status", "type": "enum", "required": true},
          {"name": "author_id", "type": "foreign_key", "required": true},
          {"name": "created_at", "type": "timestamp", "required": true}
        ],
        "source": "inferred",
        "confidence": "medium",
        "evidence_ids": ["ev-004", "ev-007"]
      }
    ],
    "relationships": [
      "A User can create many Issues",
      "An Issue can have many Comments",
      "An Issue can have many Labels"
    ]
  },
  "unknowns": [
    "The original database technology is not observable",
    "Internal caching behavior was not observed"
  ]
}
```

### 9.2 `requirements.md`

This is a human-readable rendering of `requirements.json`. It exists so a developer or reviewer can quickly inspect, share, and correct the result. It is not an independent source of truth.

```text
requirements.json → machine input and source of truth
requirements.md   → human review version
evidence/         → supporting proof and collected artifacts
```

### 9.3 `evidence/`

This directory explains how the system reached its conclusions. It contains screenshots, documentation, API information, browser observations, and network records.

Sensitive headers, authentication tokens, cookies, and passwords must be removed before evidence is stored.

## 10. Architecture

```text
requirements_fetcher/
  ├── ConfigLoader
  ├── DocumentationCollector
  ├── ApiSpecCollector
  ├── BrowserCollector
  │     ├── ScreenshotCapture
  │     ├── PageObserver
  │     └── NetworkRecorder
  ├── EvidenceStore
  ├── RequirementsGenerator
  ├── RequirementsValidator
  └── MarkdownRenderer
```

Recommended implementation stack:

- Python 3.11+
- Playwright with Chromium
- Gemini Developer API through the `google-genai` Python SDK
- Pydantic for configuration and output validation
- PyYAML for configuration
- Local filesystem for storage

### 10.1 Gemini model strategy

The system uses two configurable model roles:

| Role | Default model | Higher-capability model | Purpose |
|---|---|---|---|
| Lightweight | `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` | Browser action selection, evidence classification, and small structured tasks |
| Synthesis | `gemini-3.5-flash-lite` | `gemini-3.6-flash` | Producing the final frontend, backend, and database requirements |

Gemini 3.5 Flash-Lite is the default because it is a cost-efficient model for high-volume processing, document extraction, and structured JSON. Gemini 3.6 Flash can be selected for synthesis when stronger reasoning is worth the additional quota and cost.

The model names live in configuration rather than source code so they can be updated when Google changes its model catalog.

### 10.2 Token and cost controls

To control usage without weakening the final result:

- Send compact accessibility/page summaries instead of complete HTML documents.
- Store every screenshot as evidence, but send images to Gemini only when text and DOM evidence are insufficient.
- Filter OpenAPI operations and documentation by the requested feature before calling Gemini.
- Deduplicate repeated network responses and page content.
- Keep browser action selection prompts small and use Flash-Lite for every action.
- Use one final synthesis request rather than separate repeated requests for every section.
- Set explicit browser action, input token, and output token limits.
- Use the SDK's token-counting method before final synthesis and trim low-priority evidence when the input exceeds the configured budget.
- Record prompt, output, and total token usage for each call in `evidence/llm-usage.json`.

Gemini structured output mode is used with a JSON schema generated from the Pydantic requirements model. The returned JSON is still validated locally before it becomes `requirements.json`.

## 11. Safety and Limitations

- Exploration remains within configured domains.
- Browser actions are capped by configuration.
- Destructive actions are never performed automatically.
- Forms can be inspected without being submitted.
- Authentication credentials are not collected or stored.
- Cookies, tokens, and sensitive headers are redacted.
- Site terms, access restrictions, and rate limits must be respected.
- Requirements may be incomplete when features are hidden by permissions or unavailable test data.
- Backend and database sections represent a compatible implementation, not guaranteed copies of private internals.

## 12. Three-Hour Build Order

1. Create the CLI, YAML models, and output directory structure.
2. Implement Playwright page observation, screenshots, and network recording.
3. Implement supplied documentation and OpenAPI collection.
4. Define and validate the `requirements.json` Pydantic schema.
5. Generate requirements with one LLM call.
6. Render `requirements.md` from the JSON.
7. Validate an end-to-end analysis against a public feature such as GitHub Issues.
