# CONTENT_STRATEGY.md

*Last updated: 2026-10-23. The machine-readable version of this profile lives in `config/channels/coil-and-cog.yaml`.*

## 1. Niche profile

| Field | Value |
|---|---|
| Channel | **Coil & Cog** (`@coilandcog`), slug `coil-and-cog` |
| Niche | How Machines Work |
| Promise | "See what moves inside the machines that move the world." |
| Language / region | English, aimed at US, UK and DE viewers |
| Pillars | `vehicles`: cars, trucks, motorbikes and trains. `air_and_sea`: aircraft, helicopters, ships, submarines and rockets. `industry_and_energy`: heavy machines, cranes, factories and power generation (D-073). |
| Planned second niche | Telecom ("Inside Tech and Telecom"), a separate channel profile in Phase 4 |

## 2. Audience

- **Primary:** curious general viewers aged 18–44, mostly men, in the US, UK and DE. They watch for satisfaction ("so *that's* how it works") and quick learning.
- **Secondary:** car, aviation and engineering enthusiasts who want accurate mechanics.
- **What they reward:** a clear visual reveal, one surprising fact, no fluff, accuracy.
- **What makes them leave:** slow intros, hype, wrong facts, repeated templates.

## 3. Content pillars and seed topics

**`vehicles`:** alternator, turbocharger, CVT gearbox, differential, disc brakes and ABS (how they work, *not* how to repair them), electric car motor, diesel-electric locomotive, train pantograph, maglev train.

**`air_and_sea`:** jet engine, helicopter rotor (swashplate), aircraft landing gear, variable-pitch propeller, ship diesel engine, azimuth thruster, ship stabilizers, submarine ballast tanks, rocket engine.

**`industry_and_energy`:** tunnel boring machine, excavator hydraulics, tower crane, hydraulic press, wind turbine, hydroelectric turbine, steam turbine, transformer, elevator safety brake.

Small household objects (zippers, pens, locks and similar) are out of scope: the channel is about big, real machinery (D-073).

### Blocklist (never make)

These are enforced by `niche.blocklist` in the YAML and checked at the ideate and script stages:
- Weapons and firearm mechanisms.
- Military aircraft, warships and weapon systems.
- Step-by-step dangerous repairs (mains electricity, airbags, fuel systems, brakes).
- Defeating safety or emissions devices.
- Real brand logos or endorsements.
- Disasters or accidents involving real victims.

## 4. Series formats

| Format | Series title pattern | Length | Aspect | Platforms | Job |
|---|---|---|---|---|---|
| **Short** | "{Object} in 40 seconds" | 30–50 s (~71–118 words) | 9:16 | YouTube Shorts, TikTok | Reach and followers |
| **Deep Dive** | "Cutaway: {Object}" | 65–150 s (~154–355 words) | 9:16 | TikTok (earns money, since it's over 1 min), YouTube Shorts | First income on TikTok |
| **Episode** (Phase 2) | "Coil & Cog: {Theme}" | 5–10 min | 16:9 | YouTube long-form | Watch hours (YPP) |

Why: on YouTube, any vertical or square video of 3 minutes or less is a Short, and Shorts don't count toward watch hours. Only Episodes (over 3 min) build watch hours. TikTok only pays for videos over 1 minute.

## 5. Script structure

The narrator speaks at about 142 words per minute (`voice.target_wpm`, measured on Kokoro `am_michael`, D-066). The script stage enforces the word range from the channel YAML: `min_s × target_wpm / 60` to `max_s × target_wpm / 60` words (Short 71–118, Deep Dive 154–355), and asks the model for the middle of that range.

**Short (~40 s)**

| Time | Part | Rule |
|---|---|---|
| 0–2 s | Hook | One line; see §6. A visual reveal starts at frame 1. |
| 2–8 s | Setup | Why this object matters to *you* |
| 8–30 s | Explanation | 2–3 beats, one idea each |
| 30–36 s | Engineer's Note | One sentence of insight from the owner |
| 36–40 s | Loop line | A complete sentence (no trailing "...") that flows back into the hook (D-062) |

**Deep Dive (~120 s):** hook (0–3 s) → setup (3–15 s) → 4–6 explanation beats → a "most people don't know" fact → Engineer's Note (10–15 s) → recap in one sentence → loop line.

**Engineer's Note** (required in every script): one insight or real-world observation from the owner, said plainly in the narrator's normal voice, for example "A few amps in the rotor control over 100 amps of output." No lead-in that claims what viewers know, miss or find surprising ("What most people miss is…", "Here's the surprising part…"; the script stage sends such a draft back, D-084), no "As an engineer" and no first-person claims of expertise; it should sound like a curious friend, not a lecture (D-077). The AI drafts a suggestion; the owner edits or replaces it at Gate 1. This is the main protection against the inauthentic-content policy.

## 6. Hook formulas (`hook_type` values)

No two consecutive videos may use the same `hook_type`.

| `hook_type` | Formula | Example |
|---|---|---|
| `why_question` | "Why does/doesn't {machine} {surprising behavior}?" | "Why doesn't your car battery die while you drive?" |
| `hidden_inside` | "You've seen this a thousand times, but never inside it." | "You've flown behind one of these. Here's what actually happens inside." |
| `surprising_number` | "{Big or small number} + {object}" | "This part spins 18,000 times a minute under your hood." |
| `myth_bust` | "Most people think {X}. Actually {Y}." | "A jet engine doesn't push against the air behind it." |
| `what_if` | "What happens if {part} fails or stops?" | "What happens if your alternator stops spinning?" |
| `reveal` | "Inside this {object} is a {surprising thing}." | "Inside this wind turbine is a gearbox the size of a car." |

## 7. Visual beat orders (`beat_order` values)

The 4 beats are **Studio**, **Cutaway**, **Flow** and **In situ**. They form a menu, not a fixed template. Rotate the order, and never use the same order twice in a row.

| `beat_order` | Sequence | Best with |
|---|---|---|
| `classic` | Studio → Cutaway → Flow → In situ | `reveal`, `hidden_inside` |
| `context_first` | In situ → Studio → Cutaway → Flow | `why_question`, `what_if` |
| `action_first` | Flow → Cutaway → Studio → In situ | `surprising_number` |
| `cold_open` | Cutaway → Flow → In situ → Studio (hero outro) | `myth_bust` |

## 8. Posting cadence

- **Launch:** 5 videos/week, Mon–Fri at **17:00 Europe/Berlin**. Default weekly mix: Mon Short, Tue Deep Dive, Wed Short, Thu Deep Dive, Fri Short.
- **After 2 stable weeks:** 7/week, adding Sat and Sun Shorts.
- **Phase 2:** plus 1 Episode/week (Sunday) on YouTube.
- **Variety rules:** don't post the same pillar more than 2 days in a row; don't repeat a `hook_type` or `beat_order` back to back. `factory plan` enforces them: it skips an idea that would make a pillar run 3 days, gives each slot the idea's suggested hook unless the day before used it (then the next hook in the list), rotates the beat order, and the script stage must write that hook and beat order (D-080).
- The publish time is retuned after about 30 posts using our own analytics.

## 9. Virality scoring rubric (ideate stage)

Each idea is scored from 0 to 100. Gemini scores five criteria (85 points); the factory calculates **Trend signal** (15 points) from the real discover data, because language models are unreliable with numbers (D-080): up to 9 points for rising interest (the higher of the Wikipedia and Google Trends ratios: 0 at 0.8x, full at 1.3x) and up to 6 for YouTube demand (median views: 0 at 10k, full at 1M, on a log scale). Ideas with a score of at least `scoring.min_virality_score` (**65**) are saved as `idea` items. A machine that already has an active item (anything not rejected or archived) is never proposed again; at most 3 machines per run may come from outside the seed list. `factory plan --week next` then fills the weekly mix with the highest-scoring ideas, and only planned ideas (or items you create with `factory new`) get scripts.

| Criterion | Weight | 0 = | max = |
|---|---|---|---|
| Hook strength | 25 | No natural question | Instant "wait, what?" |
| Familiarity | 20 | Obscure machine nobody has seen | Viewer has seen, driven or ridden it |
| Visual potential | 20 | Hard to show (abstract, tiny, static) | Dramatic cutaway and energy flow |
| Trend signal | 15 | No signal | Rising Wikipedia pageviews, YouTube search activity or Google Trends interest |
| Evergreen demand | 10 | One-off | Searched all year |
| Novelty vs our catalog | 10 | Similar to something we've posted | New object and new angle |

**Hard filters, applied before scoring:** blocklist match → rejected. Semantic similarity above `scoring.similarity_block_threshold` (**0.80**) to the current script of any of the last `similarity_window` (30) other items → archived with the reason (rejected and archived items don't count, D-076). The check runs on the finished script, so the scoring step of ideation (task 1.15) uses the same module. Measured with the model: a reworded script about the *same* machine scores 0.74–0.81, so 0.80 blocks near-copies only; a repeated object is caught by the "Novelty" score and by you at Gate 1.

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
- Description: 1–2 sentences of summary, the music credit if the track needs one, then 3 hashtags. No source links (D-077): the checked sources stay in the database, at Gate 1 and in `metadata.json` for fact-checking. No AI disclosure line in the text (D-075): the platform AI labels disclose it.
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
