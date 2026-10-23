# CONTENT_STRATEGY.md

*Last updated: 2026-10-23. The machine-readable version of this profile lives in `config/channels/coil-and-cog.yaml`.*

## 1. Niche profile

| Field | Value |
|---|---|
| Channel | **Coil & Cog** (`@coilandcog`), slug `coil-and-cog` |
| Niche | How Machines Work |
| Promise | "See what moves inside the things you use every day." |
| Language / region | English, aimed at US, UK and DE viewers |
| Pillars | `machines`: cars, household and industrial machines. `everyday_things`: ordinary objects with hidden mechanisms. |
| Planned second niche | Telecom ("Inside Tech and Telecom"), a separate channel profile in Phase 4 |

## 2. Audience

- **Primary:** curious general viewers aged 18–44, mostly men, in the US, UK and DE. They watch for satisfaction ("so *that's* how it works") and quick learning.
- **Secondary:** car and DIY hobbyists who want accurate mechanics.
- **What they reward:** a clear visual reveal, one surprising fact, no fluff, accuracy.
- **What makes them leave:** slow intros, hype, wrong facts, repeated templates.

## 3. Content pillars and seed topics

**`machines`:** alternator, turbocharger, CVT gearbox, differential, clutch, starter motor, spark plug, fuel injector, disc brake (how it works, *not* how to repair it), catalytic converter (how it works, *not* removal), electric motor, transformer, jet engine, hydraulic press, elevator safety brake, escalator, wind-turbine gearbox, heat pump, refrigerator compressor, hard drive.

**`everyday_things`:** zipper, ballpoint pen, pin-tumbler lock (mechanism only, never how to pick it), microwave magnetron, induction cooktop, toilet flush valve, smoke detector, quartz watch, vacuum-cleaner cyclone, washing-machine drum, inkjet printer, mechanical keyboard switch, retractable tape measure, umbrella frame.

### Blocklist (never make)

These are enforced by `niche.blocklist` in the YAML and checked at the ideate and script stages:
- Weapons and firearm mechanisms.
- Step-by-step dangerous repairs (mains electricity, airbags, fuel systems, brakes).
- Defeating safety or emissions devices.
- Real brand logos or endorsements.
- Disasters or accidents involving real victims.

## 4. Series formats

| Format | Series title pattern | Length | Aspect | Platforms | Job |
|---|---|---|---|---|---|
| **Short** | "{Object} in 40 seconds" | 30–50 s (~80–130 words) | 9:16 | YouTube Shorts, TikTok | Reach and followers |
| **Deep Dive** | "Cutaway: {Object}" | 65–150 s (~175–400 words) | 9:16 | TikTok (earns money, since it's over 1 min), YouTube Shorts | First income on TikTok |
| **Episode** (Phase 2) | "Coil & Cog: {Theme}" | 5–10 min | 16:9 | YouTube long-form | Watch hours (YPP) |

Why: on YouTube, any vertical or square video of 3 minutes or less is a Short, and Shorts don't count toward watch hours. Only Episodes (over 3 min) build watch hours. TikTok only pays for videos over 1 minute.

## 5. Script structure

The narrator speaks at about 160 words per minute.

**Short (~40 s)**

| Time | Part | Rule |
|---|---|---|
| 0–2 s | Hook | One line; see §6. A visual reveal starts at frame 1. |
| 2–8 s | Setup | Why this object matters to *you* |
| 8–30 s | Explanation | 2–3 beats, one idea each |
| 30–36 s | Engineer's Note | One sentence of insight from the owner |
| 36–40 s | Loop line | The ending sentence flows back into the hook |

**Deep Dive (~120 s):** hook (0–3 s) → setup (3–15 s) → 4–6 explanation beats → a "most people don't know" fact → Engineer's Note (10–15 s) → recap in one sentence → loop line.

**Engineer's Note** (required in every script): one insight, opinion or real-world anecdote from the owner, for example "As an engineer, what surprises me is…". The AI drafts a suggestion; the owner edits or replaces it at Gate 1. This is the main protection against the inauthentic-content policy.

## 6. Hook formulas (`hook_type` values)

No two consecutive videos may use the same `hook_type`.

| `hook_type` | Formula | Example |
|---|---|---|
| `why_question` | "Why does/doesn't {everyday thing} {surprising behavior}?" | "Why doesn't your car battery die while you drive?" |
| `hidden_everyday` | "You use this every day, but you've never seen inside it." | "You zip this 1,000 times a year. Here's what actually locks it." |
| `surprising_number` | "{Big or small number} + {object}" | "This part spins 18,000 times a minute under your hood." |
| `myth_bust` | "Most people think {X}. Actually {Y}." | "Your microwave doesn't heat from the inside out." |
| `what_if` | "What happens if {part} fails or stops?" | "What happens if your alternator stops spinning?" |
| `reveal` | "Inside this {object} is a {surprising thing}." | "Inside this pen is a tiny precision ball bearing." |

## 7. Visual beat orders (`beat_order` values)

The 4 beats are **Studio**, **Cutaway**, **Flow** and **In situ**. They form a menu, not a fixed template. Rotate the order, and never use the same order twice in a row.

| `beat_order` | Sequence | Best with |
|---|---|---|
| `classic` | Studio → Cutaway → Flow → In situ | `reveal`, `hidden_everyday` |
| `context_first` | In situ → Studio → Cutaway → Flow | `why_question`, `what_if` |
| `action_first` | Flow → Cutaway → Studio → In situ | `surprising_number` |
| `cold_open` | Cutaway → Flow → In situ → Studio (hero outro) | `myth_bust` |

## 8. Posting cadence

- **Launch:** 5 videos/week, Mon–Fri at **17:00 Europe/Berlin**. Default weekly mix: Mon Short, Tue Deep Dive, Wed Short, Thu Deep Dive, Fri Short.
- **After 2 stable weeks:** 7/week, adding Sat and Sun Shorts.
- **Phase 2:** plus 1 Episode/week (Sunday) on YouTube.
- **Variety rules:** don't post the same pillar more than 2 days in a row; don't repeat a `hook_type` or `beat_order` back to back.
- The publish time is retuned after about 30 posts using our own analytics.

## 9. Virality scoring rubric (ideate stage)

The LLM scores each idea from 0 to 100. Ideas with a score of at least `scoring.min_virality_score` (default **65**) go on to scripting. The highest-scoring ideas fill the weekly mix.

| Criterion | Weight | 0 = | max = |
|---|---|---|---|
| Hook strength | 25 | No natural question | Instant "wait, what?" |
| Everyday relevance | 20 | Niche industrial curiosity | Viewer touches it daily |
| Visual potential | 20 | Hard to show (abstract, tiny, static) | Dramatic cutaway and energy flow |
| Trend signal | 15 | No signal | Rising Wikipedia pageviews, YouTube search activity or Google Trends interest |
| Evergreen demand | 10 | One-off | Searched all year |
| Novelty vs our catalog | 10 | Similar to something we've posted | New object and new angle |

**Hard filters, applied before scoring:** blocklist match → rejected. Semantic similarity above `scoring.similarity_block_threshold` (default **0.80**) to any of the last 30 scripts → rejected.

## 10. Winner / loser labels (learn stage)

| Label | Rule, measured 7 days after publishing |
|---|---|
| `winner` | Views ≥ 3× channel median **and** stayed-to-watch ≥ 70% **and** average view duration ≥ 80% (Shorts) or ≥ 40% (over 60 s) |
| `loser` | Views ≤ 0.5× channel median **or** average view duration below half of the winner threshold |
| `average` | Everything else |

The owner can override any label. The ideation prompt receives the top *patterns* (pillar, `hook_type`, `beat_order`, length, topic type), never instructions to copy a specific video.

## 11. Platform-specific rules

### YouTube
- Shorts and Deep Dives are uploaded as Shorts (9:16, 3 minutes or less). Episodes are 16:9 and longer than 3 minutes.
- Title: 70 characters or fewer, with the object name early; no clickbait that the video doesn't deliver.
- Description: 1–2 sentences of summary, a "Sources:" list (at least 2), and the disclosure line "Visuals AI-generated; script researched and reviewed by a human engineer.", followed by 3 hashtags.
- `containsSyntheticMedia = true` and `selfDeclaredMadeForKids = false`. Category: Science & Technology (ID 28).
- Music: YouTube Audio Library only.

### TikTok
- Account type must stay **Personal**, because Creator Rewards excludes Business accounts.
- Deep Dives must be longer than 60 s to earn. We target at least 65 s for safety.
- Caption: a hook line plus 3–5 hashtags. Turn on the AI-generated content label when posting.
- Music: the video is exported **without music**; the owner adds a sound from TikTok's library when posting.
- Never upload a file with another platform's watermark.

### Both
- The same visuals and narration go to both platforms. Only the music bed and metadata differ.
- No engagement bait ("comment 1 if…") and no misleading thumbnails or covers.
