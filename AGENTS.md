# AGENTS.md — Operating instructions for AI coding agents

This file is for any AI coding assistant (Claude Code or others) working in this repository.

## 1. Project context (read this first)

- **What:** an automation pipeline that produces faceless, AI-generated "how machines work" explainer videos for the channel **Coil & Cog** (`@coilandcog`) on YouTube and TikTok.
- **Owner:** one person, a beginner in agents and automation, strong in Python and Java, with an engineering/telecom background. The owner reviews every video before it is published.
- **Budget:** €0 at launch. Free tiers, open source and local tools only. Paid upgrades are documented but not active.
- **Pipeline stages (fixed names):** `discover → ideate → script → generate → assemble → review → publish → learn`.
- **Multi-niche:** channels and niches are added with a YAML profile in `config/channels/<slug>.yaml`, never by changing code.
- **Environment:** Windows + WSL2 (Ubuntu), repo at `~/ai-content-factory`, Python 3.12 via `uv`, SQLite at `data/factory.db`.

## 2. Docs to read before making changes

| Always | When relevant |
|---|---|
| `docs/PRD.md`: scope and non-goals | `docs/CONTENT_STRATEGY.md`: anything touching ideas, scripts, scoring |
| `docs/ARCHITECTURE.md`: stages, folders, config model | `docs/DESIGN_SYSTEM.md`: visuals, subtitles, prompts, audio |
| `docs/ROADMAP.md`: find the current task | `docs/DATABASE.md`: any schema or status change |
| `docs/CONVENTIONS.md`: how code must look | `docs/INTEGRATIONS.md`: any external API call |
| | `docs/SECURITY_COMPLIANCE.md`: secrets, publishing, disclosure, licensing |
| | `docs/TESTING.md`: how to prove the task works |
| | `docs/DECISIONS.md`: why things are the way they are |

## 3. Rules

1. **One task at a time.** Work only on the ROADMAP task the owner names. If you notice other problems, list them at the end and don't fix them.
2. **Plan first.** Before writing code, reply with: goal, files to create or change, approach, test plan, and any doc updates. Wait for "go".
3. **No unrequested files.** Don't create helpers, configs, docs or dependencies the task doesn't need.
4. **Explain like a teacher.** Use short explanations, define new terms, and say *why* each choice was made.
5. **Docs stay true.** If you change a name, stage, status, table, field, config key or integration, update every affected doc and add a `DECISIONS.md` entry in the same task.
6. **Secrets.** Never hard-code, log, print or commit secrets. Use `.env` and `secrets/` only. If a secret is ever exposed, tell the owner immediately so it can be rotated (replaced with a new one).
7. **Safety switches.** Respect `DRY_RUN`. Never make anything public without an approved Gate 2 review. Test uploads go to private or unlisted.
8. **Policy first.** Refuse and explain any change that would scrape against a platform's terms of service, fake engagement, reuse other creators' content, skip AI disclosure, or remove a human approval gate earlier than the ROADMAP allows.
9. **Boring tech.** Prefer the libraries already chosen in `docs/ARCHITECTURE.md`. Adding a new dependency needs a one-line reason in the plan.
10. **Small diffs.** Keep each change reviewable in a few minutes, and avoid unrelated refactors.

## 4. Definition of done (every task)

- [ ] The ROADMAP task's acceptance criteria are met.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run pytest` passes. New logic has unit tests, and external calls are faked in unit tests.
- [ ] The task's "How I test it myself" steps have been written out for the owner and work.
- [ ] Docs are updated if anything in rule 5 changed, with a `DECISIONS.md` entry if a decision changed.
- [ ] The *Current state* section of `CLAUDE.md` is rewritten (D-091).
- [ ] No secrets appear in the diff, and `.env.example` is updated if new settings were added.
- [ ] A short summary is given to the owner: what changed, why, how to test it, and what's next.
