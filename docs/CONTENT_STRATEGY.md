# CONTENT_STRATEGY.md

*Last updated: 2026-10-23. The machine-readable version of this profile lives in `config/channels/coil-and-cog.yaml`.*

## 1. Niche profile

| Field | Value |
|---|---|
| Channel | **Coil & Cog** (`@coilandcog`), slug `coil-and-cog` |
| Niche | How Machines Work |
| Promise | "See what moves inside the machines that move the world." |
| Language / region | English, aimed at US, UK and DE viewers |
| Pillars | `vehicles`: cars, trucks, motorbikes and trains. `air_and_sea`: aircraft, helicopters, ships and rockets. `industry_and_energy`: heavy machines, cranes, factories and power generation (D-073). |
| Planned second niche | Telecom ("Inside Tech and Telecom"), a separate channel profile in Phase 4 |

## 2. Audience

- **Primary:** curious general viewers aged 18–44, mostly men, in the US, UK and DE. They watch for satisfaction ("so *that's* how it works") and quick learning.
- **Secondary:** car, aviation and engineering enthusiasts who want accurate mechanics.
- **What they reward:** a clear visual reveal, one surprising fact, no fluff, accuracy.
- **What makes them leave:** slow intros, hype, wrong facts, repeated templates.

## 3. Content pillars and seed topics

**`vehicles`:** alternator, turbocharger, CVT gearbox, differential, disc brakes and ABS (how they work, *not* how to repair them), electric car motor, diesel-electric locomotive, train pantograph, maglev train.

**`air_and_sea`:** jet engine, helicopter rotor (swashplate), aircraft landing gear, variable-pitch propeller, ship diesel engine, azimuth thruster, ship stabilizers, rocket engine.

**`industry_and_energy`:** tunnel boring machine, excavator hydraulics, tower crane, hydraulic press, wind turbine, hydroelectric turbine, steam turbine, transformer, elevator safety brake.

Small household objects (zippers, pens, locks and similar) are out of scope: the channel is about big, real machinery (D-073).

### Blocklist (never make)

These are enforced by `niche.blocklist` in the YAML and checked at the ideate and script stages:
- Weapons and firearm mechanisms.
- Military aircraft, warships and weapon systems.
- Submarines: AI video draws them as navy warships whatever the prompt says, and their mechanism is hidden inside the hull (D-093).
- Step-by-step dangerous repairs (mains electricity, airbags, fuel systems, brakes).
- Defeating safety or emissions devices.
- Real brand logos or endorsements.
- Disasters or accidents involving real victims.

## 4. Series formats

| Format | Series title pattern | Length | Aspect | Platforms | Job |
|---|---|---|---|---|---|
| **Short** | "{Object} in 40 seconds" | 30–50 s (~71–118 words) | 9:16 | YouTube Shorts, TikTok | Reach and followers |
| **Deep Dive** | "Cutaway: {Object}" | 65–150 s (~154–355 words) | 9:16 | TikTok (earns money, since it's over 1 min), YouTube Shorts | First income on TikTok |
| **Episode** (long video, D-128) | "Inside the {Object}: How It Works" | 8–9.3 min (480–560 s) | 16:9 | YouTube only (no TikTok render) | Watch hours (YPP); 3–4 Shorts are cut from each (§14) |

Why: on YouTube, any vertical or square video of 3 minutes or less is a Short, and Shorts don't count toward watch hours. Only Episodes (over 3 min) build watch hours. TikTok only pays for videos over 1 minute.

## 5. Script structure

**Long videos (D-129):** 4 chapters (titles become YouTube chapters), each 4–6 scenes. Every scene uses one of the Blender model's scene templates (alternator: whole, exploded, rotor, stator, rectifier, brushes, note) and explains only what that view shows; a part may be mentioned elsewhere only after a scene that shows it explained it. The first scene (template `whole`) opens with a hook of at most 16 words; the Engineer's Note is its own scene (template `note`) in chapter 4; the last scene is a closing statement, never a question. Written by the long-script model in steps (outline, then one call per chapter), about 1,136–1,325 words (480–560 s).

The narrator speaks at about 142 words per minute (`voice.target_wpm`, measured on Kokoro `am_michael`, D-066). The script stage enforces the word range from the channel YAML: `min_s × target_wpm / 60` to `max_s × target_wpm / 60` words (Short 71–118, Deep Dive 154–355), and asks the model for the middle of that range.

**Short (~40 s)**

| Time | Part | Rule |
|---|---|---|
| 0–2 s | Hook | One line of at most 12 words that names the machine or states the video's most surprising fact; see §6. Shot 1 is always a moving Flow clip, so motion starts at frame 1 (D-090). |
| 2–8 s | Setup | Why this object matters to *you* |
| 8–30 s | Explanation | 2–3 beats, one idea each |
| 30–36 s | Engineer's Note | One sentence of insight from the owner |
| 36–40 s | Loop line | A complete sentence (no trailing "...") that flows back into the hook in new words, never the hook repeated (D-062, D-089) |

**Deep Dive (~120 s):** hook (0–3 s) → setup (3–15 s) → 4–6 explanation beats → one lesser-known fact, stated plainly → Engineer's Note (10–15 s) → recap in one sentence → loop line.

**Engineer's Note** (required in every script): one insight or real-world observation from the owner, opening with "As an engineer, what surprises me is that…" (D-090), for example "As an engineer, what surprises me is that a few amps in the rotor control over 100 amps of output." After that lead-in, no claim of what viewers know or miss ("What most people miss is…"; the script stage sends such a draft back, D-084; the same applies to the body, D-088, where "As an engineer" is not allowed) and no repeat of a sentence the body already says. The hook may tease the same fact (D-090). The AI drafts a suggestion; the owner edits or replaces it at Gate 1. This is the main protection against the inauthentic-content policy.

## 6. Hook formulas (`hook_type` values)

No two consecutive videos may use the same `hook_type`.

| `hook_type` | Formula | Example |
|---|---|---|
| `why_question` | "Why does/doesn't {machine} {surprising behavior}?" | "Why doesn't your car battery die while you drive?" |
| `hidden_inside` | "You've seen a {machine} {where viewers see it}, but never {the surprising thing inside}." (D-090: names the machine) | "You've flown behind a jet engine, but never seen the fire inside." |
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

- **Planned mix with long videos (owner, 2026-11-08, D-128):** 1 long video + 3 Shorts a week (Shorts Mon/Wed/Fri, the long video on Sunday); Deep Dives stop. It goes into `cadence.weekly_plan` with ROADMAP 2.31, when the first long video can really be produced; until then the plan below stays.

- **Launch:** 5 videos/week, Mon–Fri at **17:00 Europe/Berlin**. Default weekly mix: Mon Short, Tue Deep Dive, Wed Short, Thu Deep Dive, Fri Short.
- **After 2 stable weeks:** 7/week, adding Sat and Sun Shorts.
- **Phase 2:** plus 1 Episode/week (Sunday) on YouTube.
- **Variety rules:** don't post the same pillar more than 2 days in a row; don't repeat a `hook_type` or `beat_order` back to back. `factory plan` enforces them: it skips an idea that would make a pillar run 3 days, gives each slot the idea's suggested hook unless the day before used it (then the next hook in the list), rotates the beat order, and the script stage must write that hook and beat order (D-080).
- The publish time is retuned after about 30 posts using our own analytics.

## 9. Virality scoring rubric (ideate stage)

Each idea is scored from 0 to 100. Gemini scores five criteria (85 points); the factory calculates **Trend signal** (15 points) from the real discover data, because language models are unreliable with numbers (D-080): up to 9 points for rising interest (the higher of the Wikipedia and Google Trends ratios: 0 at 0.8x, full at 1.3x) and up to 6 for YouTube demand (median views: 0 at 10k, full at 1M, on a log scale). **Filmable objects only (D-092):** an idea's `object_name` must be one real machine that can be filmed as a whole and visibly moves. A system or part with no shape of its own (a cooling circuit, a fuel system) becomes the machine plus an angle: "car engine", angle "how a car engine keeps itself cool". Ideas with a score of at least `scoring.min_virality_score` (**65**) are saved as `idea` items. A machine that already has an active item (anything not rejected or archived) is never proposed again; at most 3 machines per run may come from outside the seed list. `factory plan --week next` then fills the weekly mix with the highest-scoring ideas, and only planned ideas (or items you create with `factory new`) get scripts.

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

How the code applies it (D-119): the numbers are the **YouTube** 7-day checkpoint of real posts (`metrics`, API or typed); the **channel median** is the median 7-day views of every labelled video (this one included); "Shorts" means `format = short`, everything else uses the 40% bar. A video whose stayed-to-watch or average-viewed share is missing can't be a winner (at most `average`), but views alone can still make it a loser. `factory labels show` works them out, `factory learn` and `factory report weekly` refresh them.

The owner can override any label (`factory labels set <id> winner|average|loser|auto`); a recompute never changes an owner's label, and `auto` hands it back to the rule. The ideation prompt receives the top *patterns* (pillar, `hook_type`, `beat_order`, length, topic type), never instructions to copy a specific video.

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

## 12. Short-form best practices (researched 2026-10-30, D-109)

Kept apart: what **YouTube officially states** and what is **creator consensus** (useful patterns from creator studies and guides, not platform facts).

### Official (YouTube Help)
- **Stayed to watch** ("viewed vs swiped away") is the share of times viewers stayed past the first seconds of a Short. It grades the **opening**, not the whole video. **Engaged views** count those who stayed (not counting loops).
- **Average view duration** and **average percentage viewed** are calculated **only from viewers who stayed**, so a Short can pass one and fail the other: a weak opening shows as low stayed-to-watch; a weak middle shows as low average percentage.
- Since 31 March 2025, **views count every start and replay**, so loops raise views and can push average percentage viewed above 100 % (#5: 133 %).
- YouTube publishes **no target** for stayed to watch: numbers like "70 %+" online are creator benchmarks. The fair baseline is our own similar-length Shorts in Studio. Analytics can lag a few days.
- Sources: support.google.com/youtube/answer/12220281 (content performance), /answer/10059070 (Shorts views), /answer/6085583 (data delay).

### Creator consensus (not official)
- **The first 1–2 seconds decide.** The first frame works like a thumbnail: it should be clear at a glance what the video is about (a recognisable part, not an abstract macro shot) and the opening must deliver what the hook promises.
- **Length:** about 22–45 s does best for most niches; explainers sit at the upper end (35–45 s). Retention percentage matters more than seconds: don't stretch a 30 s idea to 50 s.
- **Loops:** a last sentence that leads back into the first one, a last shot that matches the first, or a detail worth a second look earns replays.
- **Keep every Short distinct** (YouTube's inauthentic-content policy), upload natively without watermarks, and test hook variations rather than polishing one.
- Sources: creator guides and analytics blogs (Shortimize, Fastlane, Everpop, Creator Essentials, JoinBrands, 2026); TikTok-side guides say the same about the first 3 seconds.

### How the factory already matches
- Hook of at most 12 words, naming the machine or its surprising fact (D-090); shot 1 moves from the first frame (D-090); loop line that leads back into the hook (D-062, D-089); Shorts 30–50 s with the target in the middle; every video a different machine plus the similarity check (D-076).

### Proposed changes (need the owner's approval before they reach the prompts)
1. Shorts aim for **35–45 s** of narration instead of the middle of 30–50 s.
2. **Shot 1 shows the whole part, recognisably**, before any macro detail (first frame = thumbnail).
3. **Visual loop:** the last clip ends on the same view the first clip starts with.
4. **Hook variants:** the script stage writes 3 hooks and Gate 1 lets the owner pick one.
5. Reading results: low stayed to watch → fix the opening; low average % viewed → fix the middle (task 2.9's report shows both).

## 13. Topic series (D-112)

From 2026-10-31 a topic can be made as several linked Shorts (at least 3 parts, up to 5 when the topic deserves it; owner), e.g. *Jet Engine, Part 1/3: Intake, Fan and Bypass*.

- **Parts come from the drawing:** the machine's blueprint module lists its parts (title, focus, scenes), so the film of a part always shows what its script explains and the order is right. A machine needs a drawing in that many parts first (`factory series new` refuses otherwise).
- **Scripts:** each part's prompt has the outline (its own part marked), the earlier parts' narration (not to be repeated) and the drawing's verified facts (the only numbers it may say). The Engineer's Note is about the part's focus. The last line is a **closing statement, never a question**: parts before the last name what the next part shows ("Next: ..."), the last part wraps up the series. Code rejects a question.
- **Order:** a part is scripted only after every earlier part has a script (a failed part 1 makes parts 2 and 3 wait), so each part sees what came before.
- **Numbers (all blueprint scripts):** digits only ("1,600", not "sixteen hundred"), and only the drawing's verified facts, each used for what it describes (the prompt lists every fact with its meaning). The shot-list rules of Flow scripts don't apply: the film is drawn by code.
- **Similarity:** parts of one series and its teaser are not compared with each other (they share a topic on purpose); they are still compared with every other video.
- **Titles and descriptions:** code sets the title "{Topic}, Part n/N: {part title}", starts the TikTok caption with "Part n/N:", and adds "Part n of N." plus links to the teaser ("Overview: ...") and the earlier parts that are already on YouTube.
- **Order and posting:** parts are created, scripted and generated in part order; they are not put into `factory plan` (the weekly variety rules would split them). Post them on consecutive days, in order. A teaser (an overview video, e.g. #16) can go out first; the parts link back to it.

## 14. Blender long-form slate (D-121)

From 2026-11-06 each machine is made as **one long video (8–9 min, 16:9, 4 chapters)** plus **3–4 Shorts** cut from the same Blender model, each Short with its own new script. A machine can only be scripted after its Blender model exists and the owner has approved its stills (ROADMAP 2.35). Order below = suggested production order: models that share parts come next to each other (a rotor and stator built once serve the alternator, starter motor and e-car motor).

"Effort" is the modelling work for one session: **S** = simple shapes (cylinders, gears, rings), **M** = several moving assemblies, **L** = a big machine, built in simplified form (the 2 GB render card limits scene size, D-121). Every topic stays inside §3 (how it works, never how to repair it) and the blocklist.

| # | Machine (pillar) | Already in the factory | Long video: working angle | Shorts from it | Effort |
|---|---|---|---|---|---|
| 1 | Alternator (vehicles) | #5 Short published; Blender trial model | *Inside an Alternator: How Your Car Makes Its Own Electricity* (belt and pulley, claw-pole rotor, stator, three-phase AC, diodes, regulator, why it fails) | Why alternators have diodes; the slip-ring trick; what the voltage regulator does | S (exists) |
| 2 | Starter motor (vehicles) | model built (D-131); first long video | *The 1-Second Motor That Starts Every Car* (solenoid, pinion engagement, overrunning clutch, gear reduction) | Why the starter clicks; the pinion that jumps forward; why it can't run for long | S |
| 3 | Electric car motor (vehicles) | #14 idea | *Inside an Electric Car Motor* (stator windings, permanent-magnet vs induction rotor, inverter, regenerative braking) | Why EVs need no gearbox; regen braking; magnet vs induction | M |
| 4 | Turbocharger (vehicles) | #6 Short published (Flow) | *How a Turbocharger Turns Waste Heat into Power* (turbine, compressor, shaft, wastegate, intercooler, lag) | What turbo lag is; the wastegate; why intercoolers | S |
| 5 | Disc brakes (vehicles) | seed topic | *What Happens When You Press the Brake* (master cylinder, hydraulics, caliper, pads, disc, heat) | Why discs are vented; how hydraulics multiply force; why brakes fade | S |
| 6 | Four-stroke piston engine (vehicles) | new | *The Four-Stroke Engine, Part by Part* (pistons, crankshaft, camshaft, valves, firing order) | Why 4 strokes; what the camshaft does; firing order | M |
| 7 | Differential (vehicles) | #9 idea, #15 Short script approved (blueprint) | *Why Your Car Needs a Differential* (open differential, spider gears, cornering, limited-slip) | Wheels turning at different speeds; the one-wheel-spin problem; limited-slip | M |
| 8 | Manual gearbox and clutch (vehicles) | new | *Inside a Manual Gearbox* (clutch, gear pairs, synchronisers, shift forks) | What the clutch does; synchronisers; why gears have ratios | M |
| 9 | Planetary gearset / automatic gearbox (vehicles) | new | *The Gear Inside Every Automatic* (sun, planets, ring, locking members, torque converter) | One gearset, many ratios; torque converter | M |
| 10 | CVT gearbox (vehicles) | #8 Short approved (blueprint) | *A Gearbox With No Gears* (pulleys, belt, ratio change, pros and cons) | How the belt changes ratio; why CVTs feel different | S |
| 11 | Centrifugal water pump (industry_and_energy) | new | *How a Pump Moves Water Uphill* (impeller, volute, pressure, cavitation) | What the impeller does; cavitation | S |
| 12 | Hydraulic cylinder / excavator arm (industry_and_energy) | seed topic | *How Hydraulics Lift 20 Tonnes* (pump, valves, cylinder, Pascal's law, excavator arm) | Pascal's law; why oil, not air | M |
| 13 | Wind turbine nacelle (industry_and_energy) | seed topic | *Inside a Wind Turbine* (blades, pitch, main shaft, gearbox, generator, yaw) | Blade pitch; why 3 blades; the gearbox | L |
| 14 | Jet engine (air_and_sea) | #16 Short published, #17–19 series (blueprint), #10 paused | *Inside a Jet Engine* (fan, compressor, combustor, turbine, bypass) | Why the fan does most of the work; gas hotter than the turbine blades' melting point (and how the blades survive); bypass ratio | L |
| 15 | Helicopter rotor and swashplate (air_and_sea) | seed topic | *How a Helicopter Steers With One Rotor* (swashplate, collective, cyclic, tail rotor) | The swashplate trick; why a tail rotor | M |
| 16 | Aircraft landing gear (air_and_sea) | #12 idea | *How Landing Gear Folds Away* (oleo strut, retraction, locks, brakes) | The oleo strut; how it locks down | M |

What happens to the existing items: #8 CVT and #15 differential keep their blueprint films until their Blender model exists, then the owner decides whether to post them as they are or remake them; #17–19 jet series and #10 wait for model 14. The weekly mix (§8) changes in ROADMAP 2.28, when the owner sets the new `cadence.weekly_plan`.
