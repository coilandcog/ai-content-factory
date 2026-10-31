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
- Veo 3.1 Fast costs 20 Flow credits per 8 s clip, so 50 credits/day = 2 clips/day. A 4-clip item needs 2 days. Flow images cost 0 credits (owner's plan), so check compositions as images first.
- What works: compact car parts + short alternator-style prompts (D-105). Big or complex machines (submarine, jet engine) fail whatever the prompt.
- A roaming camera makes Veo re-imagine complex machines (exhaust at the intake, rings appearing): story shots keep cameras still except product shots and chain clips through approved images (D-104).
- Veo keeps one compact machine filling the frame consistent; it loses track of big scenes (whole vehicles, landscapes, flying/diving) — D-095. Head-on views of an inlet make flows run backwards; show flows side-on with a named screen direction — D-096.
- QA cannot see picture content: #4 was built from FFmpeg test-pattern stand-ins. Check `data/inbox/flow/` holds real Flow clips. `factory clips review` (Gemini vision, D-097) catches big errors (wrong machine) but missed a subtle soft rotor.
- VS Code's preview cannot play AAC audio; open renders through Windows Explorer.
- Scratch copies of `data/factory.db`: use `sqlite3 -readonly data/factory.db ".backup '<copy>'"`; a plain `cp` while something writes gave a malformed copy (2026-10-31).
- Tests must never use the repo root as `AppContext.root` when a stage writes asset folders: a series test wrote `data/assets/coil-and-cog/000002` into the real data (removed); use `tmp_path` and copy `prompts/`.
- Never run `ln -s <dir> <link>` twice: the second run puts a self-link inside the real folder (`prompts/prompts`), and copytree then loops (2026-10-31).
- Blueprint films (D-111): every number on screen must be in the module's `FACTS` with a source page (or a calculated `LIVE` readout); check numbers against two sources and show a range when they disagree.

## Current state (Claude rewrites this at the end of every task)

*Updated 2026-10-31.*

- **Phase:** Phase 1 complete; Phase 2 started. Task 2.1 (Telegram bot) done. Script fixes D-083 to D-090; CLAUDE.md knowledge D-091; film-shot Flow prompts D-092; `factory drop` + submarines blocklisted D-093; benchmark list D-094; close framing + image-first Flow sheet D-095; flow direction + `--restage N --shots` D-096; clip review `factory clips review` D-097 = ROADMAP 2.13 done early; prompt versions D-098 = 2.14; 2.15 code-drawn flows **parked** (D-099: looks like a diagram, owner stays on Flow; if ever built, keep only forward points). Reference library D-100 = 2.20 (5 pending references from the owner's own stills). Manual metrics D-101 = first part of 2.5 (on main, migration 003 applied). **2.2 done (D-102)**: token for @coilandcog in `secrets/coil-and-cog/google_token.json` (YouTube scopes only; Drive needs its own sign-in in 2.10); test upload worked and was deleted. **2.18 lessons (D-103), story shots 2.16 (D-104) and brief prompts as default (D-105), variety fix (D-106), reshoot (D-107) and lessons in Flow prompts (D-108) on `main` (d80552d)**; 7 lessons approved in the real DB. **D-109 fairer review (visible vs expert, majority of 3) + listening references (migration 006, long reels shrunk whole up to 10 min) + research (CONTENT_STRATEGY §12) on `main`**; all 43 references approved 2026-10-31 (newest 8 = videos #37-44 shape prompts); proposed research changes 1-5 need owner approval. **D-110 on `main`**: brief-mode check images no longer stand an explain shot upright with an intake/outlet; script rules keep that only for machines something flows through. #8 sheet re-printed (image 02 fixed; issue times of unchanged prompts restored by hand after D-109's fingerprint change). **D-111 blueprint films = ROADMAP 2.23 on `main`** (code-drawn films, verified on-screen facts, migration 007, `factory new --style`, `factory style <id>`). **D-112 topic series = ROADMAP 2.24 on `main`** + **D-113 truth fixes on `main`** + **D-114 complete opening frames on `main`** (blueprint scripts skip shot rules, digits only, facts with meanings, parts wait for earlier parts, 3-5 parts): migration 008 (`series`, `contents.series_id/part_n`), parts from the drawing's `PARTS` (jet engine: 3), `factory series new/show`, part-aware scripts (closing statement naming the next part, only verified numbers), siblings not similarity-blocked, titles "Jet Engine, Part n/3: ..." with teaser/earlier-part links; every command refuses a database older than the code. Real DB 2026-10-31: version 8; **jet series #1 = #17/#18/#19** (teaser #16): #17 part 1 scripted v1 (owner: drop "titanium", write own note); #18/#19 v1 have errors, redo recorded but not written (Gemini budget 60/60 used): redo them tomorrow; **#8 CVT and #16 jet overview approved at Gate 2 (2026-10-31)**, ready to post (#16 first; #16 description/caption corrected by hand);  **#10 jet deep dive (Flow) paused**, old clips parked in `000010/parked_2026-10-31/`. **#15 differential short**: script_approved, style blueprint, waits until a differential is drawn (#9 deep-dive differential stays an idea). Next code: 2.22 prompt pre-check, then 2.3. Car-parts switch recommended (owner said go to car parts as next videos). Car-parts switch proposed, on hold (owner: don't drop anything for now). Learning from results = 2.5 API → 2.9 → 3.1. New roadmap tasks 2.14–2.21 cover all of the owner's A–O ideas except L (paid model). **Next ROADMAP task after 2.2: 2.3** (scheduled publishing).
- **Published:** #5 alternator (YouTube + TikTok), #6 turbocharger (YouTube `shorts/rtzPTV6B0KQ`; TikTok post not recorded yet).
- **Real items:**

| # | Machine | Format | Status | Note |
|---|---|---|---|---|
| 5, 6 | alternator, turbocharger | short | published | `benchmarks` in the YAML (D-094); #6 TikTok link still to record |
| 16 | jet engine | short | approved | blueprint; teaser of the jet series; post first |
| 17 | jet engine part 1 | short | generated | approved script (owner's note), re-drawn with the D-114 opening; `assemble` after the Gemini budget resets (metadata), then Gate 2 |
| 18, 19 | jet engine parts 2-3 | short | scripted | series #1; need a redo (wrong turbine, wrong temperature) once the Gemini budget resets |
| 15 | differential | short | script_approved | blueprint; waits for a differential drawing |
| 8 | CVT gearbox | short | approved | blueprint; post |
| 10 | jet engine | deep_dive | awaiting_clips | **paused**; old clips parked in `parked_2026-10-31/`: every Flow attempt failed (D-104/D-105); not dropped (owner) |
| 9, 12, 13, 14 | differential, landing gear, tower crane, e-car motor | | idea | |
| 1, 2, 3, 4, 7, 11 | | | rejected / failed / archived | history only (#4 zipper and #11 submarine dropped, D-093) |

- **Open items:** `factory retry` is not built (a failed item is reset by hand); a gemini-3.8-flash script experiment is pending (needs owner go); Flow capacity (2 clips/day) cannot keep up with 5 videos/week; channel description/banner + per-pillar playlists suggested to the owner (manual, no code); flash-lite film shots vary between runs (restage again if a shot reads wrong). DB backup before the drops: `data/factory.before-drop-2026-10-27.db`.
