# loan-status-agent

Minimal project scaffold for a loan-status agent: discover flows against a legacy lending portal, replay artifacts, apply policy guardrails, and escalate when needed.

## Layout

```
loan-status-agent/
├── pyproject.toml
├── .gitignore
├── .env.example
├── config.py
├── cli.py
├── portal/
│   ├── __init__.py
│   ├── app.py              # Legacy lending portal (stub)
│   └── templates/
│       └── .gitkeep
├── agent/
│   ├── __init__.py
│   ├── surface.py          # Surface protocol (stub)
│   ├── loop.py             # Agent loop (stub)
│   └── llm.py              # LLM client (stub)
├── artifact/
│   ├── __init__.py
│   ├── schema.py           # Artifact schema (stub)
│   └── store.py            # Artifact store (stub)
├── replay/
│   ├── __init__.py
│   ├── engine.py           # Replay engine (stub)
│   └── result.py           # Replay result (stub)
├── policy/
│   ├── __init__.py
│   └── guardrails.py       # Allowlist + risk + redact (stub)
├── escalation/
│   ├── __init__.py
│   └── handoff.py          # Escalation handoff (stub)
├── artifacts/              # Git-tracked on purpose
│   └── .gitkeep
├── evidence/
│   └── .gitkeep
└── tests/
    └── __init__.py
```

## What’s in place

### Packaging (`pyproject.toml`)

- PEP 621 project metadata: name `loan-status-agent`, Python `>=3.11`
- Runtime deps: FastAPI, Uvicorn, Jinja2, Playwright, Pydantic ≥2, pydantic-settings, Anthropic, python-dotenv, Click
- Optional `dev` extras: pytest, pytest-asyncio, httpx
- Console script: `loan-agent` → `cli:main`

### Config

- `.env.example` — `ANTHROPIC_API_KEY`, `PORTAL_PORT`, `PORTAL_SESSION_TTL`, `LOG_LEVEL`
- `config.py` — Pydantic `BaseSettings` with defaults for API key, portal host/port/TTL, log level, artifacts/evidence dirs, headless mode, max steps, and step timeout; loads from `.env`, ignores unknown keys
- `.gitignore` — ignores `.env`, caches, venv, dist, egg-info, and evidence screenshots/JSON; leaves `artifacts/` trackable

### CLI (`cli.py`)

Click group `main` with stub commands that print `Not implemented`:

| Command | Args |
|---------|------|
| `discover` | `goal`, `url` |
| `replay` | `artifact-path`, `params` (JSON string) |
| `escalate-demo` | — |
| `serve-portal` | — |

### Module stubs

Empty `.py` files hold a single comment only (no imports, classes, or docstrings). Packages under `portal`, `agent`, `artifact`, `replay`, `policy`, and `escalation` are ready for implementation. `artifacts/` and `evidence/` are reserved data dirs; portal templates is an empty tree with `.gitkeep`.

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env   # then set ANTHROPIC_API_KEY
```

## Quick check

```bash
python -c "from config import Settings; Settings(); print('OK')"
```

Should print `OK` using defaults when no `.env` is present.

## CLI (stubs)

```bash
loan-agent discover "check loan status" http://127.0.0.1:8080
loan-agent replay artifacts/example.json "{}"
loan-agent escalate-demo
loan-agent serve-portal
```
