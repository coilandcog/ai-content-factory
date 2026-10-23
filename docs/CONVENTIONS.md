# CONVENTIONS.md

*Last updated: 2026-10-22*

## 1. Python

- **Version:** 3.12 (pinned in `.python-version`). All commands go through `uv`: `uv add <pkg>`, `uv run factory …`, `uv run pytest`.
- **Style:** `ruff format` (formatter) and `ruff check` (linter), line length 100. Rules: `E,F,I,B,UP,SIM`.
- **Type hints** on every function signature. Use `from __future__ import annotations` at the top of modules.
- **Data shapes:** Pydantic v2 models for config, API responses and LLM outputs. Plain `dict`s don't cross module boundaries.
- **Paths:** use `pathlib.Path`, always relative to the repo root via `config.paths`.
- **Subprocesses:** `subprocess.run([...], check=True)` with argument lists. Never `shell=True`.
- **No global state** except the loaded config object passed explicitly or via a small `AppContext`.
- **Functions:** keep them under about 40 lines and give each one job. Stage modules expose `run(ctx: AppContext, *, limit: int | None = None) -> StageResult`.
- **Dependencies:** only those listed in `ARCHITECTURE.md` §9 unless a task justifies a new one.

## 2. Naming

| Thing | Convention | Example |
|---|---|---|
| Modules and functions | `snake_case` | `whisper_align.py`, `build_flow_sheet()` |
| Classes | `PascalCase` | `ScriptDraft`, `YouTubeClient` |
| Constants | `UPPER_SNAKE` | `DEFAULT_TIMEOUT_S` |
| CLI commands | kebab-case verbs | `factory flow-log`, `factory run-nightly` |
| Channel slug | kebab-case | `coil-and-cog` |
| Config keys | `snake_case` | `budgets_per_day.gemini_calls` |
| Status and enum values | `snake_case` strings | `script_approved`, `deep_dive` |
| Prompt templates | `<purpose>.j2` | `script_deep_dive.j2`, `flow_shot_cutaway.j2` |
| Migrations | `NNN_description.sql` | `002_add_watch_time.sql` |
| Git branches | `task/<roadmap-id>-<slug>` | `task/1.3-script-generation` |
| n8n workflows | `<scope>__<trigger>__<action>` | `factory__nightly__run-pipeline`, `coil-and-cog__webhook__approve` |

## 3. Logging

- The standard-library `logging` module, configured in `logging_setup.py`.
- **Two outputs:** a readable console line, and JSON lines in `data/logs/factory-YYYY-MM-DD.jsonl`.
- **Required fields:** `ts`, `level`, `run_id`, `stage`, `channel`, `content_id` (if any), `event`, `msg`.
- **Levels:** `DEBUG` for details, `INFO` for each item processed and status change, `WARNING` for skips or fallbacks, `ERROR` when an item failed.
- Never log secrets; the redaction filter is mandatory. Never log full LLM prompts at `INFO`; use `DEBUG`.

## 4. Error handling

`src/factory/errors.py` defines:

```
FactoryError                  # base class
├── ConfigError               # bad YAML/.env → stop immediately
├── IntegrationError          # external API refused (non-retryable 4xx)
│   └── RetryableError        # network, timeout, 429, 5xx → retried 3× (2s/4s/8s)
├── BudgetExceededError       # daily budget would be exceeded → stop the stage, alert
├── PolicyBlockedError        # blocklist hit, similarity too high, missing disclosure → item rejected/archived
├── StatusTransitionError     # a status move DATABASE.md §4 doesn't allow → always a bug
└── QualityCheckError         # automated QA failed → item stays 'generated' with reason
```

- One item failing must never stop the batch. Catch at the item level, set `failed` with a reason, and continue.
- Never use a bare `except:`. Never swallow errors silently; log and record them.
- Status changes go through one function, `db.set_status(conn, content_id, new_status, reason=None)`. It validates the transition against `DATABASE.md` §4 and raises `StatusTransitionError` otherwise. The connection is passed in explicitly (no global state).

## 5. Comments and docstrings

- Docstrings on public functions: one line explaining *what*, plus a line explaining *why* when it isn't obvious.
- Comments explain **why**, not what. Good: `# Flow free clips are 720p; upscale before overlaying 1080p diagrams`. Bad: `# loop over clips`.
- Keep a small number of beginner-friendly comments where a concept is new (e.g. what an FFmpeg filtergraph does).
- No commented-out code in commits.

## 6. Prompts as code

- Prompts live in `prompts/default/*.j2`; channels can override them in `prompts/<slug>/`.
- Every LLM call records `prompt_version` (the template's git short hash).
- Changes to prompts get their own commit and a short note on the expected effect.

## 7. Git

- `main` always works. One branch per ROADMAP task; merge when its definition of done is met.
- Commit messages follow Conventional Commits: `feat(script): add engineer note field`, `fix(assemble): …`, `docs: …`, `test: …`, `chore: …`.
- Never commit `.env`, `secrets/` or `data/`, or generated media outside `assets_static/` and `tests/fixtures/`.

## 8. n8n (Phase 2+, optional)

- n8n only **calls** the CLI (Execute Command node, `wsl` command) and shows results. No business logic in n8n nodes.
- Export workflows as JSON to `n8n/` after every change, using the naming convention from §2.
