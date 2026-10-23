# Coil & Cog — AI Content Factory

A Python command-line pipeline that helps make faceless "how machines work" explainer videos for
the **Coil & Cog** channel (`@coilandcog`) on YouTube and TikTok. A human approves every video.

Stages: `discover → ideate → script → generate → assemble → review → publish → learn`.
The full picture is in [`docs/`](docs/): start with `PRD.md`, `ARCHITECTURE.md` and `ROADMAP.md`.

## Quick start (WSL2 Ubuntu)

```bash
# 1. Install uv (manages Python 3.12 and the packages for this project)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the project and its dependencies
uv sync

# 3. Create your local settings file, then fill in the keys
cp .env.example .env
chmod 600 .env

# 4. Check that it works
uv run factory --help
uv run pytest
```

## Everyday commands

```bash
uv run factory <stage>                              # run one pipeline stage
uv run pytest                                       # unit tests
uv run ruff check . && uv run ruff format --check . # lint and format check
```

`.env`, `secrets/` and `data/` are git-ignored and must never be committed.
