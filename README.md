# Requirements Fetcher

Requirements Fetcher turns a public web feature into an evidence-backed specification for building a functionally similar application. It combines browser exploration, product documentation, API specifications, and observed network data to produce frontend, backend, and database requirements.

It is useful when a downstream code-generation service needs a reliable blueprint rather than only a URL or a screenshot.

## What it produces

- Frontend pages, components, states, and user workflows
- Backend endpoints, parameters, response shapes, and business rules
- Database entities, fields, relationships, and indexes
- Traceable evidence for every conclusion: screenshots, browser observations, documentation, API operations, and network schemas

The primary machine-readable output is `requirements.json`. `requirements.md` is a readable review version of the same data.

## How it works

```text
Feature URL + scope + optional source URLs
                ↓
Documentation/API collection + safe browser exploration
                ↓
Evidence indexing and workflow coverage checks
                ↓
Gemini structured requirements synthesis
                ↓
requirements.json + requirements.md + evidence artifacts
```

The browser explores only the workflows named in the configuration. It records whether each workflow was actually covered, rather than treating a single landing-page screenshot as sufficient evidence.

## Quick start

Requirements:

- Python 3.11+
- A Gemini Developer API key

```bash
git clone <your-repository-url>
cd requirements_fetcher

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'

PLAYWRIGHT_BROWSERS_PATH=.browsers python -m playwright install chromium
cp .env.example .env
```

Add your key locally, without quotes or whitespace:

```text
GEMINI_API_KEY=your_key_here
```

Then run the included example:

```bash
PLAYWRIGHT_BROWSERS_PATH=.browsers python -m requirements_fetcher analyze examples/github-issues.yaml
```

The CLI loads `GEMINI_API_KEY` from `.env`. An exported environment variable takes precedence.

## Configuration

At minimum, provide a target URL and a focused description:

```yaml
project:
  name: issue-tracker

target:
  url: https://example.com/issues

scope:
  description: Analyze public issue listing and issue detail behavior.
  workflows:
    - View the issue list
    - Filter issues by status
    - Open an issue and read comments
```

Optional sources improve accuracy:

```yaml
sources:
  documentation:
    - https://example.com/docs/issues
  api_specs:
    - https://example.com/openapi.json
  discovery: true
```

See [examples/github-issues.yaml](examples/github-issues.yaml) for all available options and [DESIGN.md](DESIGN.md) for the data model and collection flow.

## Output

Each analysis writes a timestamped directory under `output/`:

```text
output/<project>-<timestamp>/
├── requirements.json       # Input for a code-generation service
├── requirements.md         # Human-readable review copy
└── evidence/
    ├── index.json
    ├── screenshots/
    ├── browser/
    │   ├── observations.json
    │   ├── actions.json
    │   └── workflow-coverage.json
    ├── api/
    ├── documentation/
    └── network/
```

Screenshots are deduplicated by rendered image content. Persisted GraphQL calls observed in a website are labelled as non-replayable browser transport evidence; they are not treated as public API contracts.

## Safety and limitations

- Public pages only; authentication and CAPTCHA flows are not supported.
- The browser does not intentionally submit forms or modify target application data.
- Private backend implementations and database schemas cannot be observed directly. Equivalent database designs are marked as inferred.
- A run with incomplete workflow coverage should be treated as incomplete evidence, not a complete UI specification.

## Testing

```bash
pytest
```

Optional tests:

```bash
RUN_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=.browsers pytest -m browser
RUN_LIVE_TESTS=1 pytest -m live
```

## Security

Do not commit `.env` files or API keys. `.env` and generated outputs are ignored by Git. See [SECURITY.md](SECURITY.md) for reporting guidance.
