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
- **Music:** sits about 22 dB under the voice, with sidechain ducking (music automatically gets quieter whenever the narrator speaks).
  - **YouTube render:** a track from the YouTube Audio Library.
  - **TikTok render:** no music. The owner adds a TikTok sound when posting.
- **Sound effects** (Phase 2, optional): only self-made or from YouTube's Audio Library, and only in the YouTube render.

## 9. Intro, outro, covers and thumbnails

- **Intro:** none. The hook line and visual start at frame 1.
- **Outro:** the loop line, then a 1-second logo sting (the logo fades in with a cyan "spark" sweep). The sting is built by `factory brand build` and stored as `assets_static/logo/sting_1080x1920.mp4` and `sting_1920x1080.mp4`: 1.0 s, 30 fps, H.264 `yuv420p`, with a silent 48 kHz stereo AAC track so it joins cleanly onto narrated clips. The logo fades in over 0.45 s and the spark crosses the letters between 0.10 s and 0.75 s. Frames are drawn with Pillow and encoded by FFmpeg (the installed FFmpeg has no `drawtext` filter).
- **Shorts and Deep Dive covers:** the best Cutaway frame, picked automatically as the sharpest frame in the Cutaway beat, then confirmed by the owner at Gate 2.
- **Episode thumbnails:** one object, glowing and cut open, on graphite; at most 3 words in Montserrat ExtraBold; 1280×720 JPG under 2 MB.

## 10. AI generation prompt templates

These templates live in `prompts/default/` as Jinja2 files (a text-template format where `{{ variable }}` gets filled in). A channel can override them in `prompts/<slug>/`. They're rendered by the generate stage into a **Flow prompt sheet** (`flow_sheet.md`) that the owner copies into Google Flow. Since task 1.6 they are real files (`style_block.j2`, `flow_shot_<beat>.j2`, `flow_sheet.j2`); colors come from `brand.palette`, not hard-coded hex values, and each beat template ends with a `Scene:` line holding the shot's description from the script.

### 10.1 Shared style block (`style_block.j2`), added to every visual prompt

```
Photorealistic cinematic product visualization, {{ aspect }} vertical frame.
Deep graphite studio background (#0E1116), matte graphite pedestal.
Dramatic cyan (#00D1FF) rim light from the left, warm amber (#FF8A00) rim light from the right.
Shallow depth of field, crisp metal and copper detail, subtle film grain, slow smooth camera motion.
No text, no logos, no brand names, no people, no hands, no faces.
```

### 10.2 Beat templates (`flow_shot_<beat>.j2`)

**Studio**
```
{{ style_block }}
The {{ object }} rests on the pedestal. Slow 20-degree orbit around it, ending on a three-quarter hero angle.
Highlight {{ hero_feature }}.
```

**Cutaway**
```
{{ style_block }}
The outer casing of the {{ object }} separates along a clean cutaway plane, revealing {{ internals }}.
Camera pushes in slowly toward {{ focus_part }}. Cut surfaces are smooth brushed metal.
```

**Flow**
```
{{ style_block }}
Inside the cutaway {{ object }}, glowing {{ energy_color_name }} energy ({{ energy_hex }}) flows through {{ path }},
showing {{ process }}. The motion is smooth and readable, not chaotic.
```

**In situ**
```
{{ style_block | replace("Deep graphite studio background (#0E1116), matte graphite pedestal.", "") }}
The {{ object }} is installed in {{ context }}, lit by moody cyan and amber accent lights.
Slow push-in from a wide view to the part. Realistic but clean environment, no visible brands.
```

### 10.3 Still-image template (`still_image.j2`), used for animated stills

```
{{ style_block }}
Still frame: {{ description }}. Composition leaves the area {{ safe_area_hint }} clear for labels.
```

**Stills are optional for the owner (D-067):** the Flow sheet lists a ready prompt per `still` shot. If `still_NN.png` (or .jpg/.webp) is in the Flow inbox it is used; otherwise the factory takes the sharpest of 8 frames from a clip of the same beat (else the nearest shot). Each still becomes `stills/still_NN_anim.mp4`: a 1080×1920, 30 fps Ken Burns move (zoom 1.00↔1.08 with a slight pan, eased, alternating per shot). Parallax would need a depth model and is not done.

### 10.4 Episode opener (16:9) (`flow_episode_opener.j2`)

```
{{ style_block | replace("vertical", "horizontal 16:9") }}
Wide establishing shot of {{ theme_scene }}, slow dolly in, cinematic and calm.
```

### 10.5 Prompt rules

- Free-tier credit plan: final clips use **Veo 3.1 Fast**, about 2 per day. Use Lite only to test prompts.
- Each shot is about 8 s. Plan 3–5 Flow shots per video; animated stills and diagrams fill the rest.
- Never ask for real brand names, logos, real people or real identifiable locations.
- Record the exact prompt text for every generated asset in the `assets.prompt` field.
