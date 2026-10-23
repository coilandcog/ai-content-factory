# Coil & Cog — AI Content Factory

@AGENTS.md
@docs/PRD.md
@docs/ARCHITECTURE.md
@docs/ROADMAP.md

## Critical rules

1. The owner is a beginner in AI agents and automation (comfortable with Python and Java). Explain every change in plain language, define jargon the first time it appears, and always say *why*.
2. Plan before code: start every task by stating the plan (files to touch, approach, how to test) and wait for approval.
3. One ROADMAP task per session. Do not start the next task, and do not add files or features nobody asked for.
4. Never commit secrets. Secrets live only in `.env` and `secrets/` (both git-ignored). Never print secret values in logs or chat.
5. Nothing goes public from code unless `DRY_RUN=false` **and** the content has an approved Gate 2 review. Test uploads are always private or unlisted.
6. If scope, names, stages or data fields change, update the affected docs and add an entry to `docs/DECISIONS.md` in the same task.
7. Platform policy beats growth. If a request risks a policy violation (see `docs/SECURITY_COMPLIANCE.md`), stop and say so plainly.
8. Use `uv` for all Python work (`uv add`, `uv run pytest`). Python 3.12 only.
