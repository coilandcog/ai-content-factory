# ROADMAP.md

*Last updated: 2026-10-23.*

Effort estimates assume a beginner working with Claude Code. Each task fits in **one Claude Code session of about 2 hours or less**. Do the tasks in order: one working stage before the next.

**Task format:** each task has an ID, a goal, acceptance criteria (AC), and "How I test it myself" (Test). Tasks marked **[Owner]** are done by you in a browser or app, with no code.

---

## Phase 0: Setup (weeks 1–2, about 12–15 h)

**Milestone AC:** accounts exist with 2-step verification; the repo has a skeleton with config, database, logging and brand assets; `uv run pytest` passes; `uv run factory status` works.

| ID | Task | Acceptance criteria | How I test it myself |
|---|---|---|---|
| 0.1 | **[Owner]** Create the channel Google account with 2-step verification, and set up a password manager entry | The account exists, 2-step verification is on, backup codes are stored offline | Sign out, then sign in again using 2-step verification |
| 0.2 | **[Owner]** Create the YouTube channel "Coil & Cog" (`@coilandcog`), phone-verify it, create the TikTok **Personal** account `@coilandcog` with 2-step verification, and search the trademark (closes OQ-5) | Both handles are claimed; YouTube "intermediate features" are enabled; the trademark result is noted in `DECISIONS.md` | Open `youtube.com/@coilandcog` and `tiktok.com/@coilandcog` in a private browser window |
| 0.3 | **[Owner]** Create the Telegram bot with @BotFather | Bot token saved in the password manager | Send `/start` to the bot (no reply is expected yet) |
| 0.4 | **[Owner + Claude guide]** Google Cloud project `coil-and-cog`: enable YouTube Data API v3, YouTube Analytics API and Drive API; create a YouTube **API key**; publish legal pages on GitHub Pages (`coilandcog.github.io`: home, privacy, terms; Google requires a homepage and privacy-policy URL before an External app can go **In production**); set up the OAuth consent screen (External, **In production**); create a Desktop OAuth client | `google_client_secret.json` is in `secrets/coil-and-cog/`; the API key is in `.env` | `ls secrets/coil-and-cog/` shows the file; `git status` doesn't show it |
| 0.5 | **[Owner + Claude]** Create the Gemini API key in AI Studio; record the real free-tier limits; choose `llm.model`; re-read the Flow terms (closes OQ-1, OQ-10) | The limits table is filled in `INTEGRATIONS.md` §1; `llm.model` is set; a `DECISIONS.md` entry is added | AI Studio shows the key; the docs show the numbers |
| 0.6 | Repo skeleton: `git init`, `uv init`, `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, folder tree from `ARCHITECTURE.md` §10, ruff, pytest, and a `factory --help` CLI | `uv run factory --help` lists the stage commands (stubs); `uv run pytest` passes 1 smoke test | Run both commands; `git status` doesn't list `.env`, `data/` or `secrets/` |
| 0.7 | Config: `settings.yaml`, `channels/coil-and-cog.yaml`, Pydantic models, `factory config check` | Valid config prints a summary; a broken YAML prints a clear error | Delete one required key, run the check, see the error, then restore it |
| 0.8 | Database: `migrations/001_init.sql` (all tables in `DATABASE.md`), `db.py`, `set_status()` with transition rules, `factory db init` | Tables are created; the channel row is inserted from YAML; an illegal transition raises an error in tests | `uv run factory db init` then `sqlite3 data/factory.db ".tables"` |
| 0.9 | Logging, `runs`, `errors.py`, `budgets.py`, the `DRY_RUN` flag, `factory status` | Every command writes a run row and a JSONL log line; a budget overrun raises `BudgetExceededError` in a test; secrets are redacted | Run `factory status`, open `data/logs/` and check the line |
| 0.10 | Brand assets: download the fonts, build the logo PNG with Pillow, build the 1 s sting MP4s (9:16 and 16:9) with FFmpeg | Files are in `assets_static/`; a license note for the fonts is present | Open the logo and play the sting on your phone |

## Phase 1: MVP, manual-assisted (weeks 3–6, about 25–30 h)

**Milestone AC:** from a topic you type, the CLI produces an approved YouTube render and TikTok render that pass QA. You upload them by hand. **First public post in week 3–4.** At least 8 videos are posted by the end of week 6.

We build a "walking skeleton" first: the thinnest path from topic to video (1.1–1.12), and only then the smarter parts (1.13–1.15).

| ID | Task | Acceptance criteria | How I test it myself |
|---|---|---|---|
| 1.1 | `factory new --object alternator --format short --pillar vehicles` creates a content row as an `idea` | The row exists with the chosen format and pillar | `factory status <id>` shows `idea` |
| 1.2 | Gemini client plus Jinja2 prompt loader (with channel overrides) plus fake client | Unit tests pass with the fake; one integration call returns valid JSON | `uv run pytest -m integration -k gemini` |
| 1.3 | `factory script`: `ScriptDraft` schema (hook, body, Engineer's Note draft, loop line, shot list, at least 2 sources); blocklist check; word count within format bounds | The idea becomes `scripted`; sources are stored; an off-limits topic is rejected | Run it for "alternator" and read the script in `factory status <id> --show script` |
| 1.4 | Kokoro voiceover plus a **voice audition**: render the same paragraph with `am_michael`, `am_adam` and `am_onyx` (closes OQ-7) | `voice.wav` is 48 kHz at −14 LUFS; you choose the voice in the YAML | Listen to the 3 samples on your phone and pick one |
| 1.5 | Gate 1 in the terminal: `factory review --gate script` shows the script and opens the Engineer's Note in your editor; approve / reject / redo with a reason | Transitions work; a redo creates script version 2 | Do one redo and one approve |
| 1.6 | `factory generate`, part 1: render `flow_sheet.md` from the `DESIGN_SYSTEM.md` §10 templates; ingest the inbox; `factory flow-log` | The sheet lists each Flow shot with its full prompt; status is `awaiting_clips` until all `shot_NN.mp4` files exist | Paste a prompt into Flow, save the clip into the inbox, run generate, and see the status change |
| 1.7 | Word timing (faster-whisper aligned to the script) and an ASS subtitle file with the cyan active word | Subtitle words match the script spelling; timing within ±0.15 s | Play `voice.wav` with `subs.ass` in mpv or VLC |
| 1.8 | Stills animation (Ken Burns/parallax) and diagram overlays (Pillow PNG layers) from the shot list | Every `still` and `diagram` shot produces a file; labels stay inside the safe zones | Open the frames and check that the labels are readable |
| 1.9 | `factory assemble`: clips + stills + diagrams + voice + subtitles + sting; YouTube render with an Audio Library track (ducked); TikTok render without music; 720p preview | Both renders are created; the TikTok render has no music | Watch both on your phone with sound and without |
| 1.10 | Automated QA (`media/qa.py`) plus automatic cover-frame pick | A failing check keeps the status `generated` with a reason; a pass sets `assembled` | Break something (e.g. delete `subs.ass`), run, and see the failure reason |
| 1.11 | Metadata (titles, captions, description, hashtags; AI flags) plus Gate 2 in the terminal | `metadata.json` is complete; approving sets `approved` | Read `metadata.json`; approve |
| 1.12 | **First real video end-to-end**; manual upload to YouTube (AI label on) and TikTok (sound added, AI label on); `factory publish --manual` records the posts | Video is live on both platforms; `posts` rows exist | Find the video on both apps from a second account or browser |
| 1.13 | Similarity check with local embeddings | A near-duplicate script is blocked (over 0.80) in a test | Ask for "alternator" again and read its similarity in `factory status <id> --show script` (a reworded repeat scored 0.77, below the 0.80 block; D-076) |
| 1.14 | `factory discover`: Wikimedia pageviews for the seed topics plus YouTube `search.list` signals (API key) | `trend_signals` filled; `api_usage` updated; quota stays within budget | `factory status --signals` shows the top 10 topics with trend ratios |
| 1.14a | `factory trends import`: load Google Trends CSV exports from `data/inbox/trends/` into `trend_signals` (`INTEGRATIONS.md` §3a) | Rows created with `source = google_trends_csv`; files moved to `imported/`; re-importing adds no duplicates; a broken file is reported by name | Export one CSV from trends.google.com, drop it in the inbox, run the import twice, and check `factory status --signals` |
| 1.15 | `factory ideate`: scored ideas from signals plus catalog history; weekly plan with variety rules | Ideas with a score of at least 65 are proposed for next week's 5 slots, without repeated hook or beat in a row | `factory plan --week next` prints the plan; check the variety |

## Phase 2: Semi-automated (weeks 7–12, about 25 h)

**Milestone AC:** the nightly run produces scripts and renders automatically; both gates happen in Telegram; approved videos are scheduled on YouTube and arrive as TikTok drafts; analytics are collected; the first weekly Episode is published.

Before starting Phase 2, re-check OQ-2 (YouTube incentive programs) and §3 of `SECURITY_COMPLIANCE.md`.

| ID | Task | Acceptance criteria | How I test it myself |
|---|---|---|---|
| 2.1 | Telegram bot (`factory bot`): Gate 1 and Gate 2 messages with buttons, preview video, reason prompts, alerts; only your chat ID is allowed | Buttons change statuses; messages from other chats are ignored | Approve one dry-run item from your phone; message the bot from another account and get no effect |
| 2.2 | YouTube OAuth flow (`factory auth youtube`) plus one **unlisted** test upload | The token is saved in `secrets/coil-and-cog/`; the video appears unlisted | Open YouTube Studio and see the unlisted video |
| 2.3 | Scheduled publishing: `privacyStatus=private` + `publishAt` + `containsSyntheticMedia=true` | `posts.status = scheduled`; it goes public at 17:00 by itself | Schedule a test 10 minutes ahead (unlisted channel test first), watch it flip |
| 2.4 | TikTok developer app: reuse the legal pages from task 0.4, app setup, OAuth (`factory auth tiktok`), submit for review (closes OQ-3) | The token is saved; the review is submitted or approved | Developer portal shows the app status |
| 2.5 | YouTube Analytics pull at the 24 h / 72 h / 7 d / 28 d checkpoints (closes OQ-4) | `metrics` rows created; a manual entry command exists for stayed-to-watch | `factory metrics show <content_id>` |
| 2.6 | TikTok inbox upload plus a Telegram "I posted it" confirmation | Draft appears in the TikTok inbox; confirming sets `published` | Post a draft as private first, then the real one |
| 2.7 | TikTok Display API counts plus `factory metrics import-tiktok <csv>` | Counts are stored; the CSV import is idempotent | Import the same CSV twice; row counts don't change |
| 2.8 | Episode format: 16:9 side-panel layout, opener shot, thumbnail via `thumbnails.set` | One 5–10 min Episode renders and passes QA | Watch it on a laptop or TV |
| 2.9 | Labels (winner/average/loser) plus the Monday Telegram report | Labels follow `CONTENT_STRATEGY.md` §10; you can override them | Read Monday's report |
| 2.10 | Nightly Google Drive backup | The newest 14 backups are kept; no secrets in the zip | Download a backup and open the database with `sqlite3` |
| 2.11 | `factory run-nightly` plus Windows Task Scheduler jobs (01:00, 02:30, 06:00) with how-to steps in `ops/windows/` | Jobs run with the laptop on and WSL closed | Check the next morning's Telegram messages and `runs` table |
| 2.12 | **[Owner]** Turn on Docker Desktop's WSL integration for Ubuntu; optional n8n container that only calls the CLI | `docker run hello-world` works in WSL | Run that command |

## Phase 3: Fully automated (months 4–6, about 15 h)

**Milestone AC:** the system runs for 14 days with only gate decisions from you (5 h/week or less); the learn loop changes ideation; no failed runs go unhandled.

| ID | Task | Acceptance criteria | How I test it myself |
|---|---|---|---|
| 3.1 | Feed winning patterns into the ideation prompt | Ideation output cites which patterns it used; there's no copy of specific videos | Compare next week's plan before and after |
| 3.2 | Unattended discover and ideate every night; budget alerts | 14 nights without manual starts | Check the `runs` table |
| 3.3 | Gate 1 `batch_weekly` mode. Allowed only after at least 40 published videos **and** at least 85% Gate 1 approvals over the last 20 | You review a whole week of scripts in one Telegram session; the Engineer's Note is still yours | Do one Sunday batch review |
| 3.4 | Asset retention cleanup (raw clips 180 days after `analyzed`) | Old clips are removed; renders and the database are kept | Dry-run lists the files it would delete |
| 3.5 | Research spike: Wan 2.2 (Apache 2.0) on a free Kaggle GPU | A written verdict in `DECISIONS.md`: quality, time per clip, reliability | Watch 3 test clips |
| 3.6 | Optional n8n dashboard (reads status only, calls the CLI) | Workflows exported to `n8n/` | Open n8n and see today's pipeline |

## Phase 4: Multi-channel (month 6+)

**Milestone AC:** a second channel runs from config only, with no code changes (`git diff src/` is empty apart from bug fixes).

| ID | Task | Acceptance criteria | How I test it myself |
|---|---|---|---|
| 4.1 | Telecom channel profile `config/channels/<slug>.yaml` plus prompt overrides plus its own accounts and `secrets/<slug>/` | `factory --channel <slug> run-nightly` works in DRY_RUN | Look at the Telecom ideas in Telegram |
| 4.2 | Per-channel budgets and schedules don't affect each other | Using up channel A's budget doesn't stop channel B | A test with two channels |
| 4.3 | Instagram Reels publishing (after OQ-8 is verified) | One Reel is published via the official API | Check Instagram |
| 4.4 | Postgres migration (only if you run 3+ channels or move to a server) | All tests pass against Postgres | `uv run pytest` with `DATABASE_URL` set |

---

## 5. When to loosen the human gates

| Change | Condition |
|---|---|
| Gate 1 → `batch_weekly` | At least 40 videos published and at least 85% Gate 1 approvals over the last 20 (task 3.3) |
| Gate 2 | **Never removed.** It may become "approve from the preview in Telegram" (it already is), but stays required |

## 6. Paid upgrade triggers

Prices are approximate; check the current price when a trigger fires (OQ-9).

| Order | Upgrade | Approx. €/month | Trigger |
|---|---|---|---|
| 1 | Google AI Pro (more Flow credits) | ~22 | At least 50k views/month **or** TikTok Creator Rewards active |
| 2 | Claude API or Gemini paid tier for scripts | ~3–8 | Gemini free-tier limits block the nightly run more than 2×/month |
| 3 | Epidemic Sound Personal | ~10–17 | Any Audio Library claim problem, or wanting music baked into TikTok renders |
| 4 | ElevenLabs Starter | ~6 | Only if switching narrator voice after YPP feedback |
| 5 | Mini-PC or Raspberry Pi (one-time ~€60–100) | — | The laptop can't stay on overnight reliably |
| 6 | Veo via Gemini API (pay per clip) | Varies | Flow manual work becomes the main time cost **and** income covers it 3× |

## 7. Pre-payout task (Owner)

Before the first platform payout: complete the German tax setup (Gewerbe registration and Kleinunternehmer status check with a Steuerberater or Finanzamt) and submit the YouTube and TikTok tax information forms. At the same time, check whether the channel and `coilandcog.github.io` need a German **Impressum** (legal notice with name and address) and a fuller privacy policy once the channel is commercial; ask the Steuerberater or use a reputable generator.
