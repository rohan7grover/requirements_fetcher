# Requirements Fetcher

Generate evidence-backed frontend, backend, and database requirements for a feature in a public web application.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
PLAYWRIGHT_BROWSERS_PATH=.browsers playwright install chromium
cp .env.example .env
```

Paste your key into the ignored `.env` file. The CLI loads `GEMINI_API_KEY` from `.env` automatically; an explicitly exported environment variable takes precedence.

## Run

```bash
PLAYWRIGHT_BROWSERS_PATH=.browsers python -m requirements_fetcher analyze examples/github-issues.yaml
```

The development profile uses `gemini-3.5-flash-lite`. Change `llm.profile` to `showcase` to use `gemini-3.6-flash` for the final requirements synthesis while retaining Flash-Lite for browser decisions.

Each run writes `requirements.json`, a human-readable `requirements.md`, screenshots, browser observations, network schemas, source documents, and LLM token usage under `output/<project>-<timestamp>/`.

The prototype explores public pages only. It never submits forms or intentionally changes target application data.

## Test

```bash
pytest
```

To run the optional Gemini smoke test:

```bash
RUN_LIVE_TESTS=1 pytest -m live
```

To run the local browser smoke test after installing Chromium:

```bash
RUN_BROWSER_TESTS=1 pytest -m browser
```
