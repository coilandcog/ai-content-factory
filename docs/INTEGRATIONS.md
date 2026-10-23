# INTEGRATIONS.md

*Last updated: 2026-10-23.*

Each section is a **contract**: what we call, how we authenticate, the limits, and how failures are handled. The "Verified" column says whether a fact was checked against official docs on 2026-10-22, or still has to be confirmed in the named ROADMAP task. Re-check limits whenever an API starts failing in a new way, because platforms change them without notice.

## 0. Common rules for every integration

- Each integration is one module in `src/factory/integrations/`, with a small class whose methods return Pydantic models, not raw JSON.
- Every outbound call goes through `budgets.check_and_record(service, units)` **before** it's made.
- Timeouts are 30 s by default and 300 s for uploads.
- **Retry policy:** only `RetryableError` is retried (connection errors, timeouts, HTTP 429, HTTP 5xx). That's 1 try plus 3 retries (4 attempts in total), with backoff waits of 2 s, 4 s and 8 s (plus a little jitter, meaning a small random extra delay). An SDK's built-in retry stays off, so only ours runs and every attempt is budget-checked. Other 4xx errors are raised as `IntegrationError` immediately.
- **`DRY_RUN=true`:** write operations (uploads, posts) are skipped and return a fake ID prefixed `dryrun-`. Reads still happen.
- Secrets come only from `.env` and `secrets/<slug>/`.
- Unit tests use the fakes in `tests/fakes/`. Real calls only happen in tests marked `integration`.

---

## 1. Gemini API (LLM): ideate, script, metadata

| Item | Contract | Verified |
|---|---|---|
| Auth | API key from Google AI Studio (created with the channel Google account) → `GEMINI_API_KEY` | ✔ |
| SDK | `google-genai` Python SDK, `client.models.generate_content(...)` | ✔ |
| Model | `llm.model: gemini-3.5-flash-lite` in the channel YAML (D-051). `gemini-2.5-flash` and `gemini-2.5-flash-lite` return 404 "no longer available to new users" | ✔ 2026-10-23 (test call HTTP 200) |
| Free-tier limits | Per **project and per model** (not per key); RPD resets at midnight Pacific (09:00 Berlin). Read in AI Studio → Rate Limit on 2026-10-23 (see table below). Google changes them often: re-check when 429s appear | ✔ 2026-10-23 |
| Our volume | About 10 calls per content, 50–60 per day maximum → `budgets_per_day.gemini_calls: 60` | — |
| Client | `factory.integrations.gemini.GeminiClient(api_key, model, before_call=...)`; `generate_json(system=, prompt=, schema=)` returns the validated Pydantic object. `before_call` runs before **every** attempt (stages pass `budgets.check_and_record(..., "gemini")`, so each retry counts). Timeout 30 s. The key comes from `GEMINI_API_KEY` via `load_env()` (a `SecretStr`). Prompts are logged only at DEBUG | ✔ 2026-10-23 (task 1.2) |
| Request | System prompt (from a Jinja2 template) plus a user prompt; `response_mime_type="application/json"` with a JSON schema for structured output | ✔ |
| Response | JSON validated against a Pydantic model (`IdeaBatch`, `ScriptDraft`, `PostMetadata`). On a validation error, one automatic re-ask with the error message, then fail | — |
| Errors | 429, 5xx or network error → retry (§0); other 4xx → `IntegrationError`; empty or blocked answer → `IntegrationError` with the block or finish reason; quota exhausted for the day → `BudgetExceededError` → switch to the Ollama fallback for **metadata only** (scripts wait for the next day) | — |
| Privacy | Free-tier inputs may be used by Google to improve products. Send only public topic material, never secrets or personal data | ✔ (pricing page: "Used to improve our products: Yes") |

**Free-tier limits for project `coil-and-cog` (AI Studio, 2026-10-23):**

| Model | RPM | TPM (input) | RPD | Note |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 15 | ~250K | 500 | **Chosen.** 500 RPD covers our 60/day budget about 8× |
| `gemini-3.8-flash` | 5 | ~250K | 20 | Too few requests per day; returned 503 "high demand" twice during testing |
| `gemma-4-31b-it` | 30 | ~15K | ~14,400 | Open model, free only; returned 500 twice during testing; low TPM |
| `gemini-2.5-flash` / `-lite` | 5 / 10 | ~250K | 20 / 20 | Not usable: closed to new users |

RPM = requests per minute, TPM = input tokens per minute, RPD = requests per day. Values were read off the AI Studio charts, so TPM is approximate.

## 2. Ollama (local LLM fallback)

| Item | Contract |
|---|---|
| Endpoint | `POST {OLLAMA_URL}/api/chat` (default `http://localhost:11434`), `stream=false`, `format="json"` |
| Model | `llm.fallback_model`, default `llama3.2:3b`. It's small and slow on a CPU (several seconds per answer) |
| Use | Titles, hashtags and captions when Gemini is unavailable. **Not** used for full scripts (quality too low) |
| Errors | Connection refused → `IntegrationError("Ollama not running")` → the stage skips the item and sends an alert |

## 3. Wikimedia Pageviews API (discover)

| Item | Contract | Verified |
|---|---|---|
| Endpoint | `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{Article}/daily/{YYYYMMDD}/{YYYYMMDD}` | ✔ (public REST API) |
| Auth | None. **Required:** a descriptive `User-Agent` header from `WIKIMEDIA_USER_AGENT`, including a contact email | ✔ |
| Use | Demand per seed topic: 7-day views and the trend ratio (last 7 days ÷ previous 28-day average) | — |
| Limits | Be polite: at most 10 requests/second; we make about 50 per day | — |
| Errors | 404 (no such article) → skip the topic and log a warning; 429/5xx → retry | — |

## 3a. Google Trends: manual CSV export (discover)

There is no API call here. **The owner** downloads the data by hand in a browser, which is normal permitted use of the website. Automated access to Trends (e.g. `pytrends`) is not allowed; see D-048 and D-049.

| Item | Contract | Verified |
|---|---|---|
| Owner routine | Once a week (e.g. Sunday): open `trends.google.com/explore`, compare up to 5 seed topics, set region Worldwide (and optionally US/GB/DE) and the last 90 days, then click the download (⬇) icon on "Interest over time". Save the CSV into `data/inbox/trends/` | — |
| Command | `factory trends import` parses every CSV in the inbox, writes `trend_signals` rows (`source = google_trends_csv`), then moves each file to `data/inbox/trends/imported/` | — |
| Data | Interest is **relative** (0–100, where 100 = the peak in that chart), not absolute volume. We store `trends_interest` (the last week) and `trends_ratio` (last 4 weeks ÷ previous 8 weeks) per topic and region | ✔ (how Google defines the scale) |
| Format | Google has changed the CSV layout before (extra header lines, column names). The parser finds the date column and topic columns by name, and is tested against a real export saved in `tests/fixtures/trends/` | Confirm with the first real export in task 1.14a |
| Idempotency | Importing the same file twice adds no duplicate rows (match on topic, region, date) | — |
| Errors | An unreadable file → `IntegrationError` naming the file; it stays in the inbox and the other files are still imported. An empty inbox is not an error: the importer just reports "0 files" | — |
| Future | Replaced by Google's official Trends API once access is granted (OQ-6) | — |

## 4. YouTube Data API v3 (discover, publish)

| Item | Contract | Verified |
|---|---|---|
| Auth (discover) | API key `YOUTUBE_API_KEY` for public read-only calls (`search.list`, `videos.list`). This is available from Phase 1 without OAuth | ✔ |
| Auth (publish) | OAuth 2.0 "Desktop app" client from the channel's Google Cloud project. The client file goes in `secrets/<slug>/google_client_secret.json`; the token is stored in `secrets/<slug>/google_token.json`. Scopes: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`, `drive.file` | ✔ |
| Consent screen | User type "External". Branding needs app name, support email, **homepage URL and privacy-policy URL** (`https://coilandcog.github.io/`, `/privacy.html`; authorized domain `coilandcog.github.io`) before publishing. **Publishing status "In production"** (an unverified app is fine for the owner's own account: click through the "unverified app" warning). While in "Testing", refresh tokens expire after 7 days | Confirm in task 2.2 |
| Quota | Default project quota is 10,000 units/day. `videos.insert` costs **1 unit in the separate "Video Uploads" bucket**. `search.list` costs 100 units; `videos.list` costs 1 unit | Upload cost ✔; others: confirm in Cloud Console in task 2.2 |
| Unverified projects | The official page currently says uploads from unverified API projects are **not** forced to private | ✔ |
| Discover calls | `search.list(q=<topic>, type=video, order=viewCount, publishedAfter=<now−30d>, relevanceLanguage=en, maxResults=25)`; `videos.list(chart=mostPopular, videoCategoryId=28, regionCode=US)`. Only titles and counts are stored, as **inspiration signals** | — |
| Upload | `videos.insert(part="snippet,status")`, resumable upload. Body: `snippet.title`, `snippet.description`, `snippet.tags`, `snippet.categoryId="28"`, `snippet.defaultLanguage="en"`; `status.privacyStatus="private"`, `status.publishAt=<UTC ISO>`, `status.selfDeclaredMadeForKids=false`, `status.containsSyntheticMedia=true` | `containsSyntheticMedia` ✔ |
| Test uploads | Always `privacyStatus="unlisted"` or `"private"` without `publishAt` | — |
| Thumbnails | `thumbnails.set` for Episodes only. Needs a phone-verified channel | Confirm in task 2.8 |
| Response stored | `posts.external_id = video.id`, `posts.url = https://youtu.be/<id>`, `posts.status = scheduled` | — |
| Errors | `quotaExceeded` (403) → `BudgetExceededError`; `uploadLimitExceeded` → stop for the day and alert; 5xx → resumable retry | — |

## 5. YouTube Analytics API (learn)

| Item | Contract | Verified |
|---|---|---|
| Auth | Same OAuth token, scope `yt-analytics.readonly` | ✔ |
| Call | `reports.query(ids="channel==MINE", startDate, endDate, metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained", dimensions="video", filters="video==<id>")` | Metric names: confirm in task 2.5 |
| Stayed to watch | May not be available through the API. If not, it's read in YouTube Studio and entered with `factory metrics add --manual` | OQ-4 |
| Schedule | Checkpoints at 24 h, 72 h, 7 days and 28 days after `published_at` | — |
| Errors | Data delay (up to 48 h) → store what's available; re-pull at the next checkpoint | — |

## 6. TikTok APIs (publish, learn)

| Item | Contract | Verified |
|---|---|---|
| App | A TikTok for Developers app with Login Kit, Content Posting API (**upload to inbox**) and Display API. Needs Terms of Service and Privacy Policy URLs (plan: a static GitHub Pages site). App review is needed before production use | Requirements and redirect URI: OQ-3, task 2.4 |
| Auth | OAuth 2.0 authorization code flow. Scopes: `user.info.basic`, `video.upload`, `video.list`. Tokens in `secrets/<slug>/tiktok_token.json`; `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in `.env` | Token lifetimes: confirm in task 2.4 |
| Inbox upload | `POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/` with `{"source_info": {"source": "FILE_UPLOAD", "video_size", "chunk_size", "total_chunk_count"}}` → response `data.publish_id`, `data.upload_url`. Then `PUT upload_url` with `Content-Range` and `Content-Type: video/mp4` | ✔ |
| Status | `POST /v2/post/publish/status/fetch/` with `publish_id` until processing completes | — |
| Owner step | Open TikTok → inbox notification → edit → add a TikTok sound → switch on the **AI-generated content** label → caption from `metadata.json` → Post. Then `factory publish --confirm-tiktok <content_id>` (Telegram button in Phase 2) | — |
| Why not Direct Post | Unaudited apps can only post `SELF_ONLY` (private), and Direct Post requires an audit | ✔ |
| Display API | `POST /v2/video/list/` (scope `video.list`) with fields `id,create_time,duration,view_count,like_count,comment_count,share_count` | Fields ✔ |
| Retention | Not available in the API. A weekly CSV export from TikTok Studio, imported with `factory metrics import-tiktok <file.csv>` | ✔ |
| Fallback | If the app review fails or stalls: download the TikTok render from Telegram and upload it manually in the app (about 1 minute) | — |
| Errors | `spam_risk_*` errors → stop all TikTok actions for 24 h and alert the owner; 429 → retry | — |

## 7. Telegram Bot API (review gates, alerts, reports)

| Item | Contract | Verified |
|---|---|---|
| Auth | Bot token from @BotFather → `TELEGRAM_BOT_TOKEN`. Only `TELEGRAM_ALLOWED_CHAT_ID` is accepted; everything else is ignored | ✔ |
| Mode | Long polling (`getUpdates`), so no public server or webhook is needed. Run with `factory bot`, a long-running process | ✔ |
| Messages | `sendMessage` with `inline_keyboard` buttons: `approve:<gate>:<content_id>`, `reject:<gate>:<content_id>`, `redo:<gate>:<content_id>`. Reject and redo prompt for a text reason | — |
| Video preview | `sendVideo` with `preview_720p.mp4`. Bot uploads are limited to **50 MB**, so the preview is encoded to stay under 45 MB | Confirm in task 2.1 |
| Alerts | `failed` items, budget stops, `spam_risk`, quota errors | — |
| Reports | Monday 06:00: weekly winners and losers plus pattern summary | — |
| Errors | 429 → respect the `retry_after` value | — |

## 8. Google Flow (generate; manual, no API)

| Item | Contract | Verified |
|---|---|---|
| Access | flow.google, signed in with the channel Google account; free plan | ✔ |
| Credits | Free plan: **50 credits/day**. Veo 3.1 Lite = 10, Fast = 20, Quality = 100 | ✔ |
| Policy | Final clips use **Fast** (Lite is "Preview" in Google Cloud terms; Lite is for testing only). About 2 final clips per day | — |
| Input | `flow_sheet.md`: per shot, its number, beat, duration and the full prompt (rendered from the `DESIGN_SYSTEM.md` §10 templates) | — |
| Output | Owner downloads MP4s → `data/inbox/flow/<content_id>/shot_NN.mp4` | — |
| Logging | `factory flow-log --credits N` updates `api_usage(service="flow")` | — |
| Watermark | Free output has no visible watermark (owner checked). Invisible SynthID marking may still be present | ✔ (owner) |
| Terms | Flow (labs.google) is governed by the Google Terms of Service, the Generative AI Additional Terms and the Prohibited Use Policy. Google "won't claim ownership" of generated content; commercial use isn't restricted; users must be 18+. We must not claim the content is solely human-made (D-039 covers this), must not bypass safety features or watermarks, and must not use outputs to train ML models. See D-052 | ✔ 2026-10-23 |

## 9. Local engines

| Engine | Contract |
|---|---|
| **Kokoro-82M** (`kokoro` + `misaki[en]`; system package `espeak-ng`) | `KPipeline(lang_code="a")`, `voice=<voice.voice_id>`, `speed=<voice.speed>` → 24 kHz audio → resampled to 48 kHz WAV `voice.wav`. License Apache 2.0 ✔ |
| **faster-whisper** | `WhisperModel("small.en", device="cpu", compute_type="int8")`, `transcribe(voice.wav, word_timestamps=True)`. Words are aligned to the approved script text with `difflib`, so spelling always comes from the script |
| **sentence-transformers** | `all-MiniLM-L6-v2`, cosine similarity (a 0–1 measure of how alike two texts are in meaning) of a new script vs the last `similarity_window` scripts |
| **FFmpeg / ffprobe** | System binary (installed). Called through `media/ffmpeg.py` with argument lists, never via the shell. Loudness filter `loudnorm=I=-14:TP=-1:LRA=11` |

## 10. Google Drive backup

| Item | Contract |
|---|---|
| Auth | Same Google OAuth token, scope `drive.file` (only files the app created) |
| Call | `files.create` (multipart) into folder `coil-and-cog-backups/`. Keeps the newest 14 and deletes older ones |
| Payload | A `factory.db` snapshot made with SQLite's backup API (a consistent copy even while in use), plus a zip of `config/` and `prompts/`. **Never** `secrets/` or `.env` |

## 11. Future integrations (not built yet)

| Integration | Phase | Note |
|---|---|---|
| Google Trends official API | 3 | Access status unknown (OQ-6). Would replace the manual CSV import (§3a). Unofficial scrapers like `pytrends` are **not allowed**: they call `/trends/api`, which Google's robots.txt disallows (checked 2026-10-23) |
| Instagram Graph API (Reels) / Facebook Pages | 4 | Requirements to be verified (OQ-8) |
| Veo via Gemini API (paid) | Upgrade | Replaces manual Flow once income justifies it |
| ElevenLabs, Epidemic Sound | Upgrade | See `ROADMAP.md` §6 |
