#!/usr/bin/env python3
"""Time a control pipeline, and fingerprint what it rendered.

Exists because of the rule for v0.9.0: every perf change ships two facts, and a
perf change without them is a claim, not a result.

1. **Wall clock, before and after.** Each run starts from a cold cache -- the
   pipeline's output directory is removed first, otherwise run 2 is a series of
   cache hits and measures nothing. Several runs, and the *median* is the number
   to quote: the minimum flatters a warm page cache, the mean follows whatever
   else the machine was doing.
2. **Renders identically, file for file.** Every produced file is hashed, so
   "nothing changed in the output" is a comparison of two digests instead of an
   impression.

    # baseline, on main
    python3 scripts/bench.py benchmarks/channel.yaml --save /tmp/before.json

    # after the change
    python3 scripts/bench.py benchmarks/channel.yaml --save /tmp/after.json

    # the verdict
    python3 scripts/bench.py --compare /tmp/before.json /tmp/after.json

The intermediate files under ``.lemontage/`` are ignored by the fingerprint: they
are the engine's scratch space, not the render. Only the deliverables count.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


# -- output directory ---------------------------------------------------------


def output_dir(pipeline: Path) -> Path:
    """The pipeline's ``output.dir``, resolved and checked as safe to wipe.

    This path is read from a YAML file and then handed to ``rmtree``, so it is
    checked rather than trusted: it has to sit strictly inside the repo, and it
    cannot be the repo root itself.
    """
    doc = yaml.safe_load(pipeline.read_text(encoding="utf-8"))
    declared = ((doc.get("output") or {}).get("dir")) or "./output"
    path = (REPO / declared).resolve() if not Path(declared).is_absolute() else Path(declared)
    if path == REPO or REPO not in path.parents:
        raise SystemExit(f"refusing to manage output dir {path!r}: not strictly inside {REPO}")
    if len(path.relative_to(REPO).parts) < 2:
        raise SystemExit(
            f"refusing to manage output dir {path!r}: give the benchmark its own "
            "subdirectory (e.g. ./output/bench-channel) so a run cannot wipe "
            "another pipeline's results"
        )
    return path


# -- fingerprint --------------------------------------------------------------


def fingerprint(directory: Path) -> dict[str, str]:
    """``{relative path: sha256}`` for every rendered file, scratch space aside."""
    digests = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or ".lemontage" in path.relative_to(directory).parts:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        digests[str(path.relative_to(directory))] = digest.hexdigest()
    return digests


# -- running ------------------------------------------------------------------


def settle(seconds: float) -> None:
    """Let the previous run's dirty pages reach disk before timing the next one.

    A run of the channel control writes some 85 MB. The kernel flushes that in
    the background, *after* the process exits — so without this the writeback of
    run N lands inside run N+1 and shows up as a slow oscillation across a batch,
    wide enough (25% here) to swamp the difference being measured. Two batches of
    the same code came out 39.0s and 45.7s before this was added.
    """
    if seconds <= 0:
        return
    os.sync()
    time.sleep(seconds)


def time_run(
    pipeline: Path,
    out: Path,
    var_args: list[str],
    settle_s: float = 3.0,
    env: dict[str, str] | None = None,
) -> float:
    """One cold-cache run. Returns wall-clock seconds; raises if the run failed."""
    shutil.rmtree(out, ignore_errors=True)
    settle(settle_s)
    cmd = [sys.executable, "-m", "lemontage", "run", str(pipeline)]
    for var in var_args:
        cmd += ["--var", var]
    start = time.perf_counter()
    proc = subprocess.run(
        cmd, cwd=REPO, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"pipeline failed (exit {proc.returncode}) -- benchmark aborted")
    return elapsed


def summarize(label: str, pipeline: Path, var_args: list[str], times: list[float], files: dict):
    return {
        "label": label,
        "pipeline": str(pipeline),
        "vars": var_args,
        "runs": [round(t, 3) for t in times],
        "median": round(statistics.median(times), 3),
        "min": round(min(times), 3),
        "spread_pct": round((max(times) - min(times)) / statistics.median(times) * 100, 1),
        "files": files,
    }


def bench(
    pipeline: Path,
    runs: int,
    var_args: list[str],
    label: str | None,
    warmup: int = 1,
    settle_s: float = 3.0,
) -> dict:
    out = output_dir(pipeline)
    # A discarded warm-up run, because without one this harness disagreed with
    # itself by 6% on unchanged code: the first batch after a reboot reads the
    # source from disk, every later one from the OS page cache. That gap is wider
    # than most of the gains being measured, so it is paid once and thrown away.
    for i in range(1, warmup + 1):
        elapsed = time_run(pipeline, out, var_args, settle_s)
        print(f"  warmup {i}/{warmup}: {elapsed:7.2f}s (discarded)", flush=True)

    times = []
    for i in range(1, runs + 1):
        elapsed = time_run(pipeline, out, var_args, settle_s)
        times.append(elapsed)
        print(f"  run {i}/{runs}: {elapsed:7.2f}s", flush=True)

    return summarize(label or pipeline.stem, pipeline, var_args, times, fingerprint(out))


# -- A/B ----------------------------------------------------------------------


def ab(
    pipeline: Path,
    sides: tuple[str, str],
    runs: int,
    var_args: list[str],
    warmup: int,
    settle_s: float,
) -> tuple[dict, dict]:
    """Interleave two environments, A B A B ..., and report each side.

    Measuring all of A then all of B is the wrong protocol here: this machine
    drifts by 25% on a timescale longer than one batch, so whichever side ran
    during the slow stretch loses. Two batches of *identical* code came out 39.0s
    and 45.7s that way. Alternating spreads the drift evenly over both sides, and
    the paired per-round delta cancels what is left of it.
    """
    out = output_dir(pipeline)
    envs = [_parse_env(side) for side in sides]
    for i in range(1, warmup + 1):
        elapsed = time_run(pipeline, out, var_args, settle_s, envs[0])
        print(f"  warmup {i}/{warmup}: {elapsed:7.2f}s (discarded)", flush=True)

    times: list[list[float]] = [[], []]
    prints: list[dict] = [{}, {}]
    for round_no in range(1, runs + 1):
        for side in (0, 1):
            elapsed = time_run(pipeline, out, var_args, settle_s, envs[side])
            times[side].append(elapsed)
            prints[side] = fingerprint(out)
            print(f"  round {round_no}: {sides[side] or 'default'} {elapsed:7.2f}s", flush=True)
        delta = times[1][-1] - times[0][-1]
        print(f"           paired delta: {delta:+.2f}s", flush=True)

    a, b = (
        summarize(sides[s] or "default", pipeline, var_args, times[s], prints[s]) for s in (0, 1)
    )
    # The paired delta is the number to trust: each pair ran under the same
    # machine conditions, so the drift subtracts out of it.
    paired = [second - first for first, second in zip(*times, strict=True)]
    b["paired_delta_median"] = round(statistics.median(paired), 3)
    return a, b


def _parse_env(assignment: str) -> dict[str, str]:
    if not assignment:
        return {}
    key, _, value = assignment.partition("=")
    if not key or "=" not in assignment:
        raise SystemExit(f"--ab sides must be KEY=VALUE (or ''), got {assignment!r}")
    return {key: value}


def report(result: dict) -> None:
    print(
        f"\n{result['label']}: median {result['median']:.2f}s "
        f"(min {result['min']:.2f}s, spread {result['spread_pct']:.1f}%, "
        f"{len(result['runs'])} runs), {len(result['files'])} file(s) rendered"
    )


# -- comparing ----------------------------------------------------------------


def compare(before: dict, after: dict) -> int:
    """Print the wall-clock delta and the file-for-file verdict. Returns an exit code."""
    b, a = before["median"], after["median"]
    change = (a - b) / b * 100 if b else 0.0
    verdict = "faster" if a < b else ("slower" if a > b else "unchanged")
    print(f"wall clock (median): {b:.2f}s -> {a:.2f}s  ({change:+.1f}%, {verdict})")

    # A delta smaller than the run-to-run spread is not a result, and saying so
    # here is cheaper than arguing about it in a PR.
    noise = max(before.get("spread_pct", 0.0), after.get("spread_pct", 0.0))
    paired = after.get("paired_delta_median")
    if paired is not None:
        # From --ab: each pair ran back to back, so the machine's drift is already
        # subtracted and this survives a spread the medians cannot.
        print(f"paired delta (median of A/B rounds): {paired:+.2f}s")
    elif abs(change) <= noise:
        print(
            f"  ...but the run-to-run spread is {noise:.1f}%: this delta is noise, not a gain. "
            "Use --ab to interleave the two variants instead."
        )

    ok = _report_file_diff(before["files"], after["files"])
    print(
        "\noutput: identical, file for file"
        if ok
        else "\noutput: DIFFERENT -- this is not a pure perf change"
    )
    return 0 if ok else 1


def _report_file_diff(before: dict[str, str], after: dict[str, str]) -> bool:
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    for name in missing:
        print(f"  - gone:    {name}")
    for name in added:
        print(f"  + new:     {name}")
    for name in changed:
        print(f"  ! changed: {name}")
    return not (missing or added or changed)


# -- self-check ---------------------------------------------------------------


def self_check() -> int:
    """Assert the two pieces of logic worth getting wrong: the guard and the hash."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # fingerprint: content decides the digest, scratch space is ignored,
        # and a rename is a difference.
        (root / ".lemontage" / "work").mkdir(parents=True)
        (root / ".lemontage" / "work" / "tmp.mp4").write_bytes(b"scratch")
        (root / "reel.mp4").write_bytes(b"frames")
        first = fingerprint(root)
        assert list(first) == ["reel.mp4"], first
        assert first == fingerprint(root), "same bytes must hash the same"

        (root / "reel.mp4").write_bytes(b"other frames")
        assert fingerprint(root) != first, "changed bytes must change the digest"

        (root / "reel.mp4").rename(root / "renamed.mp4")
        assert not _report_file_diff(first, fingerprint(root)), "a rename is a difference"

    # output_dir: refuse the repo root, refuse a single-segment dir, refuse outside.
    for declared, why in (
        (".", "the repo root"),
        ("./output", "a shared top-level dir"),
        ("/etc", "outside the repo"),
        ("./output/../..", "escaping via .."),
    ):
        pipeline = Path(_write_temp_pipeline(declared))
        try:
            output_dir(pipeline)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"output_dir accepted {declared!r} ({why})")
        finally:
            pipeline.unlink()

    pipeline = Path(_write_temp_pipeline("./output/bench-x"))
    try:
        assert output_dir(pipeline) == (REPO / "output" / "bench-x").resolve()
    finally:
        pipeline.unlink()

    print("self-check: ok")
    return 0


def _write_temp_pipeline(declared_dir: str) -> str:
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", dir=REPO, delete=False, encoding="utf-8"
    )
    handle.write(f"name: t\noutput:\n  dir: {json.dumps(declared_dir)}\n")
    handle.close()
    return handle.name


# -- cli ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pipeline", nargs="?", help="control pipeline to time")
    parser.add_argument("--runs", type=int, default=3, help="cold-cache runs (default: 3)")
    parser.add_argument(
        "--warmup", type=int, default=1, help="discarded runs before timing (default: 1)"
    )
    parser.add_argument(
        "--var", action="append", default=[], metavar="KEY=VALUE", help="passed to lemontage run"
    )
    parser.add_argument("--label", help="name this measurement in the report")
    parser.add_argument("--save", metavar="FILE", help="write the result as JSON")
    parser.add_argument(
        "--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two --save files"
    )
    parser.add_argument(
        "--ab",
        nargs=2,
        metavar=("A", "B"),
        help="interleave two environments (KEY=VALUE each, '' for the default) "
        "-- the honest protocol when the machine drifts",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=3.0,
        help="seconds to let writeback finish before each run (default: 3)",
    )
    parser.add_argument("--self-check", action="store_true", help="test this script and exit")
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

    if args.compare:
        before, after = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        return compare(before, after)

    if not args.pipeline:
        parser.error("give a pipeline to time, or --compare two saved results")

    pipeline = Path(args.pipeline)
    if args.ab:
        before, after = ab(pipeline, tuple(args.ab), args.runs, args.var, args.warmup, args.settle)
        report(before)
        report(after)
        print()
        code = compare(before, after)
        if args.save:
            Path(args.save).write_text(
                json.dumps({"a": before, "b": after}, indent=2), encoding="utf-8"
            )
            print(f"saved to {args.save}")
        return code

    result = bench(pipeline, args.runs, args.var, args.label, args.warmup, args.settle)
    report(result)
    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"saved to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
