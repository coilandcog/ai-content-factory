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
| Client | `factory.integrations.gemini.GeminiClient(api_key, model, before_call=...)`; `generate_json(system=, prompt=, schema=, max_reasks=1)` returns the validated Pydantic object, re-asking up to `max_reasks` times with the validation errors (the script stage uses 2). `before_call` runs before **every** attempt (stages pass `budgets.check_and_record(..., "gemini")`, so each retry counts). Timeout 30 s. The key comes from `GEMINI_API_KEY` via `load_env()` (a `SecretStr`). Prompts are logged only at DEBUG | ✔ 2026-10-23 (task 1.2) |
| Request | System prompt (from a Jinja2 template) plus a user prompt; `response_mime_type="application/json"` with a JSON schema for structured output | ✔ |
| Metadata | `stages/metadata.PostMetadata`: `youtube_title` (≤70), `youtube_summary`, `youtube_hashtags` (3), `tiktok_caption`, `tiktok_hashtags` (3–5); 1 call per item, made by `factory assemble` (or when Gate 2 opens an item without it). Attribution and platform flags are added by code (D-071; no disclosure line since D-075, no source links since D-077) | ✔ 2026-10-23 |
| Response | JSON validated against a Pydantic model (`IdeaBatch`, `ScriptDraft`, `PostMetadata`). On a validation error, an automatic re-ask with the error message (1 by default, 2 for scripts), then fail | ✔ |
| Schema rules | Gemini's schema format rejects `exclusiveMinimum`/`exclusiveMaximum` (use `ge`/`le`, not `gt`/`lt`), and fields with defaults become optional, so Gemini leaves them out: make every field required. A unit test converts each schema with the SDK offline | ✔ 2026-10-23 |
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

## 3. Wikimedia APIs (discover; script reference)

**Script reference (task 1.3, D-058):** before writing a script, `integrations/wikimedia.page_summary()` makes two calls, `GET https://en.wikipedia.org/w/api.php?action=opensearch&search=<object>&limit=1` (best title) and `GET https://en.wikipedia.org/api/rest_v1/page/summary/<Title>` (lead text). The text goes into the prompt and the article must be cited. Best effort: any failure, no article or a disambiguation page means no reference, and the script is written without one. Each call is counted in `api_usage` as `wikimedia`. The same `User-Agent` is used when checking that cited source URLs open (one GET each, following redirects). 2xx counts as existing, and so do 401/403/429, because Wikipedia and Britannica sometimes refuse scripts while the page exists; 404/410, 5xx and dead domains count as missing (invented URLs answer 404). `factory script` refuses to start if `WIKIMEDIA_USER_AGENT` has no contact details in parentheses, because Wikimedia blocked a bare `CoilAndCogFactory/0.1` with 403 "Please respect our robot policy" (D-064).


| Item | Contract | Verified |
|---|---|---|
| Endpoint | `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{Article}/daily/{YYYYMMDD}/{YYYYMMDD}` | ✔ (public REST API) |
| Auth | None. **Required:** a descriptive `User-Agent` header from `WIKIMEDIA_USER_AGENT`, including contact details: an email or a website URL, e.g. `CoilAndCogFactory/0.1 (https://coilandcog.github.io)` | ✔ |
| Article choice (discover, D-078) | `GET https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch=<topic>&prop=pageprops&redirects=1`: the best full-text hit that is not a disambiguation page (the prefix search used for script references found nothing for e.g. "ship diesel engine"). `niche.wikipedia_articles` in the channel YAML names the article where search picks the wrong one (e.g. differential → "Differential (mechanical device)"); redirects are followed, because pageviews only count the real article | ✔ 2026-10-25: all 27 seed topics matched |
| Use | Demand per seed topic, refreshed when older than 20 h: `pageviews_7d` (yesterday and the 6 days before; today is incomplete) and `pageviews_trend_ratio` = last 7 days ÷ (previous 28 days ÷ 4). The ratio is only stored when the 28 days had at least 280 views (~10/day), because tiny pages swing wildly | ✔ |
| Limits | Be polite: at most 10 requests/second; discover makes 2 calls per topic (about 54 per day), one after another | ✔ |
| Errors | No article or 404 → skip the topic with a warning; 429/5xx/network → 1 try + 3 retries (`integrations/http_json.py`), then the topic counts as failed and the others go on | ✔ |

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
| Discover calls (task 1.14, D-078) | `integrations/youtube.recent_popular_videos()`: `search.list(part=snippet, q=<topic>, type=video, order=viewCount, publishedAfter=<now−30d>, relevanceLanguage=en, maxResults=25)` (100 units), then `videos.list(part=statistics, id=<the 25 ids>)` (1 unit) for the view counts. Plain httpx with the API key as a query parameter; error messages and logs never contain the URL. Stored per topic: `video_views_median_30d` plus the top 5 titles in `raw_json`, as **inspiration signals** only. A topic is searched again when its data is older than ~7 days, oldest first, until `youtube_units` would be passed; Google's `quotaExceeded` also stops the YouTube part for the day. The `mostPopular` chart is not used yet | ✔ 2026-10-25: 27 searches, 2,727 units |
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
| Credits | Free plan: **50 credits/day**. **Veo 3.1 Fast = 20, Veo 3.1 Lite = 10** (owner read Flow's model menu, 2026-10-23; Ultra subscribers pay 10 for Fast per the changelog). So 2 final clips per day; `stages/generate.FLOW_CREDITS_PER_CLIP = 20` | ✔ (owner) |
| Policy | Final clips use **Fast** (Lite is "Preview" in Google Cloud terms; Lite is for testing only). About 2 final clips per day | — |
| Input | `flow_sheet.md`: per shot, its number, beat, duration and the full prompt (rendered from the `DESIGN_SYSTEM.md` §10 templates) | — |
| Owner settings | Flow "Agent settings": video **9:16** and **x1** (x2 makes two clips per prompt and doubles the credits); keep "Confirm before generating: Always". Model menu name: "Veo 3.1 - Fast" | ✔ (screenshots 2026-10-23) |
| Output | Owner downloads MP4s → `data/inbox/flow/<content_id>/shot_NN.mp4` (`factory generate` creates the folder and prints its Windows path). `generate` accepts a clip when ffprobe reads a video stream, the frame is portrait and it lasts ≥3 s; it copies the set to `clips/` and records model, exact prompt and license per clip. The clip's own audio is kept untouched (use decided in task 1.9) | ✔ (task 1.6) |
| Logging | `factory flow-log --credits N` updates `api_usage(service="flow")` | — |
| Watermark | Free output has no visible watermark (owner checked). Invisible SynthID marking may still be present | ✔ (owner) |
| Terms | Flow (labs.google) is governed by the Google Terms of Service, the Generative AI Additional Terms and the Prohibited Use Policy. Google "won't claim ownership" of generated content; commercial use isn't restricted; users must be 18+. We must not claim the content is solely human-made (D-039 covers this), must not bypass safety features or watermarks, and must not use outputs to train ML models. See D-052 | ✔ 2026-10-23 |

## 9. Local engines

| Engine | Contract |
|---|---|
| **Kokoro-82M** (`kokoro` 0.9.4 + `misaki[en]`, which bundles espeak-ng via `espeakng-loader`, so no system package is needed; spaCy model `en-core-web-sm` pinned in `pyproject.toml`, because misaki otherwise tries to install it with pip at first run) | `integrations/kokoro_tts.KokoroTTS().synthesize(text, voice_id, speed)` → `KPipeline(lang_code=voice_id[0], repo_id="hexgrad/Kokoro-82M")` → mono float32 at 24 kHz → `media/audio.normalize_to_wav` → 48 kHz 16-bit WAV. The model (~330 MB) downloads once into `~/.cache/huggingface`. PyTorch comes from the **CPU-only index** (`[tool.uv.sources]`) and uses half the logical cores (4 threads on the owner's PC: ~0.65× real time; 8 threads were 2× slower). PyTorch is imported only when speech is made. License Apache 2.0 ✔ |
| **Word timing** | `KokoroTTS.synthesize()` returns `Speech(pcm, tokens)`: every token's `start_ts`/`end_ts` from Kokoro, shifted by the audio of earlier chunks (`join_chunks`). `media/subtitles.align_words` matches them to the script words with `difflib` on lowercase letters/digits (punctuation and split contractions line up; unmatched words share the neighbouring time). Verified on real audio: word starts within 0.06 s of speech onsets (integration test `test_kokoro_live`). **Fallback, not installed:** faster-whisper (`small.en`, int8, CPU) for audio Kokoro didn't make (D-065) |
| **sentence-transformers** | `similarity.closest()`: `all-MiniLM-L6-v2` (Apache 2.0, ~90 MB, downloaded once into `~/.cache/huggingface`, CPU), cosine similarity (a 0–1 measure of how alike two texts are in meaning) of a new script vs the current scripts of the last `similarity_window` other items. The model reads at most 256 word pieces, so texts are embedded in 150-word chunks and averaged. Loaded only when a script is checked (~3 s, first time includes the download). Unit tests use a hashed bag-of-words fake (`tests/fakes/fake_embedder.py`) |
| **FFmpeg / ffprobe** | System binary, chosen in settings.yaml `tools` (Ubuntu's `/usr/bin/ffmpeg` 8.0.1: libass, libx264, aac; the Homebrew build earlier on PATH has no libass). Called through `media/ffmpeg.py` with argument lists, never via the shell; `check_tools()` lists missing filters/encoders. Voice loudness (`media/audio.py`): measure with `loudnorm` (EBU R128), then `volume` gain + `alimiter` at −1.5 dBFS on 192 kHz-upsampled audio, re-measure and correct the gain (≤3 passes, stop within 0.3 LU). Result: −14 ±0.5 LUFS, true peak ≈ −1.5 dBTP. Plain two-pass `loudnorm` (linear) stopped at −15.7 LUFS on speech, because peaks hit the −1 dBTP ceiling first |

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
