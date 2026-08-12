# Changelog

All notable changes to LeMontage are tracked here.

This project follows SemVer-style versioning while it is pre-1.0: minor versions
may still introduce breaking changes, and those changes must be called out here.

## [Unreleased]

### Added

- **A benchmark harness for the concurrency work**: two control pipelines in
  `benchmarks/` and `scripts/bench.py`. It times cold-cache runs and hashes every
  rendered file, so a perf change can show both that it is faster and that the
  output did not move. It also reports the run-to-run spread and refuses to call
  a delta inside that spread a gain — measured at 5% on the channel control,
  which is wider than several of the gains being chased.

### Changed

- **Matrix cells render concurrently**, when it is safe to do so: 34.58s → 30.01s
  on the four-cell control (-17.9%, faster in 8 rounds out of 8, byte-identical
  output). "Safe" is checked, not assumed — `export`, `concat` and `music` build
  their default file name from the pipeline name, the step id and the clip index,
  none of which vary by cell, so cells that would overwrite each other still run
  one after another and the run says so. Give those steps an `output:` containing
  `{{ matrix.<key> }}` and they run at once. See SPEC §9.1.
  Each cell now also gets its own scratch directory under `.lemontage/work/`;
  pipelines without a `matrix` keep the old path, so no checkpoint is invalidated.
- **`lemontage analyze` is 18% faster**: its three ffmpeg passes (scene cuts,
  loudness, silence) are independent full reads of the same file and now run at
  once instead of one after another. On a 300s 1080p source, 44.18s → 36.38s, with
  a byte-identical manifest. The overlap is complete — what is left is the
  scene-detection pass alone, which is the expensive read. Transcription is
  untouched: it is not an ffmpeg pass and already uses what it is given.
- **The channel worker pool grows with the machine**, instead of stopping at 8.
  It is now `min(clips, max(8, os.cpu_count()))`, so a 32-core box no longer
  renders 16 clips 8 at a time; on 8 cores or fewer nothing changes at all. The
  new `LEMONTAGE_WORKERS` environment variable overrides it. Measured on the
  channel control (16 clips, 4 cores): 38.30s at 2 workers, 36.14s at 4, 35.72s
  at 8, 35.45s at 16 — so lowering the pool to the core count, which was the
  original plan, is 1.2% slower rather than faster.
- **The validator dispatches per block instead of running one 180-line `if`
  chain.** `_check_block_params` was complexity 55 for a threshold of 12; each
  block now has its own `_check_<block>_params` behind a `_BLOCK_CHECKS` table.
  No pipeline validates differently: the two implementations were diffed over
  101 282 generated documents plus every pipeline in the repo and return the
  same errors, in the same order. `captions` gained the param tests it never
  had, and two tests now pin the dispatch table itself.

## [0.8.0] - 2026-08-11

### Changed

- **Captions are CAPITALS by default.** `uppercase` now defaults to `true`: it
  is the register short-form captions are set in, and every pipeline in the repo
  was asking for it by hand. A pipeline that says nothing therefore renders in
  capitals where it used to keep the transcript's casing. `uppercase: false`
  restores that casing, `case: lower` forces lowercase.
- **Face detection is now YuNet via OpenCV, and `mediapipe` is gone** from the
  `[smartcrop]` extra (−36 MB, one dependency instead of two). The mediapipe code
  path was in fact dead: it needed `mp.solutions`, which mediapipe removed in
  0.10.x — inside the `>=0.10,<1.0` range this project pinned. So `smart_crop`
  silently fell back to a centre crop, and the `still` zooms to the edge-energy
  grid, on a normal install. YuNet ships as vendored weights (227 KB, MIT) and
  reads painted faces: 6/6 on a set of Napoleon portraits at ≥0.87, where the
  grid had been aiming at the torso (y≈0.31-0.56) instead of the heads
  (y≈0.13-0.29). `smart_crop` now needs OpenCV ≥ 4.5.4 and says so if it is
  older, instead of quietly doing nothing.
- `still` zooms aim at the subject in a **straight line**: the window centre
  travels to the focal point in step with the zoom's easing, instead of being
  pinned on it at every zoom level — which made the frame wander sideways
  mid-push (the `zoomin` "detour"), because at zoom 1.0 the window is the whole
  image and the clamp drags it. `zoomout` therefore starts *on* the subject and
  pulls back.
- `still` zoom easing is now **cubic** rather than quadratic, both directions: the
  move spends its speed early and coasts into the landing, which is the "smooth
  zoom" braking a CapCut Ease Out gives. No overshoot.

### Added

- **Caption styles are presets now**: `style1`-`style4`, each also reachable by
  the name of its animation (`karaoke`, `bounce`, `zoom`, `none`). `style` used
  to set the outline and the weight and nothing else, so the mode had to be
  assembled by hand out of `pop`, `pop_on` and the colours, and reading a
  pipeline meant reading the spec. A preset only fills in what the pipeline
  leaves unset, so a parameter written by hand still wins, and it never sets
  anything measured in pixels of the final frame. `tiktok`, `default` and
  `minimal` keep their old look-only meaning.
- `captions.pop_on: line`: the pop lands on the **phrase** instead of the active
  word. The whole line punches in and settles back every time it changes, with
  no highlight and no per-word event, which is the beat short-form captions are
  cut to when they are read as rhythm rather than followed word by word.
  `pop_on: word` (the default) is the existing behaviour.
- `captions.case: upper | lower`: captions could be forced to CAPITALS
  (`uppercase: true`) and nothing else, so all-lowercase captions were simply
  out of reach. `uppercase` stays as the older spelling of `case: upper`.
- `captions.pop_duration`: how long the pop takes to settle back, previously a
  fixed 90 ms in the engine. The scale said how far the text moved and nothing
  said how hard it landed, which is the other half of the beat.
- `captions.max_words`: cap the words per line, previously a fixed 5. `3` gives
  the 2-3 word phrasing `pop_on: line` needs, without having to guess a
  `max_chars` that happens to break there.
- `captions.color` and `captions.outline`: caption text colour (white by
  default) and black contour thickness. Both `color` and `highlight` now take a
  colour name or `#RRGGBB`, like every other block, alongside the raw ASS
  `&HBBGGRR` form — which is now checked before it reaches the subtitle file
  instead of being pasted in as-is.
- Text placement: `overlay.position` and `export.title_position` now reach all
  **nine** frame anchors (`top-left`, `bottom-right`, …), with `margin_x` for
  the distance to the side edge. Text was locked to the centre column, so a
  flush-left ranking column or a score in a corner could not be written at all —
  it had to be drawn outside LeMontage as a PNG and dropped in with
  `overlay.image`. The bare `top`/`center`/`bottom` names keep meaning the
  centre column, so existing titles are untouched.
- `overlay.outline` / `overlay.outline_color`: a letter contour for overlay
  text. It had none, which is fine over a band or a card and unreadable over
  moving footage. Defaults to `0` — the previous look.
- `overlay` text runs take their own `size` and `font`, alongside `color`. One
  text block can now mix scales, which is what a big rank number followed by a
  small label needs.
- `overlay.cues`: several texts, each with its own window and style, rendered in
  **one** pass instead of one `overlay` step (and one re-encode) per text. A
  five-step reveal used to stack five generations of compression on the reel.
  Cues are pinned to the point their `position` and margins describe — libass
  nudges events that would overlap, which silently collapses the spacing of a
  fixed column once the text is big enough to collide.
- `overlay.image` accepts a **list**, read by clip position like
  `export.title_color`. A clip past the end of the list gets no image, and with
  no text either it passes through without a needless re-encode.
- `analyze --packed`: a phrase-level markdown view of the transcript instead of
  the JSON manifest. Words are grouped into phrases on silences >= 0.5s, each
  line prefixed with its `[start-end]`, which reads at roughly a tenth of the
  tokens of raw `speech.words`. Phrase edges are word edges, so a range can be
  fed straight back as a `detect_clips: method: agent` span.
- `filter: grain: <0-100>` sets the strength of the `grain` look (default `12`,
  unchanged). Archive-footage edits want far more grain than a clean talking
  head, and the look was previously hard-coded.

### Fixed

- `cut` now bakes a 30ms fade in/out at every segment edge. A hard splice left a
  step discontinuity in the waveform, audible as a click at each join —
  `concat` only crossfaded when `transitions:` was set, so plain cuts (the
  common case for spoken edits) clicked. Skipped on sources with no audio track.
- `export`: `normalize_audio: true` aborted the whole export on ffmpeg 4.x
  (`Cannot select channel layout`) — `loudnorm` renegotiates the filter link when
  it flushes and the following `aresample` did not pin a layout.

### Removed

- The GitHub Pages documentation site and its `Docs site` workflow. It rendered
  the Markdown docs to HTML on every docs push, which meant a published site and
  a `github-pages` environment to keep taking down. The docs are readable as
  Markdown on GitHub and as a man page locally (`man lemontage`); the HTML copy
  was a third place for the same text to drift.

## [0.7.0] - 2026-08-10

### Added

- `zoom` block: punch in on a **video** clip — the move that carries short-form
  talking-head edits, and until now only `still` could do it (to images). `at:
  [2.4, 5]` snaps the frame closer then back out (alternating, eased over
  `duration`, `0.15s` by default); with no `at` the punch is static for the
  whole clip, and `amount` as a **list** frames each clip differently so every
  jump cut changes the shot size instead of replaying one locked-off take.
  Static punches render as `crop`+`scale`, animated ones as `zoompan` at the
  source's own frame rate. FFmpeg-only, no new dependency. See
  `examples/pipeline_zoom_punch_video.yaml`.
- `sfx` block: drop a sample at chosen times — a whoosh on a cut, a ding on the
  punchline, a riser under a reveal. `music` could only lay one continuous track
  over a finished reel, so a one-shot effect at 3.2s was impossible. `at` is
  clip-relative when the step maps a channel, so one `sfx` step puts an effect
  on every cut; `gain` sits it under the voice. The sample is decoded once and
  split per hit, and the mix never normalises — amix's default would duck the
  voice by 1/N every time an effect fired. See `examples/pipeline_sfx.yaml`.
- `captions` `pop`: the active word scales up as it is spoken and settles back
  over 90ms (`true` = 115%, or a percent). The karaoke tag can only change
  colour, so a popped line is emitted once per word instead — each event still
  draws the whole line, so the wrapping never shifts and only the active word
  changes. It is the difference between captions that read as *spoken* and
  captions that read as displayed.
- `captions` `uppercase`: draw every line in capitals — applied to the text
  itself, so the `.srt` sidecar matches and `max_chars` still counts what is
  drawn.
- `detect_clips` `silence_db` / `silence_gap`: the silence detector's two knobs
  were hard-coded at `-30dB` / `0.5s`, so the only edit it could produce was
  "drop the real pauses". `silence_gap: 0.25s` now also removes the breaths
  between sentences (the jump-cut look) and `silence_db` copes with a noisy
  room. Defaults unchanged.
- `export` `normalize_audio`: bring each clip to the streaming loudness target
  (EBU R128, -14 LUFS, one pass) so clips cut from different parts of a
  recording sit at the same level instead of the reel jumping at every join.
- `examples/pipeline_hormozi.yaml`: the short-form clipping pipeline end to end
  — transcript, silence-dropped jump cuts, subject-following vertical reframe,
  light grade, karaoke captions burned on the final frame, one reel out.
- `make audit`: a release-time static-analysis pass. Runs the ruff rule sets the
  project does not gate on (security, complexity, smells, dead code) as advisory
  output, then `scripts/audit_blocks.py`, which fails when a block is registered
  without a SPEC section, missing from the man page, or when the SPEC §6 section
  numbers no longer run in order. `make check` stays the CI-equivalent gate.

### Fixed

- A negative time no longer renders as a valid-looking ASS timestamp. `export`
  formatted `title_start: -0.5` as `-1:59:59.50` — libass reads that as two
  hours in, so the title silently never appeared instead of erroring. The three
  copies of the formatter (`captions`, `export`, `overlay`) are now one shared
  `assformat.timestamp`, which clamps to zero like `captions` already did.

## [0.6.2] - 2026-08-10

### Fixed

- Mapped blocks placed **after** `export` (`filter`, `speed`, `reverse`,
  `overlay`) worked on the pre-export cut clip and wrote the result to a key
  nothing downstream reads — the grade, the retime or the overlay was silently
  thrown away, since `captions` and `concat` both prefer the exported `file`.
  They now all read (and write back) the latest clip in the chain, like
  `captions` already did.
- The step cache keyed the input source on its **path** only, so overwriting a
  file with new content (re-cutting an excerpt to the same name) replayed the
  previous run's transcript and clips. The key now includes the source's size
  and mtime.
- Re-pin `mediapipe<1.0` in the `[smartcrop]` extra. An automated dependency
  bump (#86) moved it to `>=1,<1.1` — the very version that removed the
  `mediapipe.solutions` API `export: smart_crop` is built on, so a fresh
  `pip install 'lemontage[smartcrop]'` produced an install whose first
  `smart_crop` run failed. Renovate is now told to hold mediapipe below 1.0;
  the cap lifts with the port to the Tasks API, not before.

## [0.6.1] - 2026-08-10

### Fixed

- `export` `smart_crop` framing no longer breathes. The window used to be
  re-positioned at every sample (4×/s, stepwise via `sendcmd`) and to lock onto
  the *largest* face, so a two-shot made it ping-pong between speakers and a
  talking head drifted continuously — the clip read as amateur. It now tracks
  one subject (the face nearest the previous one), median-filters the samples,
  **holds** while the subject stays within ~8% of the crop width, and glides in
  ~0.7s (eased) when it really has to move; a jump wider than ~30% of the crop
  reads as a camera cut and snaps. On a 30s two-speaker interview: 8 deliberate
  moves instead of ~120 steps. The trajectory is now a per-frame `crop` x
  expression, so there is no `sendcmd` script and no stepping between samples.
- `smart_crop` tracking is also ~5× faster: frames are read sequentially and
  downscaled to 640px before detection instead of seeking per sample at full
  resolution.
- `smart_crop` with mediapipe 1.x failed with `module 'mediapipe' has no
  attribute 'solutions'` (1.0 removed that API). The `[smartcrop]` extra now
  pins `mediapipe<1.0`, and an already-installed 1.x raises an error that says
  which version to install.

## [0.6.0] - 2026-08-05

### Added

- `export` `fit: stretch`: scale each axis independently so a horizontal source
  fills a vertical frame edge to edge — distorted on purpose, but nothing
  cropped and no bars, which neither `contain` nor `cover` can do. `fit` also
  takes a **list**, picking the mode per clip by position
  (`fit: [cover, cover, stretch]` stretches only the 3rd), the same convention
  as `mute`; positions past the end fall back to `contain`.
- `export` `position`: an exact `X,Y` pixel offset inside the `canvas`, on top of
  the five named anchors. The anchors can't express a fixed layout — a video
  band seated under a header card is at neither the top nor the centre — so
  `position: 0,421` now places the frame's top-left corner precisely. Both
  coordinates are integers the block formats itself, and the frame must stay
  fully inside the canvas (a runtime error otherwise).

- `overlay` `image`: composite a prepared image over a clip, transparency
  preserved — a logo, a lower-third, a whole header card. Text and bands can
  only draw glyphs and rectangles, so until now anything with real artwork in it
  had to be baked into the source. `x`/`y` place the top-left corner, and a
  negative value counts back from the right/bottom edge (`x: -40` = 40px in from
  the right) so a corner watermark needs no knowledge of the frame size. The
  existing `show` window gates the image too, and the layers compose band →
  image → text. `text` is no longer required on its own: an overlay needs a
  `text` and/or an `image`. See `examples/pipeline_overlay.yaml`.

- `overlay` coloured runs: `text` also takes a list of `{text, color}` runs, so
  a paragraph can highlight its key phrases in different colours instead of
  being one flat block. Runs are concatenated verbatim (the spaces between them
  are the ones you write) and a run without a `color` falls back to the
  overlay's. The pipeline only ever *names* a colour — it goes through the same
  strict parser as `title_color`, the block emits the renderer tags, and run
  text keeps going through the existing escaping, so an untrusted pipeline still
  cannot inject render directives. A bad colour names the run it came from.
  See `examples/pipeline_overlay.yaml`.

- `detect_clips` `method: beat`: music-synced cuts. Reads a `track`'s beats
  (librosa PLP — follows tempo drift, no fixed BPM) and tiles the source into
  clips `beats_per_clip` beats long, so the concatenated reel cuts on the beat;
  lay the same track over it with `music` for a beat-synced montage. The beat
  grid is exposed as a `beats` output for `method: agent`. Behind the optional
  `[beat]` extra (librosa). See `examples/pipeline_beatsync.yaml`.
- `export` `smart_crop`: subject-following vertical reframe. Instead of black
  bars (`contain`) or a fixed centre crop (`cover`), the crop window slides to
  keep the main face in shot (mediapipe, smoothed trajectory driven via FFmpeg
  `sendcmd`) — real landscape → 9:16 TikTok framing. Behind the optional
  `[smartcrop]` extra (mediapipe + OpenCV); falls back to a centre crop when the
  source is not wider than the target or no face is found. See
  `examples/pipeline_smartcrop.yaml`.
- `filter` block: per-clip looks. `look` applies named FFmpeg effects (`bw`,
  `vignette`, `grain`, `sharpen`) — one name or a list, in order — and `eq`
  grades colour (`brightness`/`contrast`/`saturation`/`gamma`). Works on the
  input or maps over a channel of clips. FFmpeg-only, no new dependency. See
  `examples/pipeline_filter.yaml`.

### Changed

- **CLI rebuilt on [Typer](https://typer.tiangolo.com) + [Rich](https://github.com/Textualize/rich)**
  (now core dependencies): typed sub-commands, richer `--help`, and coloured
  terminal output — per-step run status, tinted `✓`/`✗` results, and readable
  validation errors. `run --json` / `analyze` keep emitting plain JSON on stdout.

### Removed

- **Breaking:** the hand-rolled `lemontage completion <shell>` command is gone;
  shell completion now comes from Typer via `--install-completion` /
  `--show-completion` (bash, zsh, fish, PowerShell).

## [0.5.0] - 2026-07-24

### Added

- `lemontage analyze <video>`: distils a video into a compact JSON manifest (a
  Video State Object) — shots with per-shot loudness, dead-air spans, and word
  timings — so an AI agent understands the source in one cheap read instead of
  screenshotting it. `--visual` adds per-shot motion and sharpness scores
  (OpenCV, behind the optional `[analyze]` extra); `--no-transcribe` skips STT.
- `music` block: lay a music track over the final reel. `start_at` skips into
  the track, `delay` holds it back, `fade_out` fades the end, and `mix: false`
  makes the music the sole audio (drops the source track).
- `detect_clips` `method: agent` and `lemontage run --json`: the AI-agent loop —
  read the transcript from `--json`, choose spans, feed them back verbatim.
- `concat` assembly-level `transition`: one typed crossfade (`fadewhite`,
  `pixelize`, `smoothleft`, …) at channel-merge boundaries, with an optional
  absolute `at` offset.
- `export` `canvas` / `position`: place the rendered frame inside a larger
  canvas (e.g. a 1080x1080 square centred in a 1080x1920 vertical frame).
- `overlay` block: burn timed multi-line text — optionally on a full-width
  band — shown only during a `show.from`/`show.to` window.
- `AGENTS.md`: a playbook mapping a user's video goal to the features to use.

### Fixed

- Duplicate step ids are now a validation error (two steps resolving to the same
  id silently overwrote each other's cache).
- Cache keys now hash a step's params plus its upstream steps' keys, so a param
  change reruns the step and everything downstream of it.
- Two `emit` concats with no explicit output no longer collide (default output
  is keyed on the step id).

### Changed

- Roadmap reframed as a direction statement plus an idea pool; the shipped
  history now lives here in the CHANGELOG only.
- The man page is no longer rendered onto the GitHub Pages site.

## [0.4.0] - 2026-07-18

### Added

- Six new `concat` transitions: `fadeblack` (fade through black, for a marked
  scene break), `zoomin` (dynamic push, needs FFmpeg >= 5.0), `circleopen` /
  `circleclose` (spotlight iris), `dissolve` (noisy organic fade) and `radial`
  (clock-hand sweep).
- `still` motion effects: `motion: zoomout | zoomin` animates each image with
  an eased punch-out / punch-in (fast start, braking before it lands), and
  `motion: panup | pandown` is a pure vertical scroll — a full-width band
  slides across the image at constant speed, no zoom. Tuned via
  `motion_amount` (default 1.1) and `motion_duration` (default: the whole
  clip). See `examples/pipeline_zoom_punch.yaml` and
  `examples/pipeline_pan_scroll.yaml`.

- `detect_clips` `method: agent`: an AI agent reads the transcript
  (`words` from `stt`) and supplies exact `clips: [{start, end}]` itself,
  used verbatim — no heuristic. Every method now attaches spoken
  `text`/`words` to each candidate so an agent sees what is said.
- `lemontage run --json`: prints every step's outputs (notably the `stt`
  transcript with word timings) to stdout as JSON, status lines stay on
  stderr. Closes the AI-agent loop: transcribe, read the transcript,
  decide which spans are viral, feed them back through
  `detect_clips: method: agent`.
- Bigger, lower default subtitles (`caption_size` 100px, lower
  `caption_margin`); `captions` prefers the exported `file` over the cut
  `clip`, so placing it after `export` burns captions at full size on the
  reframed (e.g. vertical) clip. Adds an `output:` param so `captions`
  can be the last step.
- Parameterizable `input.source` (via `vars`/`matrix`): a single pipeline
  can take its source via `--var` instead of duplicating the file per
  video.

### Fixed

- `detect_clips` agent boundaries are used verbatim instead of snapping
  to `words:`, and are exposed via a `clips` output so the
  refine-detected-clips agent loop works end to end.
- Checkpoint signatures now include `input.source`, so two different
  input videos with identical step params no longer collide on the same
  cache entry.

- Two `export` steps in the same pipeline no longer overwrite each other's
  clips: without an explicit `output:`, a custom-id export step now writes
  `<name>-<step_id>-<index>.mp4` instead of the shared `<name>-<index>.mp4`.
  Pipelines with a single (implicit-id) export step keep the historical
  naming.

## [0.3.3] - 2026-07-08

### Fixed

- FFmpeg/ffprobe subprocess calls now redirect stdin to `/dev/null`: without
  `-nostdin`, ffmpeg put the controlling terminal in raw/no-echo mode to
  listen for keypresses and didn't reliably restore it, leaving the terminal
  unresponsive after a pipeline run finished.

## [0.3.2] - 2026-07-07

### Changed

- Default `author_size` of the export author label raised from 26 to 44 px:
  26 was barely legible once compressed by the Shorts/TikTok players.

### Security

- Preset title fonts (`font1`-`font5`) are now verified against pinned SHA-256
  digests at download time: a substituted TTF (MITM, compromised upstream) is
  rejected before ever reaching libass (audit S6, #35).


### Added

- Documentation site on GitHub Pages: the man page and the Markdown docs are
  rendered to HTML on every docs push to main — for users without `man`
  (Windows).

## [0.3.1] - 2026-07-07

### Fixed

- `captions` on a landscape source no longer lose their line ends when the clip
  is later exported vertical with `fit: cover`: lines are kept inside the centre
  9:16 column (and wrap instead of overflowing). Opt out with `safe_area: false`
  when the final export stays horizontal.

## [0.3.0] - 2026-07-07

### Added

- Image-folder input (`input.type: images`): build a slideshow / photo montage
  from a folder of `.jpg` / `.jpeg` / `.png` / `.webp` files.
- `stills` built-in block: emit a channel with one item per image of a folder
  (natural sort, optional seeded `shuffle`, `max`, per-image `duration`).
- `still` built-in block: render an image into a short video-only H.264 clip so
  `export` and `concat` (transitions) can treat it like any other clip.
- `concat` tolerates video-only clips: when a clip has no audio track, the join
  is rendered without audio instead of failing.

## [0.2.0] - 2026-07-06

### Added

- `concat` can merge several channels: `from: [viral, montage]` joins channels
  in listed order into one reel. `transitions_at: boundaries` places a single
  transition at each channel join (default `all` crossfades every gap).
- `concat` can `emit:` its reel as a channel, so branches nest: each is a
  self-contained sub-pipeline concatenating (with its own transitions) into one
  clip, and a parent `concat` joins those clips — with or without a transition.
- `export` author label: persistent corner credit for the clip's source channel
  or the editor's own handle (`author`, `author_position`, `author_size`,
  `author_margin`, `author_font`).
- `lemontage completion <shell>` command: bash, zsh and fish completion scripts.
- `concat` transitions: crossfade / wipe / slide between clips via `transitions` and `duration`.
- `reverse` built-in block: play a clip backwards (video + audio).
- `speed` built-in block: slow-motion / fast-forward by a playback factor.
- Project documentation split across README, contributing, support, security and docs files.
- Docker Compose local deployment file under `infrastructure/local/compose.yaml`.
- Documentation assets grouped under `docs/assets/`.

### Changed

- Installation scripts moved under `infrastructure/script/`.
- README reorganized around app description, tech stack and install/deploy options.
- **Breaking:** a step's `output:` path must now resolve inside the pipeline's
  output directory or the current working directory; absolute paths or `..`
  traversal that escape both are rejected.

### Security

- Confine `export` and `concat` output paths to the allowed output tree,
  preventing a shared pipeline from writing files to arbitrary locations.
- Escape ASS override syntax (`{`, `}`, `\`) in caption text (from the
  transcript) and export title text (from the pipeline), so neither can inject
  libass render directives.
- Escape single quotes and backslashes in concat-demuxer clip paths.
- Bound `export` `resolution`, `fps` and `title_size`, plus `speed` `factor`,
  to sane ranges to avoid absurd FFmpeg allocations.
- Reject empty or dotted `--var` keys instead of silently dropping them.

## [0.1.4]

### Added

- CLI commands: `init`, `validate` and `run`.
- YAML validation for the v1 pipeline format.
- Local execution engine with DAG ordering, channels, matrix runs, caching and failure handling.
- Built-in blocks for STT, clip detection, cutting, captions, export and concat.
- Local Whisper integration through `faster-whisper`.
- Dockerfile, GitHub Actions workflows, tests and quality checks.

### Notes

- MP4 input is the supported input target for the current engine.
- The media engine dependencies are installed through the optional `engine` extra.
