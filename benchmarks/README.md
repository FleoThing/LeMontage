# Benchmarks

Control pipelines for the concurrency work. They exist to answer two questions
about a perf change, and nothing else:

1. Is it faster? Wall clock, before and after, on the same pipeline.
2. Does it still render the same thing? File for file.

A perf change without both answers is a claim, not a result.

## The two controls

| Pipeline | What it exercises |
|---|---|
| `channel.yaml` | The per-item fan-out: 16 clips through `cut` then `export`, 32 mapped ffmpeg runs. |
| `matrix.yaml` | Matrix cells: 4 cells of 4 clips each. |

Both avoid `stt` on purpose. Whisper dominates the wall clock on any real source
and would hide everything else, so the clip boundaries are hard-coded
(`detect_clips: method: agent`) and nothing is transcribed.

Neither is meant to look good. No captions, no `smart_crop`, no filter: a knob
that is not being measured only adds variance.

## Running them

The default source is `./media/lotr.mp4`, which is not in the repo (media is
gitignored). Point them at any video longer than 240 seconds:

```bash
python3 scripts/bench.py benchmarks/channel.yaml --var source=path/to/video.mp4
```

`scripts/bench.py` removes the pipeline's output directory before each run, so
every run starts from a cold cache. Without that, run 2 is a series of cache hits
and measures nothing.

## Before and after

```bash
git switch main
python3 scripts/bench.py benchmarks/channel.yaml --save /tmp/before.json

git switch perf/my-change
python3 scripts/bench.py benchmarks/channel.yaml --save /tmp/after.json

python3 scripts/bench.py --compare /tmp/before.json /tmp/after.json
```

The comparison prints the median delta and the per-file verdict. It exits
non-zero when any rendered file changed, which is the point: a pure perf change
must not touch the output.

## Reading the number

Quote the **median**, not the minimum (it flatters a warm page cache) and not the
mean (it follows whatever else the machine was doing).

Every measurement starts with one discarded warm-up run. It is not politeness:
without it this harness disagreed with itself by 6% on unchanged code, because
the first batch reads the source from disk and every later one from the OS page
cache. Six percent is wider than most of the gains being measured here.

`--compare` prints the run-to-run spread alongside the delta and says so when the
delta is inside it. A change that does not clear its own noise floor has not been
measured yet; give it more `--runs` or a quieter machine.

The numbers are only comparable to each other on the same machine, with the same
source and the same ffmpeg. There is no absolute score here.

### One thing the fingerprint cannot absorb

Byte-identical output holds as long as the change does not alter what ffmpeg is
asked to do. Changing how many workers run encodes in parallel is invisible to
any single encode, so it stays byte-identical. Changing an encoder's own thread
count is not: x264 partitions frames by thread count, so the same source encoded
with `-threads 1` and `-threads 4` produces different bytes for the same picture.
A change of that kind needs its own argument, not this fingerprint.
