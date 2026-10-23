# SECURITY_COMPLIANCE.md

*Last updated: 2026-10-23. Policies were verified against official sources on this date (links in §3–§5). Platforms change rules; re-check before Phase 2 and every 3 months.*

## 1. Secrets handling

**Rule:** secrets live only in `.env` (settings) and `secrets/<channel_slug>/` (OAuth files and tokens). Both are in `.gitignore`. Never in code, docs, logs, Telegram messages, screenshots or chat with an AI assistant.

`.env.example` is committed. It lists every key with no values:

```dotenv
# --- safety ---
DRY_RUN=true                     # true = no uploads/public posts
FACTORY_CHANNEL=coil-and-cog     # default channel slug

# --- LLM ---
GEMINI_API_KEY=
OLLAMA_URL=http://localhost:11434

# --- Telegram ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=

# --- Google ---
YOUTUBE_API_KEY=                 # read-only public data for discover (no OAuth needed)
# paths only; files live in secrets/<slug>/
GOOGLE_OAUTH_CLIENT_FILE=secrets/coil-and-cog/google_client_secret.json
GOOGLE_TOKEN_FILE=secrets/coil-and-cog/google_token.json

# --- TikTok ---
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_TOKEN_FILE=secrets/coil-and-cog/tiktok_token.json

# --- Wikimedia ---
WIKIMEDIA_USER_AGENT=CoilAndCogFactory/0.1 (contact: CHANNEL_EMAIL_HERE)
```

Rules:
- File permissions: `chmod 600 .env secrets/*/*`.
- The logging setup has a **redaction filter** that masks any value of a key containing `KEY`, `TOKEN`, `SECRET` or `PASSWORD`.
- Before every commit, run `git status` and check that no `.env`, `secrets/` or `data/` paths are staged. A pre-commit check runs `git diff --cached --name-only | grep -E '(^\.env$|^secrets/|^data/)'` and must find nothing.
- **If a secret leaks** (committed, pasted or logged): revoke or rotate it at the provider immediately, then remove it from git history. Rotating first matters most.
- The password manager holds: Google account password and backup codes, TikTok login, the Telegram bot token, and copies of API keys.

## 2. Account safety rules (non-negotiable)

1. 2-step verification on the Google, TikTok and Telegram accounts.
2. A separate Google account just for the channel; it owns YouTube, the Cloud project, the Gemini key, Drive and the TikTok sign-up.
3. One channel per Google account.
4. The TikTok account stays **Personal**, not Business, because Creator Rewards requires Personal.
5. No buying views, followers or likes. No engagement bots, follow-for-follow, engagement pods or automated comments or DMs.
6. At most 1 upload per platform per day at launch.
7. Only official APIs or permitted sources. No scraping that violates terms of service, and no unofficial "trends" scrapers. Google Trends data enters only through CSV files the owner downloads by hand.
8. Copyright claims and strikes are handled **manually** by the owner, never by automatic dispute.
9. If any platform warning arrives (strike, `spam_risk`, monetization review), pause publishing for that platform (`platforms.<p>.enabled: false`) until it's resolved.

## 3. Platform policy checklist (every video)

**YouTube monetization policies:** [support.google.com/youtube/answer/1311392](https://support.google.com/youtube/answer/1311392). The "inauthentic content" rule (formerly "repetitious content") rejects content that *"looks like it's made with a template"* and *"AI-generated content made with generic or unoriginal templates giving the impression of mass production without adding the creator's original, authentic insights."*

Our safeguards, all of which apply to every video:
- [ ] **Engineer's Note** is present and was approved or edited by the owner.
- [ ] `beat_order` and `hook_type` differ from the previous video.
- [ ] Similarity to the last 30 scripts is below 0.80.
- [ ] At least 2 of our own diagram moments (Short) or at least 4 (Deep Dive).
- [ ] At least 2 sources stored; Deep Dive descriptions list them.
- [ ] The topic isn't on the blocklist (`CONTENT_STRATEGY.md` §3).
- [ ] No real people, faces, brand logos or identifiable real locations.
- [ ] No content copied from other creators. Trend signals are used for **topic inspiration only**; we never reuse their footage, scripts, voice or music.
- [ ] The title and cover match what the video delivers (no misleading metadata).

## 4. AI disclosure rules

| Platform | Rule (official) | Our implementation |
|---|---|---|
| YouTube | Disclosure is required for AI content that *"seems realistic"* ([Help](https://support.google.com/youtube/answer/14328491)) | **Always** `status.containsSyntheticMedia=true` (API field ✔), plus the description line |
| TikTok | *"We also require creators to label all AI-generated content that contains realistic images, audio, and video"* ([Support](https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content)) | **Always** switch on the AI-generated label when posting; Gate 2 checklist item |
| All | Transparency | Description line: "Visuals AI-generated; script researched and reviewed by a human engineer." |

We disclose on **every** video, even where it may not be strictly required, because it costs nothing and removes all ambiguity.

## 5. Copyright and licensing rules

| Asset | Allowed source | License status | Record in |
|---|---|---|---|
| Video clips | Google Flow (Veo 3.1 Fast) generated by the owner | Google doesn't claim ownership of generated content. Pure AI output may not be copyrightable, so others could reuse raw clips; our protectable work is the script, edit and diagrams. Flow terms re-read 2026-10-23 (D-052): commercial use allowed; never remove or obscure SynthID or any watermark | `assets.license = google_flow_output` |
| Stills | Flow images or FLUX.1-schnell (Apache 2.0) | Commercial use OK | `assets.license` |
| Voice | Kokoro-82M (Apache 2.0) | Commercial use OK ✔ | `assets.license = apache-2.0` |
| Diagrams, logo, sting | Our own code (Pillow/FFmpeg) | Own work | `own_work` |
| Fonts | Montserrat, Inter (SIL Open Font License) | Commercial use OK | — |
| Music, YouTube render | **YouTube Audio Library only** ([Help](https://support.google.com/youtube/answer/3376882)). Copyright-safe on YouTube; attribution added when the track requires it | ✔ (YouTube only) | `music_tracks`, `assets.music_track_id` |
| Music, TikTok render | **None in the file**; the owner adds a TikTok library sound in the app | TikTok's own license | — |

**Banned sources:**
- AI music from Suno's free tier (non-commercial).
- MusicGen model weights (non-commercial license).
- Pixabay or other "royalty-free" music, because of retroactive Content ID claims.
- Kling or Hailuo free tiers (no commercial rights on free plans).
- Any clip, sound or voice from other creators.
- Cloned voices.

## 6. Data protection

- We store no personal data about viewers. Comments aren't stored in the MVP.
- Trend signals store public titles and counts only.
- Google Drive backups contain the database and configs only, never secrets.

## 7. Incident playbook

| Event | Action |
|---|---|
| Content ID claim | Owner reviews it in YouTube Studio. If it's our licensed Audio Library track, dispute it with the track link; otherwise replace or mute the audio. Note it in `contents.status_reason` |
| Copyright strike / community-guidelines strike | Pause the platform (`enabled: false`), read the notice, fix the root cause, update this doc and `DECISIONS.md` |
| YPP rejected for inauthentic content | Switch narration fallback to option C (owner records the Engineer's Note in their own voice), strengthen diagrams, wait the required period, re-apply. Log the decision |
| TikTok `spam_risk` / posting ban | Stop all TikTok actions for at least 24 h; switch to manual posting only |
| Leaked secret | Rotate it immediately (§1) |
