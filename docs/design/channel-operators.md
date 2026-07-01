# Design note — combining channels (multi-channel `concat`)

> **Status:** design only, not implemented. Branch `feat/channel-operators`.
> This note captures what to build later so we don't lose the context.

## Goal / use case

Assemble **several independent branches into one final video**. Motivating
example: take the single most viral moment (`detect_clips: loudness`,
`max_clips: 1`) and then cut to an edited random montage
(`detect_clips: random`), joined by a transition — **in one reel**.

Today each branch lives in its own channel, but there is **no way to merge two
channels into one output**: `concat` consumes a single channel.

```yaml
# what we want to be able to write
- detect_clips: { method: loudness, max_clips: 1, emit: viral }
- cut:    { from: viral }
- export: { from: viral }

- detect_clips: { method: random, max_clips: 6, emit: montage }
- cut:    { from: montage }
- export: { from: montage }

- concat:
    from: [viral, montage]      # <-- NOT supported yet
    transitions: [fade]
    output: "./output/final.mp4"
```

## Why this is NOT a small feature

`from` (a channel name) is a **core primitive**, currently a single string baked
through the engine. It is shared by `cut` / `captions` / `export` / `concat`, so
changing its shape ripples across the engine, not just one block.

Touch points (verified against the code):

- `engine/dag.py`
  - `Node.consumes: str | None` → must support **multiple** channels.
  - `build_nodes()` reads `from` as a string (`consume if isinstance(consume, str)`).
  - `_add_channel_edges()` wires **one** producer dependency per consumer and
    keeps same-channel consumers in listed order → must depend on **all**
    producers and rethink the ordering-chain when a step consumes many channels.
- `engine/executor.py`
  - channel-aggregator path: `items = ctx.channels.get(node.consumes, [])` → must
    gather and **order** items across the listed channels.
- `validator.py`
  - `_check_channel_refs()` assumes a single `from` string → must validate each
    entry of a list and keep the good error messages.
- `engine/blocks/base.py`
  - the block contract ("a block consumes one channel") documents the single-channel
    model → update.
- `engine/blocks/concat.py`
  - `execute_channel()` already takes a flat `items` list; if the executor hands it
    the concatenated-in-order items, concat itself barely changes.

## API options

1. **`concat.from` accepts a list** (minimal, concat-only)
   `from: [viral, montage]` → concatenate channel `viral` then `montage`.
   - Pros: smallest surface, directly solves the use case.
   - Cons: `from` now has two shapes (string | list); only concat understands the
     list unless we generalize.

2. **General multi-channel `from`** (any block)
   Allow `from: [a, b]` everywhere; mapped blocks would map over the concatenation
   of the channels.
   - Pros: consistent, powerful.
   - Cons: bigger blast radius, more edge cases (ordering, indices) for every block.

3. **A dedicated channel operator / block** (à la Nextflow `mix`/`concat`)
   e.g. a `merge` step: `merge: { from: [viral, montage], emit: all }` that produces
   a new channel, which `concat` then consumes normally.
   - Pros: keeps `from` single everywhere; the operator is explicit and reusable
     (mirrors Nextflow's `.concat()` / `.mix()`); clean separation.
   - Cons: one extra step in the YAML.

**Recommended:** option **3** (a `merge`/`concat`-operator step) or option **1**
scoped to concat. Option 3 matches the Nextflow mental model best and avoids
overloading `from` across the whole engine. Decide before coding.

## Semantics to pin down

- **Ordering**: channels concatenated in the listed order; within a channel, keep
  the existing `index` order. Define a stable global order for the merged items
  (e.g. re-index sequentially).
- **`index` collisions**: `viral` item `index 0` and `montage` item `index 0`
  clash → re-index on merge, or namespace by source channel.
- **Transitions at the join**: with N total clips there are N-1 gaps; make sure the
  boundary viral→montage gets a transition entry like any other gap.
- **Empty channels**: a branch that produced 0 clips should drop out gracefully.
- **Mixed shapes**: items may carry `file` (exported) or `clip` (cut) — concat
  already prefers `file` then `clip`; keep that.

## Testing plan

- `dag`: a step consuming `[a, b]` depends on both producers; topo order correct;
  cycle/missing-channel errors still raised per entry.
- `validator`: each entry validated; unknown channel in the list reported.
- `executor`/`concat`: merged items are ordered `a` then `b`, re-indexed, and the
  join gap receives a transition.
- end-to-end: loudness(1) + random(6) → single reel, clip count = 7, one transition
  at the viral→montage boundary.

## Out of scope (for the first cut)

- Interleaving channels (Nextflow `mix` semantics) — only ordered concat first.
- Operators like `filter`/`groupTuple` on channels.
