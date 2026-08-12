"""``compose``: layered composition, N sources placed in one frame (SPEC §6.17).

Every other block works on **one** source at a time: `concat` joins clips in
time, `overlay` draws glyphs and composites a still PNG. Nothing put two moving
pictures in the same frame. ``compose`` is that missing primitive: a canvas,
and a stack of layers laid into it, each with its own rectangle.

It is deliberately **not** a dual-screen block. A split screen is two layers at
half height; a picture-in-picture is a small layer in a corner; a subject over a
backdrop is an image layer under a keyed video layer. Same code, different
coordinates, which is the whole point of writing it once.

A layer names its own source (``video:`` or ``image:``), following the per-step
``input:`` convention the rest of the engine already uses, so no pipeline-level
"multiple inputs" concept is needed. ``video:`` also accepts the name of a
channel emitted earlier in the pipeline.

Geometry is ``x``/``y``/``width``/``height``. An **int is pixels**, a
**``"50%"`` string is a share of the canvas axis**, so one composition replays
in vertical, square or horizontal without rewriting the numbers. A negative
``x``/``y`` counts back from the opposite edge, the same convention
``overlay`` uses for a corner watermark.

``key:`` makes one colour transparent (the green screen case) so the layers
underneath show through. It is a per-layer option rather than a block, because
"remove the background" is a property of a source, not a kind of edit.

Layers rarely share a duration. The composition lasts as long as its **longest**
layer; a shorter one holds its last frame (``on_short: freeze``, the default),
restarts (``loop``) or simply ends and lets the layers below show
(``hide``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...spec import COMPOSE_FIT_MODES, COMPOSE_ON_SHORT
from .. import ffmpeg, safepath
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult
from .export import _RESOLUTIONS, _bg_pad_color

_DEFAULT_FPS = 30
# Measured on real green-screen footage, not guessed. chromakey's own defaults
# (0.01/0.0) only key a mathematically perfect colour, which no camera produces;
# but a wide tolerance is the worse failure: at 0.3 the *subject* goes
# semi-transparent, because skin and cloth land within 30% of the key in UV space.
_DEFAULT_TOLERANCE = 0.12
_DEFAULT_SOFTNESS = 0.02
# Green bouncing off the screen tints the subject's edges (hair especially), so
# keying alone leaves a halo. `despill` neutralises it; 0.5 clears the halo
# without draining genuinely green things in frame.
_DEFAULT_SPILL = 0.5
# FFmpeg's named "green" is #008000, a dark green nothing is ever shot against.
# A green screen is the bright primary, so the shorthands point at that instead.
_KEY_COLORS = {"green": "0x00FF00", "blue": "0x0000FF"}


class ComposeBlock(Block):
    name = "compose"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        layers = params.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("compose: needs a non-empty 'layers' list")
        canvas = _canvas(params)
        resolved = [_resolve_layer(layer, i, ctx) for i, layer in enumerate(layers)]
        duration = _duration(params, resolved)
        fps = _fps(params, resolved)
        out = _output_path(params, ctx, step_id)
        _render(resolved, params, canvas, duration, fps, out)
        composed = str(out)
        return BlockResult(
            outputs={"file": composed},
            channel_items=[{"index": 0, "file": composed, "clip": composed}],
        )


def _canvas(params: dict[str, Any]) -> tuple[int, int]:
    """The output frame: a named ``format`` preset, or an explicit ``WxH`` size."""
    size = params.get("size")
    if size is not None:
        try:
            width, height = (int(part) for part in str(size).lower().split("x", 1))
        except ValueError:
            raise ValueError(f"compose: size must look like '1080x1920', got '{size}'") from None
        if width < 2 or height < 2:
            raise ValueError(f"compose: size must be at least 2x2, got '{size}'")
        return width, height
    fmt = str(params.get("format", "vertical")).lower()
    if fmt not in _RESOLUTIONS:
        valid = ", ".join(sorted(_RESOLUTIONS))
        raise ValueError(f"compose: unknown format '{fmt}' (choose from: {valid})")
    return _RESOLUTIONS[fmt]


def _resolve_layer(layer: object, index: int, ctx: RunContext) -> dict[str, Any]:
    """One layer's source and kind, checked up front so FFmpeg can't fail obscurely."""
    if not isinstance(layer, dict):
        raise ValueError(f"compose: layer {index} must be a mapping")
    video, image = layer.get("video"), layer.get("image")
    if video is not None and image is not None:
        raise ValueError(f"compose: layer {index} has both 'video' and 'image' (pick one)")
    if video is None and image is None:
        raise ValueError(f"compose: layer {index} needs a 'video' or an 'image'")
    source = _channel_file(str(video), ctx) if video is not None else str(image)
    if not Path(source).is_file():
        raise ValueError(f"compose: layer {index} source '{source}' not found")
    return {"params": layer, "index": index, "source": source, "is_video": video is not None}


def _channel_file(value: str, ctx: RunContext) -> str:
    """A layer's ``video``: the file of a channel emitted earlier, or a path.

    A channel carrying several clips is refused rather than silently composing
    only its first: `concat` is what turns a channel into one clip, and doing it
    implicitly here would drop footage without saying so.
    """
    items = ctx.channels.get(value)
    if items is None:
        return value
    files = [it.get("file") or it.get("clip") for it in items]
    files = [f for f in files if f]
    if len(files) != 1:
        raise ValueError(
            f"compose: channel '{value}' holds {len(files)} clips; "
            f"compose takes one clip per layer (run 'concat' on it first)"
        )
    return str(files[0])


def _extent(value: object, total: int, field: str) -> int:
    """A layer size: an int in pixels, or a ``"50%"`` share of the canvas axis."""
    if isinstance(value, bool):
        raise ValueError(f"compose: {field} must be pixels or a percentage, got a boolean")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.endswith("%"):
        try:
            return round(total * float(text[:-1]) / 100)
        except ValueError:
            raise ValueError(f"compose: {field} '{value}' is not a valid percentage") from None
    try:
        return int(text)
    except ValueError:
        raise ValueError(
            f"compose: {field} must be pixels (1080) or a percentage ('50%'), got '{value}'"
        ) from None


def _rect(layer: dict[str, Any], canvas: tuple[int, int]) -> tuple[int, int, int, int]:
    """The layer's ``x, y, w, h`` in pixels, resolved against the canvas.

    A negative ``x``/``y`` counts back from the opposite edge (``x: -40`` seats
    the layer 40px in from the right), which is how a corner is expressed
    without knowing the canvas width.
    """
    params, index = layer["params"], layer["index"]
    canvas_w, canvas_h = canvas
    width = _extent(params.get("width", "100%"), canvas_w, f"layer {index} width")
    height = _extent(params.get("height", "100%"), canvas_h, f"layer {index} height")
    if width < 2 or height < 2:
        raise ValueError(f"compose: layer {index} resolves to {width}x{height} (too small)")
    x = _extent(params.get("x", 0), canvas_w, f"layer {index} x")
    y = _extent(params.get("y", 0), canvas_h, f"layer {index} y")
    if x < 0:
        x = canvas_w - width + x
    if y < 0:
        y = canvas_h - height + y
    return x, y, width, height


def _duration(params: dict[str, Any], layers: list[dict[str, Any]]) -> float:
    """How long the composition runs: the longest video layer, or an explicit `duration`."""
    if params.get("duration") is not None:
        return parse_seconds(params["duration"])
    lengths = [ffmpeg.probe_duration(layer["source"]) for layer in layers if layer["is_video"]]
    lengths = [length for length in lengths if length > 0]
    if not lengths:
        raise ValueError(
            "compose: every layer is a still, so the composition has no length. "
            "add a 'duration:' (e.g. duration: 5s)"
        )
    return max(lengths)


def _fps(params: dict[str, Any], layers: list[dict[str, Any]]) -> int:
    """Output frame rate: an explicit `fps`, else the fastest video layer."""
    if params.get("fps") is not None:
        rate = int(params["fps"])
        if rate < 1:
            raise ValueError(f"compose: fps must be >= 1, got {rate}")
        return rate
    rates = [ffmpeg.probe_fps(layer["source"]) for layer in layers if layer["is_video"]]
    rates = [rate for rate in rates if rate > 0]
    return round(max(rates)) if rates else _DEFAULT_FPS


def _key_filter(params: dict[str, Any], index: int) -> list[str]:
    """``chromakey`` making one colour transparent, so lower layers show through."""
    key = params.get("key")
    if key is None:
        return []
    if key is True:
        key = {}
    if not isinstance(key, dict):
        raise ValueError(
            f"compose: layer {index} 'key' must be a mapping (color/tolerance/softness)"
        )
    raw = str(key.get("color", "green")).lower()
    color = _KEY_COLORS.get(raw)
    if color is None:
        if not raw.startswith("#") or len(raw) != 7:
            valid = ", ".join(sorted(_KEY_COLORS))
            raise ValueError(
                f"compose: layer {index} key.color '{raw}' must be a hex '#rrggbb' "
                f"or one of: {valid}"
            )
        color = f"0x{raw[1:]}"
    tolerance = _unit(key.get("tolerance", _DEFAULT_TOLERANCE), f"layer {index} key.tolerance")
    softness = _unit(key.get("softness", _DEFAULT_SOFTNESS), f"layer {index} key.softness")
    spill = _unit(key.get("spill", _DEFAULT_SPILL), f"layer {index} key.spill")
    # yuva420p first: chromakey writes alpha, and the source pixel format has none.
    chain = ["format=yuva420p", f"chromakey={color}:{tolerance}:{softness}"]
    if spill > 0:
        chain.append(f"despill=type={_spill_type(color)}:mix={spill}:expand=0.3")
    return chain


def _spill_type(color: str) -> str:
    """Which cast ``despill`` removes. It only knows green and blue, so a custom
    key colour is answered by whichever of the two channels dominates it."""
    green, blue = int(color[4:6], 16), int(color[6:8], 16)
    return "blue" if blue > green else "green"


def _unit(value: object, field: str) -> float:
    """A 0..1 knob, refused outside its range rather than clamped silently."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"compose: {field} must be a number between 0 and 1, got '{value}'")
    if not 0 <= float(value) <= 1:
        raise ValueError(f"compose: {field} must be between 0 and 1, got {value}")
    return float(value)


def _fit_chain(layer: dict[str, Any], width: int, height: int) -> tuple[list[str], str]:
    """Scaling into the layer rect, and the ``overlay`` x/y offset it implies.

    ``contain`` deliberately does not pad: a padded layer would hide the layers
    beneath it behind bars, which is exactly what compositing is meant to avoid.
    The fitted picture is centred in its rect instead.
    """
    fit = str(layer["params"].get("fit", "cover")).lower()
    if fit not in COMPOSE_FIT_MODES:
        valid = ", ".join(sorted(COMPOSE_FIT_MODES))
        raise ValueError(
            f"compose: layer {layer['index']} unknown fit '{fit}' (choose from: {valid})"
        )
    if fit == "stretch":
        return [f"scale={width}:{height}", "setsar=1"], "0:0"
    if fit == "cover":
        return (
            [
                f"scale={width}:{height}:force_original_aspect_ratio=increase",
                f"crop={width}:{height}",
                "setsar=1",
            ],
            "0:0",
        )
    return (
        [f"scale={width}:{height}:force_original_aspect_ratio=decrease", "setsar=1"],
        f"({width}-w)/2:({height}-h)/2",
    )


def _timing(layer: dict[str, Any], duration: float) -> tuple[list[str], list[str]]:
    """What a layer shorter than the composition does: filters, and input flags."""
    mode = str(layer["params"].get("on_short", "freeze")).lower()
    if mode not in COMPOSE_ON_SHORT:
        valid = ", ".join(sorted(COMPOSE_ON_SHORT))
        raise ValueError(
            f"compose: layer {layer['index']} unknown on_short '{mode}' (choose from: {valid})"
        )
    if not layer["is_video"]:
        # A still has no length of its own: it is looped to the full duration at
        # the input, so the on_short modes simply do not apply to it.
        return [], ["-loop", "1", "-t", f"{duration:.3f}"]
    if mode == "loop":
        return [], ["-stream_loop", "-1"]
    if mode == "freeze":
        # Holds the last frame. stop_duration is capped at the full duration,
        # which the output -t then trims. Padding a layer that is already long
        # enough costs nothing.
        return [f"tpad=stop_mode=clone:stop_duration={duration:.3f}"], []
    return [], []  # hide: the layer ends, and eof_action=pass reveals what is under it


def _graph(
    layers: list[dict[str, Any]],
    canvas: tuple[int, int],
    background: object,
    duration: float,
    fps: int,
) -> tuple[str, str]:
    """The ``filter_complex`` stacking every layer, and the video label to map."""
    width, height = canvas
    color = _bg_pad_color(background)
    steps = [f"color=c={color}:s={width}x{height}:r={fps}:d={duration:.3f}[base]"]
    current = "[base]"
    for position, layer in enumerate(layers):
        x, y, rect_w, rect_h = _rect(layer, canvas)
        fit_chain, offset = _fit_chain(layer, rect_w, rect_h)
        pad_chain, _ = _timing(layer, duration)
        chain = _key_filter(layer["params"], layer["index"]) + fit_chain + pad_chain
        chain.append("setpts=PTS-STARTPTS")
        steps.append(f"[{position}:v]{','.join(chain)}[l{position}]")
        # eof_action=pass keeps the composition running when a layer ends early
        # (`on_short: hide`) instead of freezing the whole frame on its last one.
        xy = f"{x}+{offset.split(':')[0]}:{y}+{offset.split(':')[1]}"
        steps.append(f"{current}[l{position}]overlay={xy}:eof_action=pass[c{position}]")
        current = f"[c{position}]"
    return ";".join(steps), current


def _audio_args(params: dict[str, Any], layers: list[dict[str, Any]]) -> list[str]:
    """Which layer is heard: an explicit `audio`, else the first layer that has one."""
    choice = params.get("audio")
    audible = [layer for layer in layers if layer["is_video"] and ffmpeg.has_audio(layer["source"])]
    if choice is None:
        return ["-map", f"{audible[0]['index']}:a"] if audible else ["-an"]
    if isinstance(choice, str) and choice.lower() == "none":
        return ["-an"]
    if isinstance(choice, str) and choice.lower() == "mix":
        if not audible:
            return ["-an"]
        inputs = "".join(f"[{layer['index']}:a]" for layer in audible)
        return [
            "-filter_complex", f"{inputs}amix=inputs={len(audible)}:normalize=0[mixed]",
            "-map", "[mixed]",
        ]  # fmt: skip
    if isinstance(choice, bool) or not isinstance(choice, int):
        raise ValueError(f"compose: audio must be a layer index, 'mix' or 'none', got '{choice}'")
    if not 0 <= choice < len(layers):
        raise ValueError(f"compose: audio layer {choice} is out of range (0..{len(layers) - 1})")
    if not layers[choice]["is_video"]:
        raise ValueError(f"compose: audio layer {choice} is an image, it has no sound")
    return ["-map", f"{choice}:a"]


def _render(
    layers: list[dict[str, Any]],
    params: dict[str, Any],
    canvas: tuple[int, int],
    duration: float,
    fps: int,
    out: Path,
) -> None:
    args: list[str] = []
    for layer in layers:
        _, input_flags = _timing(layer, duration)
        args += [*input_flags, "-i", layer["source"]]
    graph, label = _graph(layers, canvas, params.get("background"), duration, fps)
    audio = _audio_args(params, layers)
    # A `mix` carries its own filter_complex; FFmpeg takes only one, so the two
    # graphs are joined rather than passed twice (the second would win silently).
    if "-filter_complex" in audio:
        graph = f"{graph};{audio[audio.index('-filter_complex') + 1]}"
        audio = [arg for arg in audio if arg != "-filter_complex"]
        audio.pop(0)
    ffmpeg.run([
        *args,
        "-filter_complex", graph,
        "-map", label,
        *audio,
        "-t", f"{duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out),
    ])  # fmt: skip


def _output_path(params: dict[str, Any], ctx: RunContext, step_id: str) -> Path:
    template = params.get("output")
    if template:
        rendered = (
            str(template)
            .replace("{{ name }}", ctx.pipeline_name)
            .replace("{{name}}", ctx.pipeline_name)
        )
        out = Path(rendered)
    else:
        out = ctx.output_dir / f"{ctx.pipeline_name}-{step_id}.mp4"
    out = safepath.confine(out, safepath.allowed_roots(ctx.output_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    return out
