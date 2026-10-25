# TESTING.md

*Last updated: 2026-10-23*

## 1. Testing layers

| Layer | What | Tool | When it runs |
|---|---|---|---|
| **Unit** | Pure logic: scoring, status transitions, budget math, subtitle timing, prompt rendering, config validation | `pytest` with fakes from `tests/fakes/` | Every change: `uv run pytest` |
| **Media** (marked `slow`) | FFmpeg pipelines on tiny fixture clips (made with FFmpeg's built-in `testsrc` and `sine` generators, so no Flow credits are used) | `pytest -m slow` | Before merging media-related tasks |
| **Integration** (marked `integration`) | One real call per external API, using test data, private or unlisted only | `pytest -m integration` | Manually, once per integration task, and after credential changes |
| **Dry run** | The whole pipeline with `DRY_RUN=true`: everything except uploads and public posts | `uv run factory run-nightly` | Before turning on scheduling, and after big changes |
| **Private-first publishing** | The first 3 YouTube uploads via the API are **unlisted**, and the first TikTok drafts are checked in the app before posting | Manual | Phase 2 tasks 2.2–2.6 |
| **Human QA** | The quality checklist (§4) at Gate 2 | Owner | Every video |

`pyproject.toml` pytest settings: `markers = ["slow", "integration"]` and `addopts = "-m 'not integration'"`, so real APIs are never called by accident.

## 2. Fakes and fixtures

- `tests/fakes/fake_gemini.py` returns canned JSON from `tests/fixtures/llm/*.json`. It can simulate 429 errors and invalid JSON.
- `tests/fakes/fake_youtube.py`, `fake_tiktok.py` and `fake_telegram.py` record calls in memory, so tests can check them.
- `tests/fixtures/media/`: 3 small `shot_NN.mp4` clips (720×1280, 8 s), `voice.wav` (a sine tone plus silence), a sample script.
- Every test uses a **temporary SQLite database** (a `tmp_path` fixture), never `data/factory.db`.

## 3. What each stage must prove

| Stage | Unit tests (minimum) | Integration / manual check |
|---|---|---|
| config | Valid YAML loads; missing key → `ConfigError` with a clear message | `uv run factory config check` |
| db | Migrations apply; invalid status transition raises an error; `failed` → retry restores the previous status | `uv run factory db init` then `factory status` |
| discover | Trend ratio math; missing article skipped; disambiguation skipped; YAML article override; tiny pages get no trend; re-run adds nothing; YouTube weekly + stops at the budget and on `quotaExceeded`; the API key never appears in logs or rows; Trends CSV parser handles the real export fixture; re-import adds no duplicates | One real Wikimedia call; one YouTube `search.list` (watch `api_usage`); one real Trends CSV import |
| ideate | Blocklist rejects; score threshold filters; weekly plan respects the variety rules | One real Gemini call → 5 ideas stored |
| script | Schema validation; at least 2 sources required; similarity block over 0.80; word count within format bounds | One real script for "alternator"; owner reads it |
| subtitles | Alignment (punctuation, split contractions, missing words); ≤4 words / ≤760 px per line, ≤2 lines; ASS style values and one cyan word per event; preview burns (slow) | Integration: real Kokoro, word starts within 0.15 s of speech onsets; owner watches `subs_preview.mp4` |
| assemble | Slots fill the narration in whole frames; clip slow-down ≤1.25× then hold; both renders 1080×1920/30 fps/AAC 48 kHz, voice + 1 s long, −14 ±1 LUFS; music band only in the YouTube render, clip sound in both; empty music library stops before work; least-used track picked (slow) | Owner watches both renders on the phone, with and without sound |
| stills & diagrams | Label card inside the safe box for 1–4 (long) labels; energy colours; Title Case; Ken Burns clip is 1080×1920/30 fps/right length (slow); sharpest frame skips blurred frames (slow); owner still wins over the clip frame | Owner opens `diagrams/diag_NN_preview.jpg` and plays `stills/still_NN_anim.mp4` |
| generate | Flow sheet lists every `flow_clip` shot; inbox ingest detects missing or extra shots; voice duration ≈ estimate ±20% | Paste one prompt into Flow; drop the clip; status → `generated` |
| assemble | ffprobe checks: resolution, fps, duration bounds, audio present, loudness −14 ±1 LUFS; TikTok render has no music track | Watch both renders on the phone |
| review | Approve / reject / redo transitions; reason is required for reject and redo | Telegram buttons on a dry-run item |
| publish | Manual (1.12): link parsing, refusals (not approved, AI label not confirmed, wrong or conflicting link), status moves, second platform, re-run with the same link. API (2.3+): builds the correct YouTube body (`containsSyntheticMedia=true`, `publishAt`, `selfDeclaredMadeForKids=false`); DRY_RUN returns a `dryrun-` ID | 1.12: find the live video from a second browser. 2.2+: unlisted real upload; TikTok draft appears in the inbox |
| learn | Label rules (winner/average/loser) from sample numbers; CSV import parser | One real Analytics pull for an unlisted video |

## 4. Quality checklist (every video must pass)

### Automated (assemble stage, `media/qa.py`)

`factory assemble` runs these after rendering (or on existing renders) and writes `qa_report.json`. All pass → `assembled`; any failure → the item stays `generated` and the failures become its `status_reason` (shown by `factory status <id>`). A check that can't run counts as a failure, never a pass (D-070).
- [x] 1080×1920 (or 1920×1080 for Episodes), 30 fps, H.264, yuv420p, AAC 48 kHz (both renders).
- [x] Duration within the format bounds from the channel YAML (Short 30–50 s, Deep Dive 65–150 s, Episode 300–600 s), sting included.
- [x] Integrated loudness −14 ±1 LUFS; true peak ≤ −1 dBTP (both renders).
- [x] `subs.ass` exists, its events run from the first to the last spoken word, at most 4 words per line and 2 lines.
- [x] No subtitle or label outside the safe zones (`DESIGN_SYSTEM.md` §6): subtitle lines ≤ 760 px with the real font and the baseline ≥ 480 px above the bottom; diagram cards inside x 90–920, y 220–1100.
- [x] At least 2 (Short) or 4 (Deep Dive) diagram files.
- [x] `metadata.json` exists and is complete: at least 2 checked sources are recorded (every format), the title is ≤ 70 characters, 3 YouTube / 3–5 TikTok hashtags, AI flags on. If it couldn't be created (e.g. Gemini unavailable), the failure says why.
- [x] YouTube render's music track is from `music_tracks` with `source = youtube_audio_library`; TikTok render has **no** music (silent under the logo sting, where nothing else plays).
- [x] `beat_order` and `hook_type` differ from the previous content. Similarity (stored by the script stage) at most 0.80; scripts written before task 1.13 show "not checked".
- [x] Preview file is under 45 MB.

### Human (Gate 2)

- [ ] The hook works with the sound **off** (the first frame is visually interesting).
- [ ] Facts match the sources; nothing on screen contradicts the narration.
- [ ] The Engineer's Note is present and sounds like me.
- [ ] No visual glitches: melting parts, wrong physics, accidental text or logos, hands or faces.
- [ ] The loop line flows back into the hook.
- [ ] Title and cover are honest.
- [ ] I will switch on the AI label when posting on TikTok (YouTube's is set automatically).

## 5. Running tests

```bash
uv run pytest                     # unit tests (fast, default)
uv run pytest -m slow             # media tests with ffmpeg
uv run pytest -m integration -k youtube   # one real integration, manually
uv run ruff check . && uv run ruff format --check .
DRY_RUN=true uv run factory run-nightly   # full dry run
```

## 6. Definition of "stage works"

A stage counts as working when:
1. Its unit tests pass.
2. Its integration check has been done once and noted in the task's PR or commit message.
3. Running it twice in a row changes nothing the second time (idempotency).
4. With `DRY_RUN=true`, nothing external is written.
