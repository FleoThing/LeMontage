# Architecture

LeMontage is a local-first pipeline engine. A YAML document is validated, converted
to a DAG, then executed step by step against local media files.

## High-Level Flow

```text
pipeline.yaml
    |
    v
YAML parser
    |
    v
validator.py
    |
    v
dag.py
    |
    v
executor.py
    |
    v
built-in blocks
    |
    v
output files
```

## Main Modules

| Module | Responsibility |
|---|---|
| `src/lemontage/cli.py` | CLI entrypoint for `init`, `validate` and `run`. |
| `src/lemontage/validator.py` | Validates parsed YAML against the supported v1 shape. |
| `src/lemontage/spec.py` | Shared constants for supported blocks, fields and reserved names. |
| `src/lemontage/engine/dag.py` | Builds a stable topological order from steps, templates and channels. |
| `src/lemontage/engine/executor.py` | Runs steps, handles states, cache, matrix cells, channels and failures. |
| `src/lemontage/engine/template.py` | Resolves `{{ vars.* }}`, `{{ steps.* }}` and related references. |
| `src/lemontage/engine/context.py` | Stores mutable run state for one matrix cell. |
| `src/lemontage/engine/blocks/` | Built-in media blocks. |
| `src/lemontage/engine/providers/` | Provider interfaces and local Whisper implementation. |

## Pipeline Validation

Validation happens before execution. The validator checks:

- required top-level keys,
- supported spec version,
- MP4 input shape,
- known block names,
- common step fields such as `on_failure` and `retries`,
- channel references created through `emit` and consumed through `from`.

Validation returns a list of human-readable errors. An empty list means the file is
valid enough to execute.

## DAG Construction

Each YAML step becomes a `Node`. Edges are inferred from:

- template references such as `{{ steps.transcript.words }}`,
- channel producers using `emit`,
- channel consumers using `from`,
- listed order for consumers of the same channel.

The DAG builder returns a deterministic topological order and raises an error for
unknown references or cycles.

## Execution Model

The executor creates one `RunContext` per matrix cell. Each context contains:

- user variables,
- input metadata,
- matrix values,
- output directory,
- step outputs,
- emitted channels,
- step states.

Step states follow:

```text
pending -> running -> success | failed | skipped
```

Failure behavior is controlled by `on_failure`:

- `abort`: stop the current matrix cell,
- `skip`: mark the step skipped and continue where possible,
- `retry`: retry according to `retries`.

## Blocks And Channels

Blocks implement the contract in `engine/blocks/base.py`.

Execution modes:

- **single**: the block runs once and may emit a channel.
- **mapped**: a `from: <channel>` block runs once per channel item in parallel.
- **aggregator**: a block consumes the whole channel at once, such as concat.

Channel item outputs are merged back into each item so later mapped blocks can use
the enriched data.

## Cache And Outputs

Intermediate work is written under:

```text
<output.dir>/.lemontage/
```

Cache signatures include the step, its resolved parameters, the input source and
the signatures of its upstream steps, so a param change invalidates the step and
everything downstream of it. On cache hit, the step is marked as `success` so
downstream `requires` gates continue to work.

The CLI `--clean` flag removes temporary files after a successful run.

## Local Media Stack

LeMontage uses:

- FFmpeg through `imageio-ffmpeg` for media operations,
- local Whisper through `faster-whisper` for speech-to-text,
- generated or installed fonts for captions and titles.

The first run can download model and font assets into local caches.
