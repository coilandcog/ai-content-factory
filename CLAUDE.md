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

## Working agreements (owner preferences, keep following them)

- Owner test steps go only in the final summary, after the files exist; never in plans. Spell out `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .`.
- Never `source .env` (secrets leaked through shell errors once). Parse it in Python and print only derived facts ("key is set", never the value).
- Update the git-ignored `CHEATSHEET.md` in every task that adds or changes a command.
- Every Flow/video prompt asks for smooth transitions, movement and overall flow.
- Real tests run on a scratch copy of the database (`--config-dir` pointing at a scratchpad copy, `set -euo pipefail`, bash arrays). Never experiment on `data/factory.db`.
- Do not change `budgets_per_day.flow_credits` (50) or `cadence.weekly_plan` (5/week) unless the owner asks. Do not raise Flow limits with borrowed or second accounts.
- Commits: work on a branch, commit only when the owner says "commit", then merge to `main`, push and delete the branch.

## Gotchas (learned while building)

- FFmpeg: the Homebrew build first on `PATH` has no libass; always use `tools.ffmpeg` (`/usr/bin/ffmpeg`, D-065). It also has no `drawtext`, so text is drawn with Pillow (D-056).
- Gemini response schemas reject `gt`/`lt` (exclusiveMinimum) and leave out fields that have defaults. `flash-lite` breaks script rules often, so the script stage re-asks up to 4 times (D-083).
- Source checks: 401/403/429 count as "page exists" only on Wikipedia (D-086); Gemini's guessed Wikipedia titles are replaced via Wikipedia search (D-087).
- `factory status <id> --show script` NARRATION includes the Engineer's Note itself.
- `factory generate --limit N` takes items in ID order; there is no option to pick one item.
- Veo 3.1 Fast costs 20 Flow credits per 8 s clip, so 50 credits/day = 2 clips/day. A 4-clip item needs 2 days.
- QA cannot see picture content: #4 was built from FFmpeg test-pattern stand-ins. Check `data/inbox/flow/` holds real Flow clips.
- VS Code's preview cannot play AAC audio; open renders through Windows Explorer.

## Current state (Claude rewrites this at the end of every task)

*Updated 2026-10-27 (benchmarks).*

- **Phase:** Phase 1 complete; Phase 2 started. Task 2.1 (Telegram bot) done. Script fixes D-083 to D-090; CLAUDE.md knowledge D-091; film-shot Flow prompts D-092; `factory drop` + submarines blocklisted D-093; benchmark list D-094 (branch `feat/benchmarks`, awaiting owner "commit"). **Next ROADMAP task: 2.2** (YouTube OAuth + one unlisted test upload).
- **Published:** #5 alternator (YouTube + TikTok), #6 turbocharger (YouTube `shorts/rtzPTV6B0KQ`; TikTok post not recorded yet).
- **Real items:**

| # | Machine | Format | Status | Note |
|---|---|---|---|---|
| 5, 6 | alternator, turbocharger | short | published | `benchmarks` in the YAML (D-094); #6 TikTok link still to record |
| 8 | CVT gearbox | short | awaiting_clips | old prompts: run `factory generate --restage 8` first; 4 clips |
| 10 | jet engine | deep_dive | awaiting_clips | old prompts: `--restage 10` first; 3 clips; recommended next video |
| 9, 12, 13, 14 | differential, landing gear, tower crane, e-car motor | | idea | |
| 1, 2, 3, 4, 7, 11 | | | rejected / failed / archived | history only (#4 zipper and #11 submarine dropped, D-093) |

- **Open items:** `factory retry` is not built (a failed item is reset by hand); a gemini-3.8-flash script experiment is pending (needs owner go); Flow capacity (2 clips/day) cannot keep up with 5 videos/week; channel description/banner + per-pillar playlists suggested to the owner (manual, no code); flash-lite film shots vary between runs (restage again if a shot reads wrong). DB backup before the drops: `data/factory.before-drop-2026-10-27.db`.
