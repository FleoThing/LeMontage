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
| `dag.yaml` | Independent DAG steps: two branches that meet only at a final `concat`. |

All three avoid `stt` on purpose. Whisper dominates the wall clock on any real source
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

Two things in the harness exist only because the first measurements were wrong,
and both are worth knowing about before trusting a number.

**A discarded warm-up run.** Without it the harness disagreed with itself by 6%
on unchanged code: the first batch reads the source from disk, every later one
from the OS page cache.

**A settle step before each run.** A run of the channel control writes some 85 MB
and the kernel flushes it in the background *after* the process exits, so the
writeback of run N landed inside run N+1. That showed up as a slow oscillation
across a batch, and two batches of identical code came out 39.0s and 45.7s. With
`os.sync()` and a few seconds of quiet before each run, the same control measures
a 0.3% spread instead of 25%. If a batch ever spreads by more than a couple of
percent again, suspect the machine before the code: check `uptime`, and look for
`kworker/*flush*` near the top of `ps`.

`--compare` prints the run-to-run spread alongside the delta and says so when the
delta is inside it. A change that does not clear its own noise floor has not been
measured yet.

## When the machine drifts anyway

Measuring all of A and then all of B is the wrong protocol against slow drift:
whichever side ran during the slow stretch loses, and the verdict can invert
between two attempts. `--ab` interleaves them instead, A B A B, and reports the
median of the per-round paired deltas — each pair ran back to back, so whatever
drift is left subtracts out.

```bash
python3 scripts/bench.py benchmarks/channel.yaml \
    --ab LEMONTAGE_WORKERS=8 LEMONTAGE_WORKERS=4 --runs 4
```

Each side is `KEY=VALUE`, or `''` for the default environment. This only compares
things an environment variable can switch; a code change still needs two
checkouts and `--compare`.

The numbers are only comparable to each other on the same machine, with the same
source and the same ffmpeg. There is no absolute score here.

## A control can be wrong about its own subject

`dag.yaml` first used `detect_clips: method: agent`, like the other two, so every
step in a branch was a fan-out. A fan-out already spreads itself across the
worker pool and fills the machine on its own, so there was nothing idle for
concurrent branches to recover: the control measured -2.3% over 11 rounds, inside
its own noise, and said the DAG scheduler bought nothing.

It was measuring the wrong thing. A real pipeline from the repo
(`pipeline_canadair.yaml`, three branches with a beat-detection pass each) put the
same change at -6.1%, faster in 3 rounds of 3. What the scheduler recovers is the
time a **single-mode** step leaves the machine idle — and the control had none.
With a real analysis pass per branch it now reports -9.8%, faster in 4 of 4.

The lesson is not about this file. Before trusting a control that reports "no
change", check that it contains the thing the change acts on. A control that
cannot see the effect looks exactly like an effect that is not there.

### One thing the fingerprint cannot absorb

Byte-identical output holds as long as the change does not alter what ffmpeg is
asked to do. Changing how many workers run encodes in parallel is invisible to
any single encode, so it stays byte-identical. Changing an encoder's own thread
count is not: x264 partitions frames by thread count, so the same source encoded
with `-threads 1` and `-threads 4` produces different bytes for the same picture.
A change of that kind needs its own argument, not this fingerprint.
