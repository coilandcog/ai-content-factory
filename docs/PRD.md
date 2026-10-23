# PRD — Product Requirements Document

*Last updated: 2026-10-23*

## 1. Overview

**Coil & Cog AI Content Factory** is a personal automation pipeline. It researches, writes, generates, assembles and publishes short "how machines work" explainer videos for a faceless channel, **Coil & Cog** (`@coilandcog`), on YouTube and TikTok. A human approves every video.

The visual benchmark is a cinematic 9:16 product explainer. A machine part is shown on a dark studio pedestal, then cut open, then shown with glowing energy flows, then shown installed where it is used. The first example was a car alternator made with Google Flow.

## 2. Problem

Making high-quality explainer videos by hand takes hours per video, and a solo side project can't sustain the posting frequency that platforms reward. Mass-produced "AI slop", on the other hand, gets demonetized under YouTube's inauthentic-content policy. The factory automates the repetitive work (research, drafting, voiceover, subtitles, editing, uploading, analytics) so the owner's limited time goes into what makes the content valuable: choosing topics, checking facts, adding a personal engineering insight, and approving quality.

## 3. User

There is one user, **the owner**:
- A beginner in AI agents and automation, comfortable with Python and Java.
- Has an engineering/telecom background.
- Lives in Germany; produces content in English.
- Has 10 h/week for the first 8 weeks, then 5 h/week.
- Has a Windows laptop with WSL2, 16 GB RAM and no NVIDIA GPU. The PC can run overnight.

## 4. Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Reach monetization as early as possible, with no fixed deadline | TikTok Creator Rewards eligible (10k followers, 100k views/30 days); YouTube YPP eligible (from 1 Feb 2027 for new applicants: 8,000 long-form watch hours in 365 days **or** 20M Shorts views in 90 days) |
| G2 | Income | Planning assumption: €0 in months 0–6, first income in months 6–12, €200–500/month by month 18 |
| G3 | Consistency | 5 videos/week (3 Shorts and 2 Deep Dives), rising to 7/week after 2 stable weeks |
| G4 | Owner time | 5 h/week or less after the build phase; about 5–10 minutes of review per video |
| G5 | Policy safety | Zero strikes, zero demonetization for inauthentic content, 100% AI-disclosure compliance |
| G6 | Scalability | A second niche or channel can be added with one YAML file and no code changes |

## 5. Success metrics

- **Per video, "winner" definition:** within 7 days, at least 3× the channel's median views **and** at least 70% stayed-to-watch **and** average view duration of at least 80% (Shorts) or at least 40% (videos longer than 60 s).
- **Channel:** weekly growth in followers and subscribers; share of winners per month (target: at least 15% from month 3); watch hours from Episodes (from Phase 2).
- **Pipeline:** percentage of scripts approved at Gate 1 (target: at least 60%); percentage of renders approved at Gate 2 (target: at least 80%); failed runs per week (target: 1 or fewer).

## 6. MVP scope (Phases 0–1)

- One channel: Coil & Cog, niche "How Machines Work", with pillars `machines` and `everyday_things`.
- Formats: **Short** (30–50 s, 9:16) and **Deep Dive** (65–150 s, 9:16).
- A Python CLI (`factory <stage>`) covering: discover (Wikipedia pageviews, YouTube search signals, and a weekly Google Trends CSV you export by hand), ideate (Gemini, scored), script (Gemini, with sources and an Engineer's Note), generate (Flow prompt sheet → manual Flow clips; Kokoro voiceover; animated stills; diagrams; subtitles), and assemble (FFmpeg, separate YouTube and TikTok renders).
- Gate 1 (script) and Gate 2 (final) approvals happen in the terminal in Phase 1 and move to Telegram in Phase 2.
- Manual upload in Phase 1. Posting starts in week 3–4.
- SQLite database, assets on disk, a JSON-lines log.

## 7. Nice-to-have (Phase 2–3)

- Telegram approval bot with buttons.
- YouTube API scheduled publishing with the AI-disclosure flag; TikTok inbox-draft upload.
- Analytics collection (YouTube Analytics API, TikTok Display API, weekly TikTok CSV) and winner/loser labels.
- Weekly 16:9 **Episode** (5–10 min) for YouTube watch hours.
- Nightly runs via Windows Task Scheduler; Google Drive backups.
- Learn loop feeding winning patterns into ideation.
- Optional n8n dashboard; Wan 2.2 open-model video generation experiment.

## 8. Future (Phase 4+)

- Second niche: Telecom ("Inside Tech and Telecom") as its own channel profile.
- Instagram and Facebook Reels.
- Postgres and a cloud server or always-on mini-PC.
- Paid upgrades when results justify them (see `ROADMAP.md` §6).

## 9. Non-goals (explicitly out of scope)

- **No** re-uploading, re-cutting or copying of other creators' videos, voices, music or likeness.
- **No** scraping that violates terms of service. Only official APIs or permitted data sources are used.
- **No** fake engagement: no bots, bought views, follow-for-follow or automated comments.
- **No** removing human approval before the conditions in `ROADMAP.md` are met. Gate 2 is never removed.
- **No** cloned real voices and no realistic depictions of real people.
- **No** affiliate links at launch (the field exists but is disabled).
- **No** German-language content at launch.
- **No** paid services until an upgrade trigger in `ROADMAP.md` §6 fires.
- **No** web UI. The CLI and Telegram are the interfaces.

## 10. Open questions

| # | Question | Owner | Resolve by |
|---|---|---|---|
| OQ-1 | ~~Exact Gemini API free-tier limits (RPM/RPD) for the chosen model~~ **Resolved 2026-10-23:** `gemini-3.5-flash-lite`, 15 RPM / 500 RPD (`INTEGRATIONS.md` §1, D-051) | Claude (read in AI Studio with the owner's key) | ROADMAP task 0.5 |
| OQ-2 | Details of YouTube's announced incentive programs for channels under the Shorts threshold | Claude | Before Phase 2 |
| OQ-3 | TikTok app review requirements for Login Kit, Content Posting (inbox) and Display API; redirect URI for a local app | Claude | ROADMAP task 2.4 |
| OQ-4 | Whether the YouTube Analytics API exposes "stayed to watch" for Shorts; otherwise read it manually in Studio | Claude | ROADMAP task 2.5 |
| OQ-5 | Claim `@coilandcog` on YouTube and TikTok; trademark search result for "Coil & Cog" | Owner | ROADMAP task 0.2 |
| OQ-6 | Access to Google's official Trends API | Claude | Phase 3 |
| OQ-7 | ~~Final Kokoro narrator voice (`am_michael` vs `am_adam` vs `am_onyx`) after a listening test~~ **Resolved 2026-10-23:** `am_michael` (D-060) | Owner | ROADMAP task 1.4 |
| OQ-8 | Instagram/Facebook publishing API requirements | Claude | Phase 4 |
| OQ-9 | Exact current prices of paid upgrades | Claude | When an upgrade trigger fires |
| OQ-10 | ~~Re-read the Flow-specific terms of service for commercial use of outputs~~ **Resolved 2026-10-23:** commercial use allowed, Google claims no ownership (D-052) | Claude | ROADMAP task 0.5 |
