# Design note — image folder input (slideshow / photo montage)

> **Status:** design only, not implemented. Branch `feat/image-input`.
> Captures what to build later so we don't lose the context.

## Goal / use case

Feed a **folder of photos** instead of a single video and build an edited montage
(one clip per image, with the existing transitions/titles/fit/mute):

```yaml
input:
  type: images
  source: ./mon_dossier/       # a directory (or glob) of images

steps:
  - stills:                    # NEW: emit one channel item per image
      duration: 2s
      emit: shots
  - export:
      from: shots
      format: vertical
      fit: cover
  - concat:
      from: shots
      transitions: [fade, wipeleft, ...]
      output: "./output/slideshow.mp4"
```

## Is it possible today?

**No.** Verified:
- `spec.SUPPORTED_INPUT_TYPES = {"video"}` → `type: images` fails validation
  ("unknown input.type 'images'").
- `spec.SUPPORTED_INPUT_EXTENSIONS = (".mp4",)` → a directory `source` is rejected
  ("input.source must be a .mp4 file").
- No block turns an image into a video clip; `detect_clips` (loudness / silence /
  scene_change) assumes a video with an audio/visual timeline.

## What to build

### 1. Accept the new input type
- `spec.SUPPORTED_INPUT_TYPES` += `"images"`.
- `validator._check_input`: when `type: images`, `source` is a **directory** (or a
  glob), not a `.mp4`. Validate it's a string; existence is a runtime concern.
- Decide accepted extensions: `.jpg/.jpeg/.png/.webp` (add `SUPPORTED_IMAGE_EXTENSIONS`).

### 2. Turn images into a channel (new block, e.g. `stills`)
A producer block (like `detect_clips` is for video) that lists the folder,
**sorts** the images (natural sort so `img2 < img10`), and emits one channel item
per image:
```
{index, image: <path>, duration: <sec>}
```
Params: `duration` (per image, default e.g. 3s), maybe `sort` (name|mtime),
`shuffle` + `seed` (reuse the random idea), `max` (cap count).

### 3. Turn an image item into a video clip
Images are static → each must become a short clip so `concat`/transitions work
(transitions/xfade operate on video streams). Two options:
- **Extend `export`** to accept an image item (`item["image"]`) and render a clip
  of `duration` via `ffmpeg -loop 1 -t <dur> -i img -r <fps> ... -c:v libx264`.
- **New `still` block** that does image→clip, then `export` handles format/title.

Recommended: a dedicated **`still`** (or fold into `cut`, which currently extracts
video segments — semantically close: "produce a clip from a source"). Keep the
audio question in mind (see below).

### 4. Reuse what already exists
Once each image is a proper video clip, the current blocks work unchanged:
`export` (fit/cover, title, mute), `concat` (transitions). Ken Burns (slow zoom/pan)
is a nice-to-have on top (a `zoompan` filter) — optional, later.

## Semantics to pin down

- **Ordering**: deterministic natural sort by filename by default; `shuffle` opt-in.
- **Audio**: stills have no audio. `concat` with `acrossfade` expects an audio track.
  Either add a silent audio track when rendering a still (like `mute` does with
  `volume=0`, but here `anullsrc`), or make `concat` tolerate video-only clips.
  **This is the main gotcha** — decide up front.
- **Duration**: per-image `duration`; allow a global default and per-item override.
- **Mixed media** (images + a video): out of scope for the first cut.
- **Resolution**: images vary wildly → `fit: cover`/`contain` already handles it;
  `cover` + `trim_bars` is irrelevant for stills (no bars) but harmless.

## Touch points

- `spec.py`: `SUPPORTED_INPUT_TYPES`, new `SUPPORTED_IMAGE_EXTENSIONS`.
- `validator.py`: `_check_input` branch for `type: images` (directory source).
- `engine/blocks/`: new `stills` producer block + registry entry; image→clip
  rendering (new `still` block or extend `export`/`cut`).
- `ffmpeg.py`: helper for `-loop 1 -t D -i image` (+ silent audio track).
- `docs/SPEC.md`: document the `images` input type and the new block(s).
- Tests: stills listing/sorting/shuffle, image→clip ffmpeg args, end-to-end folder
  → slideshow with transitions.

## Out of scope (first cut)

- Ken Burns / pan-zoom effects (add later via `zoompan`).
- Mixing images and videos in one pipeline.
- Remote image sources (URLs).
