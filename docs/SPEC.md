# Reelflow YAML Specification — v1

This document is the authoritative reference for the Reelflow pipeline file
format (`*.yaml`). It doubles as the manual: every top-level key, every built-in
block, and every shared field is described here.

> **Status:** v1 draft. The format follows semantic versioning via the
> `reelflow:` key — a pipeline declaring `reelflow: "1.0"` will keep running on
> any `1.x` engine.

---

## 1. File anatomy

A Reelflow pipeline is a single YAML file with the following top-level keys:

```yaml
reelflow: "1.0"          # required — spec version
name: my-pipeline        # required — pipeline identifier
description: "..."        # optional — human-readable summary

vars:                    # optional — reusable values
  topic: deepseek

input:                   # required — the source media
  type: video
  source: ./episode.mp4

matrix:                  # optional — fan-out over combinations
  lang: [fr, en]

steps:                   # required — ordered list of steps
  - stt: { ... }
  - export: { ... }

output:                  # optional — global output settings
  dir: ./output
```

| Key | Required | Description |
|---|---|---|
| `reelflow` | ✅ | Spec version string (`"1.0"`). |
| `name` | ✅ | Pipeline name. Used for logs and output naming. |
| `description` | ❌ | Free text. |
| `vars` | ❌ | Key/value map of reusable values (see §3). |
| `input` | ✅ | The source media (see §4). |
| `matrix` | ❌ | Fan-out combinations (see §9). |
| `steps` | ✅ | Ordered list of steps (see §5). |
| `output` | ❌ | Global output settings (see §10). |

---

## 2. Versioning

The `reelflow` key pins the spec version the pipeline was written against.

```yaml
reelflow: "1.0"
```

- The engine refuses to run a pipeline whose major version it does not support.
- Unknown keys under a known version are a **validation error** (`reelflow
  validate` fails) — this keeps shared pipelines portable.

---

## 3. Variables & templating

`vars` defines reusable values. They are referenced anywhere in the file with
double-brace templating:

```yaml
vars:
  topic: deepseek
  lang: fr

steps:
  - stt:
      lang: "{{ vars.lang }}"
```

### Reference namespaces

| Namespace | Resolves to | Example |
|---|---|---|
| `vars.*` | a value from the `vars` map | `{{ vars.topic }}` |
| `input.*` | a field of the input block | `{{ input.source }}` |
| `steps.<id>.*` | a named output of a prior step | `{{ steps.transcript.text }}` |
| `matrix.*` | the current matrix cell (see §9) | `{{ matrix.lang }}` |

Templating is resolved **lazily**, at the moment a step runs — so a step can
reference the output of any step that precedes it in the DAG.

CLI overrides (`--var topic=sora`) take precedence over the `vars` block.

---

## 4. Input

`input` declares the single source media for the pipeline.

```yaml
input:
  type: video
  source: ./episode.mp4
```

| Field | Required | Values | Description |
|---|---|---|---|
| `type` | ✅ | `video` | Media kind. **v1 supports `video` only.** |
| `source` | ✅ | path | Path to the source file. **v1 supports `.mp4` only.** |

> Out of scope for v1: audio-only input, URLs (YouTube), RSS, multiple inputs.

---

## 5. Steps

`steps` is an **ordered list**. Each item is a single-key map: the key is the
block name, the value is the block's parameters.

```yaml
steps:
  - stt:
      model: base
      lang: fr
  - export:
      format: vertical
```

### 5.1 Common fields

Every step accepts these fields alongside its block-specific params:

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | block name | Identifier for referencing this step's outputs. Required if two steps use the same block. |
| `cache` | bool | `true` | Skip the step if its output already exists (checkpoint). |
| `on_failure` | enum | `abort` | `abort` \| `skip` \| `retry`. |
| `retries` | int | `0` | Number of retries when `on_failure: retry`. |
| `requires` | string | — | Gate the step on another step's state, e.g. `transcript.success`. |

```yaml
steps:
  - id: transcript
    stt:
      model: base
    cache: true
    on_failure: retry
    retries: 2
```

### 5.2 Step states

At runtime each step moves through:

```
pending → running → success
                  ↘ failed
                  ↘ skipped   (on_failure: skip, or requires unmet)
```

A **cached** step reports as `success` (its earlier result is reused, only the
recompute is skipped), so it still satisfies a downstream
`requires: <id>.success`. `skipped` is reserved for steps that did *not* produce
a result this run (`on_failure: skip`, or an unmet `requires`).

---

## 6. Built-in blocks

The six blocks shipped in v1. All run locally — no API key required.

### 6.1 `stt` — speech-to-text

Transcribes the input (or a referenced media) to timed text.

```yaml
- id: transcript
  stt:
    model: base          # tiny | base | small | medium | large
    lang: fr             # ISO code, or "auto"
    input: "{{ input.source }}"
```

| Param | Type | Default | Description |
|---|---|---|---|
| `model` | enum | `base` | Whisper model size (via `faster-whisper`). Larger = more accurate, slower. |
| `lang` | string | `auto` | Language code or `auto` to detect. A wrong code garbles the transcript. |
| `vad_filter` | bool | `true` | Drop non-speech (silence, crowd, music) before transcribing — removes most hallucinated text. |
| `beam_size` | int | `5` | Beam search width; higher = more accurate, slower. |
| `input` | path | pipeline input | Media to transcribe. |

**Outputs:** `text` (full transcript), `segments` (list of `{start, end, text}`),
`lang` (detected language).

---

### 6.2 `tts` — text-to-speech

Synthesizes speech from text.

```yaml
- id: voice
  tts:
    text: "{{ vars.script }}"
    voice: default
    speed: 1.0
```

| Param | Type | Default | Description |
|---|---|---|---|
| `text` | string | — | Text to speak (required). |
| `voice` | string | `default` | Voice name (via `kokoro-onnx`). |
| `speed` | float | `1.0` | Playback speed multiplier. |

**Outputs:** `audio` (path to generated audio file), `duration` (seconds).

---

### 6.3 `detect_clips` — find the strong moments

Analyzes a long video and emits candidate clips as a **channel** (see §8).

```yaml
- id: clips
  detect_clips:
    method: silence       # silence | scene_change | loudness
    min_duration: 30s
    max_duration: 60s
    max_clips: 5
    emit: clip_channel
```

| Param | Type | Default | Description |
|---|---|---|---|
| `method` | enum | `silence` | `silence` \| `scene_change` \| `loudness`. |
| `min_duration` | duration | `15s` | Minimum clip length. |
| `max_duration` | duration | `60s` | Maximum clip length. |
| `max_clips` | int | `5` | Cap on number of clips emitted. |
| `emit` | string | — | Channel name to emit clips into. |

**Methods.** `silence` keeps the spoken spans (best for talking-head / podcast).
`scene_change` splits on camera cuts. `loudness` ranks 1-second windows by audio
level (via `astats`) and keeps the loudest, non-overlapping moments — the best
local proxy for action highlights (crowd roar, commentator excitement) in sports
footage. For `loudness`, each clip's boundaries are found **automatically** by
expanding around the peak while the level stays high, so the build-up and the
sustained reaction are both captured; `min_duration`/`max_duration` only bound
the resulting length (no manual offset).

**Outputs:** `count`, `timestamps` (list of `{start, end}`), plus the named
channel.

> Out of scope for v1: `method: engagement` (LLM-scored). Reserved keyword.

---

### 6.4 `cut` — extract a segment

Cuts a segment from a video. Operates on a single time range, or maps over a
channel.

```yaml
# single segment
- cut:
    start: 00:01:10
    end: 00:01:55

# map over a channel — runs once per clip, in parallel
- cut:
    from: clip_channel
```

| Param | Type | Default | Description |
|---|---|---|---|
| `start` | timecode | — | Start time (`HH:MM:SS` or seconds). |
| `end` | timecode | — | End time. |
| `from` | channel | — | Channel to map over (mutually exclusive with `start`/`end`). |
| `input` | path | pipeline input | Source video. |

**Outputs:** `clips` (list of paths), or `clip` (single path) when not mapping.

---

### 6.5 `captions` — burned-in subtitles

Generates and renders subtitles onto a video.

```yaml
- captions:
    from: clip_channel
    segments: "{{ steps.transcript.segments }}"
    style: tiktok
    burn: true
```

| Param | Type | Default | Description |
|---|---|---|---|
| `segments` | ref | — | Timed segments (usually `steps.<stt>.segments`). |
| `from` | channel | — | Channel of clips to caption. |
| `style` | enum | `default` | `default` \| `tiktok` \| `minimal`. |
| `burn` | bool | `true` | `true` burns into video; `false` writes a sidecar `.srt`. |
| `position` | enum | `bottom` | `top` \| `center` \| `bottom`. |
| `max_chars` | int | `42` | Max characters per subtitle cue; longer segments are split so the text never fills the frame. |

**Outputs:** `clips` (captioned paths) or `srt` (sidecar path when `burn: false`).

---

### 6.6 `export` — final render

Renders the final video(s) to disk.

```yaml
- export:
    from: clip_channel
    format: vertical
    resolution: 1080x1920
    output: "./output/{{ name }}-{{ index }}.mp4"
```

| Param | Type | Default | Description |
|---|---|---|---|
| `format` | enum | `vertical` | `vertical` (9:16) \| `horizontal` (16:9) \| `square` (1:1). |
| `resolution` | string | per-format | e.g. `1080x1920`. |
| `from` | channel | — | Channel to export (one file per item). |
| `fps` | int | `30` | Frames per second. |
| `title` | string | — | Persistent title banner at the top of the frame, for the whole clip. `\n` splits lines. |
| `title_size` | int | `34` | Title font size, in pixels of the export resolution. |
| `title_margin` | int | `120` | Title distance from the top edge (into the letterbox band). |
| `title_font` | string | `font1` | Title font: a preset `font1`–`font5`, or any installed family name (e.g. `Impact`). |
| `output` | path | `output.dir` | Output path; supports `{{ index }}` and `{{ name }}` when mapping a channel. |

**Title tokens.** Inside `title`, `output` and overlays you can use `{{ part }}`
(1-based clip number, e.g. `#1`, `#2`), `{{ index }}` (0-based) and `{{ name }}`
(pipeline name).

**Title fonts.** The presets are bundled-by-download: on first use the (OFL)
font is fetched to `~/.reelflow/fonts/` and given to libass via `fontsdir`, so
they render **identically on every machine, no system install**:
`font1`=Anton, `font2`=Bebas Neue, `font3`=Bangers, `font4`=Archivo Black,
`font5`=Fjalla One. You can also pass any installed family name directly, or drop
your own `.ttf` in `~/.reelflow/fonts/` and reference it by family. A custom name
that can't be found is substituted by libass — Reelflow prints a warning when
that happens.

**Outputs:** `files` (list of written paths).

---

### 6.7 `concat` — stitch clips into one video

Joins a channel's clips, in order, into a single file. A channel *aggregator*:
unlike mapped consumers it receives the whole channel at once. Place it after
`export` to assemble the rendered clips into a final reel.

```yaml
- concat:
    from: clip_channel
    output: "./output/{{ name }}-reel.mp4"
```

| Param | Type | Default | Description |
|---|---|---|---|
| `from` | channel | — | Channel whose clips are concatenated (required). |
| `output` | path | `<name>-reel.mp4` | Output path; supports `{{ name }}`. |

**Outputs:** `file` (the joined video), `parts` (the source clips, in order).

---

## 7. Common output namespaces

Quick reference of what each block exposes for `{{ steps.<id>.* }}`:

| Block | Outputs |
|---|---|
| `stt` | `text`, `segments`, `lang` |
| `tts` | `audio`, `duration` |
| `detect_clips` | `count`, `timestamps`, + channel |
| `cut` | `clips` / `clip` |
| `captions` | `clips` / `srt` |
| `concat` | `file`, `parts` |
| `export` | `files` |

---

## 8. Channels

A channel is a **stream of items** flowing between steps. A producer declares
`emit: <name>`; a consumer declares `from: <name>`. The consumer runs **once per
item, in parallel**, as soon as each item is available — no barrier.

```yaml
steps:
  - id: clips
    detect_clips:
      max_clips: 5
      emit: clip_channel        # produces N clips

  - captions:
      from: clip_channel        # runs N times, one per clip, in parallel

  - export:
      from: clip_channel
```

This is what lets one source video fan out into N captioned, exported clips
without writing a loop.

---

## 9. Matrix

`matrix` fans the **entire pipeline** out over every combination of the listed
values. Each combination is an independent run; reference the current cell with
`{{ matrix.* }}`.

```yaml
matrix:
  lang: [fr, en]
  format: [vertical, square]

steps:
  - stt:
      lang: "{{ matrix.lang }}"
  - export:
      format: "{{ matrix.format }}"
```

The example above produces **4 runs** (`fr×vertical`, `fr×square`,
`en×vertical`, `en×square`).

> Channels (§8) parallelize *within* a run; matrix parallelizes *across* runs.

---

## 10. Output

Global output settings. Individual `export` steps can override `output` per file.

```yaml
output:
  dir: ./output          # default: ./output
  cleanup: true          # delete temp files after a successful run
```

| Field | Default | Description |
|---|---|---|
| `dir` | `./output` | Base directory for all produced files. |
| `cleanup` | `false` | When `true`, delete `output/.reelflow/` (intermediate clips, `.srt`/`.ass`, cache) after a successful run — handy when a `concat` step has already assembled the final reel. The CLI `--clean` flag forces this regardless of the setting. |

---

## 11. Reserved & out of scope for v1

To keep shared pipelines forward-compatible, these keywords are **reserved** but
not implemented in v1 — using them is a validation error:

- `input.type: audio | text | url | rss`
- `detect_clips.method: engagement`
- cloud providers (`engine: elevenlabs`, `model: deepgram`, …)
- `use:` (composing community pipelines from the hub)
- `hooks:` (lifecycle callbacks)
- `music:` block
- custom/third-party blocks

---

## 12. Full example

A complete, valid v1 pipeline: long podcast → 5 captioned vertical clips.

```yaml
reelflow: "1.0"
name: podcast-to-clips
description: "Turn a long podcast into short captioned clips"

vars:
  lang: fr

input:
  type: video
  source: ./episode.mp4

steps:
  - id: transcript
    stt:
      model: base
      lang: "{{ vars.lang }}"
    cache: true

  - id: clips
    detect_clips:
      method: silence
      min_duration: 30s
      max_duration: 60s
      max_clips: 5
      emit: clip_channel

  - cut:
      from: clip_channel

  - captions:
      from: clip_channel
      segments: "{{ steps.transcript.segments }}"
      style: tiktok
      burn: true

  - export:
      from: clip_channel
      format: vertical
      resolution: 1080x1920

output:
  dir: ./output
```

---

## 13. Running pipelines

`reelflow run pipeline.yaml` validates the file, then executes it. The engine:

- builds a **DAG** from the steps (channel wiring + `{{ steps.* }}` references
  define the edges) and runs it in dependency order;
- fans **channels** out in parallel (one run per item) and **matrix** out across
  cells (§8, §9);
- honours **states**, `cache` checkpoints, and `on_failure` / `retries` (§5);
- writes produced files under `output.dir` and intermediates under
  `output.dir/.reelflow/`.

```bash
reelflow run pipeline.yaml --var lang=en   # override a vars entry (repeatable)
reelflow run pipeline.yaml --clean         # delete temp files after a successful run
```

`--clean` removes `output/.reelflow/` (intermediate clips, `.srt`/`.ass`, and the
checkpoint cache) once the run succeeds, leaving only the final media. Omit it to
keep the cache so a re-run can resume from checkpoints.

### 13.1 Time values

Durations (`min_duration`, …) and timecodes (`start`, `end`) accept:

| Form | Examples |
|---|---|
| bare seconds | `90`, `5.5` |
| compact duration | `30s`, `2m`, `1m30s` |
| timecode | `00:01:30`, `01:30` |

### 13.2 Local models

`run` needs the engine extra (`pip install "reelflow[engine]"`). Models are
fetched on first use and cached:

- **STT** (`faster-whisper`) — cached by the library (HuggingFace cache).
- **TTS** (`kokoro-onnx`) — model + voices downloaded to `~/.reelflow/models/`
  (override the base dir with the `REELFLOW_HOME` environment variable).
- **Title fonts** (`export.title_font` presets) — downloaded to
  `~/.reelflow/fonts/` and used by libass, so titles look the same everywhere.

FFmpeg is used for all media work: a system `ffmpeg` on `PATH` is preferred,
otherwise the static binary bundled with `imageio-ffmpeg` is used.
