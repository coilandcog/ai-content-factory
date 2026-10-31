# DESIGN_SYSTEM.md

*Last updated: 2026-10-22. Values here mirror `brand:` and `voice:` in `config/channels/coil-and-cog.yaml`. If you change one, change the other.*

## 1. Brand

| Item | Value |
|---|---|
| Name | **Coil & Cog** |
| Handle | `@coilandcog` (YouTube and TikTok) |
| Tagline | "See what moves inside." |
| Logo | A text logo, "COIL & COG", in Montserrat ExtraBold. The `&` is in cyan `#00D1FF`; the rest is off-white on graphite. Built in code (Pillow), so there's no third-party license risk. Saved in `assets_static/logo/` by `factory brand build`: `logo.png` (transparent), `logo_on_bg.png` (on graphite). |
| Avatar | `avatar_800.png`: an 800×800 monogram, "C&C" with the cyan `&`, on graphite. The full name would be unreadable at profile-picture size, and the monogram stays inside the circle crop YouTube and TikTok apply. Use it as the profile picture on both platforms. |
| Personality | A calm, curious engineer friend who shows you the inside of things |

## 2. Voice and tone

- **Tone:** calm, confident and curious. One small witty line per video. Speak directly to the viewer ("you").
- **Banned words:** insane, mind-blowing, crazy, you won't believe, SHOCKING, and all-caps hype generally.
- **Narrator:** AI voice, **Kokoro-82M**, male, neutral American English, warm, mid-to-low pitch, about 142 wpm (measured at speed 1.0; D-066). **Chosen voice: `am_michael`** (owner's pick after the task 1.4 listening test against `am_adam` and `am_onyx`, D-060). It must stay the same from now on, so viewers recognise the channel by ear.
- **Never:** clone or imitate a real person's voice.
- **Fallback (decision D-040):** if YouTube Partner Program review rejects the channel for inauthentic content, the owner records the Engineer's Note in their own voice.

## 3. Color palette

| Token | HEX | Use |
|---|---|---|
| `bg` | `#0E1116` | Graphite: backgrounds, pedestal, text outline |
| `electric` | `#00D1FF` | Cyan: electricity, active subtitle word, brand accent |
| `heat` | `#FF8A00` | Amber: heat, friction, motion, warnings |
| `fluid` | `#3A7BFF` | Blue: fluids and air flow |
| `text` | `#F5F7FA` | Off-white: subtitles and labels |

**Energy color language:** these mappings are fixed so viewers learn them. Cyan always means electricity, amber means heat or motion, blue means fluid or air.

## 4. Typography

These are free Google Fonts under the Open Font License, so commercial use is OK. They are stored in `assets_static/fonts/`.

| Use | Font | Size (at 1080 px wide) |
|---|---|---|
| Subtitles | Montserrat ExtraBold | 78 px |
| Diagram labels | Inter SemiBold | 44 px |
| Thumbnails (Episodes) | Montserrat ExtraBold | 150–190 px |

## 5. Video technical spec

| Item | Short / Deep Dive | Episode |
|---|---|---|
| Resolution | 1080×1920 (9:16) | 1920×1080 (16:9) |
| Frame rate | 30 fps | 30 fps |
| Codec | H.264 High, yuv420p, CRF 18, MP4 with `+faststart` | same |
| Audio | AAC 192 kbps, 48 kHz | same |
| Loudness | −14 LUFS integrated, true peak ≤ −1 dBTP | same |

- Flow free-tier clips are 720p. They're upscaled with the Lanczos filter, a sharp resizing method, and slow-zoom moves are kept small to hide softness.
- **Episode layout:** the 9:16 clip sits in the center. The 16:9 side panels show diagrams, labels and key facts on graphite. Only the opening shot is regenerated natively in 16:9.

## 6. Subtitles

- **Format:** ASS subtitle file (a format that supports styling and per-word timing), burned into the video.
- **Style:** Montserrat ExtraBold 78 px, text `#F5F7FA`, 6 px outline in `#0E1116`, no shadow.
- **Word highlight:** the current word turns cyan `#00D1FF` from the moment it starts until the next word starts. Timing comes from **Kokoro's own token timestamps** (it builds the audio from them, so they are exact for that audio), aligned to the approved script text (D-065). Word starts were measured within 0.06 s of the audio; Kokoro stretches a sentence's last word over part of the following pause, which only keeps that word cyan a little longer.
- **Layout:** at most 2 lines and at most 4 words per line, centered, each line at most **760 px** wide (measured with the real font). A centered 820 px block would reach x = 950 and break the right-hand safe zone below, so 760 px is the limit (D-065). A sentence end or a pause over 0.6 s starts a new page; the last page stays 0.3 s after its last word.
- **Position:** the text baseline sits about 30% from the bottom (y ≈ 1344 px).
- **Spelling:** always from the approved script, never from the voice engine's tokens.
- **Files:** `factory voice render <id>` (and `generate`) writes `voice.wav`, `voice_words.json` (each script word with start/end seconds) and `subs.ass`; `factory voice preview <id>` writes `subs_preview.mp4` (540×960, dark background, burned-in subtitles) for a phone check. Burning needs an FFmpeg with libass: settings.yaml `tools.ffmpeg` (D-065).

### 9:16 safe zones (keeping clear of TikTok and YouTube buttons)

- Top 220 px: no text.
- Bottom 480 px: no text, because captions and buttons sit there.
- Right 160 px: no text, because the like and share buttons sit there.
- Labels and diagrams stay inside the box x 90–920, y 220–1440.

## 7. Motion graphics (diagrams)

These are our own work and a major part of the "original value" each video adds.

- **Elements (launch, D-067): the label card.** AI footage doesn't tell us where each part sits in the frame, so arrows would point at the wrong place. A rounded graphite card (~80% opaque, 3 px outline in the energy colour) in the upper safe area (x 90–920, y 260 to at most 1100, clear of the subtitles) lists 1–4 part labels, each with a dot in the energy colour (cyan electric, amber heat, blue fluid; cyan if none). Inter SemiBold 44 px, shrunk to no less than 30 px if a label wouldn't fit. Arrows, flow paths, cross-sections and number callouts need part positions, so they wait until we have them.
- **Source:** the script's diagram shots carry `labels` (1–4, ≤4 words) and `energy`; `factory generate` draws `diagrams/diag_NN.png` and a `diag_NN_preview.jpg` (the card over a clip frame) to check readability.
- **Rendering:** Pillow produces transparent PNG layers, and FFmpeg overlays them with fades and slides of 6–10 frames.
- **Amount:** at least 2 diagram moments per Short and at least 4 per Deep Dive.
- **Labels:** 4 words or fewer; part names in Title Case.

## 8. Audio mix

- **Voice:** −14 LUFS target after mastering. Light compression and a high-pass filter at 80 Hz.
- **Music:** about 15 dB under the voice on average (owner's pick by ear, D-069): about 6 dB under in pauses and under the sting, ducked several dB more while the narrator speaks (sidechain ducking: the music automatically gets quieter whenever the voice is present); fades out over the last 1.5 s. The earlier "22 dB under" was inaudible on phone and laptop speakers.
  - **YouTube render:** a track from the YouTube Audio Library, registered with `factory music add` and picked least-used first.
  - **TikTok render:** no music at all (verified: digital silence where nobody speaks). The owner adds a TikTok sound when posting.
- **Veo's own sound** (D-068): the Flow clips' generated sound (motors, clicks, whirs) plays under its clip in **both** renders, about 10 dB under the voice in pauses and ~18 dB under it while the narrator speaks (ducked like the music). It's Flow output (D-052) and not music, so D-042 still holds. Stills and diagrams are silent. Other sound effects: none for now.
- **Master:** the whole mix is brought to −14 LUFS with a true-peak limiter (≤ −1 dBTP), the same method as the voice.

## 9. Intro, outro, covers and thumbnails

- **Intro:** none. The hook line and visual start at frame 1.
- **Outro:** the loop line, then a 1-second logo sting (the logo fades in with a cyan "spark" sweep). The sting is built by `factory brand build` and stored as `assets_static/logo/sting_1080x1920.mp4` and `sting_1920x1080.mp4`: 1.0 s, 30 fps, H.264 `yuv420p`, with a silent 48 kHz stereo AAC track so it joins cleanly onto narrated clips. The logo fades in over 0.45 s and the spark crosses the letters between 0.10 s and 0.75 s. Frames are drawn with Pillow and encoded by FFmpeg (the installed FFmpeg has no `drawtext` filter).
- **Blueprint covers (D-111):** the sharpest frame of `blueprint.mp4`, licence `own_work`.
- **Shorts and Deep Dive covers:** the best Cutaway frame, picked automatically as the sharpest of 8 frames from the Cutaway beat's clip (else the first clip), saved as `cover.jpg` (1080×1920, no text) by `factory assemble`, then confirmed by the owner at Gate 2.
- **Episode thumbnails:** one object, glowing and cut open, on graphite; at most 3 words in Montserrat ExtraBold; 1280×720 JPG under 2 MB.

## 10. AI generation prompt templates

These templates live in `prompts/default/` as Jinja2 files (a text-template format where `{{ variable }}` gets filled in). A channel can override them in `prompts/<slug>/`. They're rendered by the generate stage into a **Flow prompt sheet** (`flow_sheet.md`) that the owner copies into Google Flow. The files are `flow_shot.j2` (one template for every clip), `style_block.j2`, `flow_sheet.j2` and `visual_rules.j2` (the film rules Gemini follows). Colours are named in plain words (cyan, amber), because video models follow plain words better than hex codes.

**Since D-092 the clips of a video are the shots of one short cinematic film.** At the script stage Gemini writes the film, so every clip is specific to its machine instead of a fill-in-the-blanks template (D-074 is replaced):
- **`hero_look`** (20–80 words, once per video): exactly how this machine looks: a real, typical example, its size, shape, materials and colours, **never a place or background** (each scene names its own place; D-095), always **brand-new and flawless** (smooth, polished, crisp reflections, no rust, dirt or wear), like the alternator and turbocharger product films the owner liked. It is repeated word for word in every clip prompt, so Veo draws the same machine in every shot. When the topic is a system inside a bigger machine (ballast tanks, a gearbox), the hero is the whole machine and the cutaway goes inside it; a pedestal is only for a part small enough to carry. Never a military version.
- **Close framing (D-095):** in every camera angle the machine fills at least half of the frame, like a product film; never a whole vehicle in a big landscape or a wide establishing shot, because Veo loses track of a big scene (#10's engine away from its wing, #11 surfacing). **In situ** is a tight cinematic close-up where the machine is mounted on a vehicle or in a plant that **stands still** (the engine pod under a parked airliner's wing at dusk, the alternator in an open engine bay); the vehicle never flies, drives or dives.
- **Flow direction (D-096):** cutaway and flow shots show the machine from the side, cut open along its length; each flow names its screen direction ("air enters on the left and leaves on the right") and moves only that way, with flames and hot gas only where they really are. The camera never looks head-on into an inlet or outlet in these shots: from the front Veo can't tell in from out (#10's first flow clip shot air and fire out of the intake). The flow-beat clip prompt adds "Everything flows only in the direction described; nothing flows backwards or out of the wrong opening." An image can't show direction, so check this on the video.
- **Redo one clip (D-096):** `factory generate --restage N --shots 3` (or `2,4`) rewrites only those clips; the hero_look and the other clips stay, and Gemini sees them so the film stays continuous.
- **Clip review (D-097):** `factory clips review <id>` lets Gemini watch each saved clip and frame image and check it against its prompt and these rules (whole real machine, one place, flow direction, sharp spinning parts, polished, nothing forbidden). Save the free frame image as `frame_NN.png` and review it before making the 20-credit video; review the clip for what an image can't show (direction, motion, blur). Advice only: the owner still decides at Gate 2.
- **Brief prompts are the default (D-105):** `video_prompts: brief` in the channel YAML writes every clip like the prompts of the channel's best videos (the alternator and turbocharger): "Create a premium 8-second <product video / technical cutaway video / technical animation / video … at work> of the <machine>, vertical 9:16, with three smooth camera angles." followed by three plain sentences (start close on one clear action, then one smooth camera move while the moving part works, finish on a close-up) and the shared closing block. Gemini follows the short `visual_rules_brief.j2` with the benchmark prompts as examples, plus approved references and lessons. Each clip gets one optional free **check image** (`image_NN.png`, reviewed by `clips review`); no image chains. `video_prompts: story` switches to the story shots below.
- **Story shots (D-104):** each clip has a **style** that fixes its camera: `product` (three smooth angles), `open` (almost still, one slow move while the casing opens), `explain` (camera locked; a machine that air, fuel, fluid or exhaust flows through stands upright as a vertical cutaway, intake at the top, everything flows top to bottom, cool blue turning amber and red where heat is involved; a part that only passes on turning force, such as a gearbox, differential or brake, is a clean side cutaway with every part in its real place, D-110), `close`, `installed` (vehicle standing still, two or three gentle angles) and `moving` (camera fixed to the vehicle on the part; only the surroundings move). Each clip runs between a **start image** and an **end image**; a clip that `continues` the story starts on the previous clip's end image. The Flow sheet lists the images first (`image_01.png`, … all free) and then each video with its start and end image (Frames to video). Fewer camera moves and two approved pictures leave the video model little to invent: the #10 failures (an exhaust cone at the intake, hollow rings appearing, parts floating apart) all came from a roaming camera re-imagining a complex machine. In brief mode (the default) check images never get the upright sentence, so each image shows what its brief clip will (D-110). The film rules add plain "no invented physics" rules (parts never swap ends, never appear, float or change shape; nothing changes place inside a shot).
- **Fairer clip review (D-109):** only problems an ordinary viewer would see fail a clip (a part through solid metal, fusing, morphing, wrong place, wrong direction, blur); construction details only an engineer would notice are **expert notes**. Each file gets up to 3 votes and the majority decides, because flash-lite gave the same #8 clip 9/10 and then 3/10.
- **Lessons in the Flow prompts (D-108):** approved lessons are also written at the end of every Flow prompt as "Rules from earlier clips: …" (clip lessons in video prompts, frame lessons in image prompts), so the video model itself reads them, not only Gemini while it writes. Approved lessons should therefore read as plain rules about the picture ("belts run as continuous loops between the pulley faces"), not as advice to a writer ("Specify that …").
- **Lessons (D-103):** `factory lessons learn` turns failed clip reviews into short general lessons ("the camera never passes into solid parts"); after the owner approves them they are part of every new prompt and every clip review, so a mistake made once is avoided and caught on later videos.
- **Reference library (D-100):** style notes Gemini writes about images and clips the owner likes (`factory references add`) reach the film rules only after the owner approves them, newest 8, with the owner's own note first. Style only: the files are never used in a video and names, brands and logos are never described.
- **Prompt versions (D-098):** `flow_issued.json` keeps each prompt's fingerprint and the time it was issued in that form (a restage changes only the prompts that really changed). `clips review` marks a file saved before its current prompt with "⚠ … made from an older prompt, remake it".
- **Image first (D-095):** Flow images cost 0 credits, so the Flow sheet gives every clip a *Frame* prompt (`flow_frame.j2`: the clip's opening frame as a still image). The owner generates the image, regenerates it for free until the composition is right, then makes the video from it with **Frames to video** (20 credits). Still images are now recommended too, since they're free.
- Each scene names exactly one place ("fully submerged in deep water, no water surface in view"): no details from another place (a waterline, a dock) that Veo could draw instead, because #11's first film shot surfaced mid-clip when the look mentioned "the waterline".
- Per `flow_clip`: **`scene`** (40–80 words, one shot in concrete physical detail, in one place that never changes inside the clip), **`camera`** (**three smooth camera angles** that flow into each other, each with a lens; the owner's choice), **`end_frame`** (the final picture). The next clip's prompt starts with "This shot continues the film from the previous shot, which ended on: …", so the clips cut together like a film.
- The beats still give the story order: Studio (the machine alone on a dark set) → Cutaway (the casing opens) → Flow (glowing energy) → In situ (the machine at work), in the script's `beat_order`.
- **Benchmarks (D-094):** `benchmarks` in the channel YAML lists the owner's best videos (now #5 alternator and #6 turbocharger), with the shot numbers that worked, what the owner liked and what to do better. When Gemini writes film shots (new scripts and `--restage`), it gets those notes plus the **exact Flow prompts** of the good shots (read from each item's `flow_prompts.json`) as examples of the detail, motion and polish to match. Gemini can't watch the videos; the prompts that made them and the owner's notes are what it learns from. To add a benchmark, add an entry to the YAML (no code); a missing prompts file only gives a warning.
- **Spinning parts** (rotors, shafts, turbine and compressor wheels, fans) turn in smooth slow motion so every blade stays sharp: in #5 and #6 the spinning parts were the weakest shots.
- Approved scripts from before D-092 get their film shots with `factory generate --restage <id>`: the narration stays as approved, and only the clip prompts (and, for an item waiting for clips, the Flow sheet) are rewritten.

### 10.1 Shared closing block (`style_block.j2`), added to the end of every clip prompt

The cyan and amber lights stay as the channel's signature, as accents on top of the scene's own realistic light (the owner's choice, D-092):

```
Lighting: the scene's own realistic light plus the channel's signature accents, a cool cyan light on one side and a warm amber light on the other. Brand-new, flawless surfaces: smooth polished metal and clean paint with crisp, glossy reflections, no rust, dirt or wear, like a premium product film. Spinning parts turn in smooth slow motion, every blade and edge sharp, no motion blur. Real proportions, shallow depth of field, sharp detail, photorealistic.
No logos, no text, no brand names, no people, no hands, no faces, no extra or distorted components.
Sound: realistic mechanical sounds of the machine only; no music, no voices, no narration.
```

### 10.2 Clip template (`flow_shot.j2`)

```
Vertical {{ aspect }}, {{ clip_seconds }}-second shot from a cinematic short film that shows how it works inside: {{ object }}. Photorealistic, like a high-end documentary.
The machine: {{ hero_look }}.
[shots 2+] This shot continues the film from the previous shot, which ended on: {{ previous_end }}.
{{ scene }}.
[flow beat] Glowing {{ energy_color_name }} light traces show the {{ energy_word }} moving through the parts, clear and readable, not chaotic.
Camera, three smooth angles: {{ camera }}. Smooth, fluid transitions between the angles; the place, the machine and the physics stay the same from the first to the last frame[; shot 1: the camera and the machine are already moving in the very first frame, no fade-in].
The shot ends on: {{ end_frame }}.
{{ style_block }}
```

### 10.3 Still-image template (`still_image.j2`), used for animated stills

```
Create a premium photorealistic product image of the {{ object }}, vertical {{ aspect }}.
{{ description }}.
Deep graphite studio background, realistic materials, polished commercial lighting with a cool cyan rim light from the left and a warm amber rim light from the right, sharp product detail.
Composition leaves the {{ safe_area_hint }} clear for labels and subtitles.
No logos, no text, no brand names, no people, no hands, no faces, no extra or distorted components.
```

**Stills are optional for the owner (D-067):** the Flow sheet lists a ready prompt per `still` shot. If `still_NN.png` (or .jpg/.webp) is in the Flow inbox it is used; otherwise the factory takes the sharpest of 8 frames from a clip of the same beat (else the nearest shot). Each still becomes `stills/still_NN_anim.mp4`: a 1080×1920, 30 fps Ken Burns move (zoom 1.00↔1.08 with a slight pan, eased, alternating per shot). Parallax would need a depth model and is not done.

### 10.4 Episode opener (16:9) (`flow_episode_opener.j2`)

```
Create a premium 8-second cinematic video, horizontal 16:9.
Open on a wide establishing shot of {{ theme_scene }}, then a slow dolly in, cinematic and calm.
{{ style_block }}
```

### 10.5 Prompt rules

- Free-tier credit plan: final clips use **Veo 3.1 Fast**, about 2 per day. Use Lite only to test prompts.
- Each shot is about 8 s. Plan 3–5 Flow shots per video; animated stills and diagrams fill the rest.
- Three smooth camera angles per clip, all in the same place; the prompt says the place and physics never change (D-092).
- Never ask for real brand names, logos, real people or real identifiable locations.
- Record the exact prompt text for every generated asset in the `assets.prompt` field.

## 11. Blueprint films (D-111)

A blueprint film is drawn by code (`src/factory/blueprint/`), so the mechanism is calculated, never guessed: a CVT belt keeps one length while the pulleys change, jet-engine air only ever moves forward and leaves faster than it came in. It is the channel default for new items (`video_style: blueprint`); Flow stays available per item (`factory new --style flow`).

- **Look:** navy blueprint paper (`#0A1E36`) with a 30 px grid (every 5th line brighter), light-blue line work, the channel's energy colours: cyan for electricity and cool air, amber/red for heat, blue for incoming air and fluids. Titles in Montserrat ExtraBold, labels in Inter SemiBold.
- **Layout (1080×1920):** a scene title at the top (y ≈ 120); the machine in the upper two thirds; **y 1180–1360 stays empty for the subtitles**; readouts, charts and close-ups below (y 1400–1860).
- **Story:** one scene per part of the script: hook, body scenes, the Engineer's Note, the closing line. Scenes start on their sentences (from `voice_words.json`, 0.15 s early) and crossfade over 0.3 s, so a changed note still lines up.
- **Verified truth (owner rule):** every number on screen comes from the machine's `FACTS` (text + source page); counters count up to a fact's value, and readouts the drawing calculates (a ratio) are listed in `LIVE`. `generate` refuses a film that shows any other number. When sources disagree, the film shows a range that covers both (blade alloys melt at "about 1,100-1,350 °C").
- **Audio:** the narration and music are added by `assemble` as for every video; the film itself is silent.
- **A new machine** needs one drawing module (one session with Claude); until it exists, a blueprint item waits at `script_approved` with a warning.
