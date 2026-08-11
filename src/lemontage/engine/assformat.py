"""Escape user text before it enters an ASS (SubStation Alpha) subtitle file.

Both the ``captions`` word text (from the transcript of an untrusted input
video) and the ``export`` title text (from the pipeline file) are written into
ASS ``Dialogue`` lines that libass then renders inside an FFmpeg filtergraph.
ASS override blocks are delimited by ``{`` … ``}``, so unescaped braces in that
text could inject render directives; a lone backslash starts an escape such as
``\\N`` (line break) or ``\\h``. :func:`escape_text` neutralises both so the text
can only ever render as literal characters.
"""

from __future__ import annotations


def escape_text(text: str) -> str:
    """Make ``text`` safe to place in an ASS Dialogue field (literal only).

    ``{``/``}`` become parentheses so they cannot open an override block, and a
    stray backslash is dropped so it cannot start an ASS escape. Callers that
    emit their own tags (e.g. the karaoke ``{\\kNN}`` markers) must escape the
    user text *before* wrapping it with those tags.
    """
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


# The end timestamp for a cue with no closing time: past any real clip length,
# so libass keeps it on screen to the last frame.
FOREVER = "9:59:59.99"

_DOC_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

_DOC_STYLE = (
    "Style: {name},{font},{size},{primary},&H000000FF,{outline},&H64000000,"
    "-1,0,0,0,100,100,0,0,{border},{outline_w:g},{shadow:g},{align},"
    "{margin_l},{margin_r},{margin_v},1"
)

_DOC_EVENTS = """\

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def document(width: int, height: int, cues: list[dict]) -> str:
    """Build a full ASS file from a list of independently styled, timed cues.

    One ``Style`` and one ``Dialogue`` per cue, which is what lets a single
    render pass carry several texts that appear and disappear at their own
    times — instead of one filter pass (and one re-encode) per text.

    Each cue is a mapping: ``text`` (already escaped and tagged by the caller),
    ``start``/``end`` (seconds, ``end`` None = to the end of the clip), and the
    style fields ``font``, ``size``, ``primary``, ``outline``, ``outline_w``,
    ``shadow``, ``border``, ``align``, ``margin_l``, ``margin_r``, ``margin_v``.

    An optional ``pos`` ``(x, y)`` pins the cue there instead of letting the
    margins place it. That is not a convenience: libass shifts events that would
    overlap, so several cues on screen at once push each other off their seats —
    a fixed column's spacing collapses as soon as the text is big enough to
    collide. A positioned event is exempt from that.
    """
    styles, events = [], []
    for index, cue in enumerate(cues):
        name = f"Cue{index}"
        styles.append(
            _DOC_STYLE.format(
                name=name,
                font=cue["font"],
                size=int(cue["size"]),
                primary=cue["primary"],
                outline=cue.get("outline", "&H00000000"),
                border=cue.get("border", 1),
                outline_w=float(cue.get("outline_w", 0)),
                shadow=float(cue.get("shadow", 0)),
                align=int(cue["align"]),
                margin_l=int(cue["margin_l"]),
                margin_r=int(cue["margin_r"]),
                margin_v=int(cue["margin_v"]),
            )
        )
        end = cue.get("end")
        pos = cue.get("pos")
        body = f"{{\\pos({pos[0]:g},{pos[1]:g})}}{cue['text']}" if pos else cue["text"]
        events.append(
            f"Dialogue: 0,{timestamp(cue.get('start', 0.0))},"
            f"{timestamp(end) if end is not None else FOREVER},{name},,0,0,0,,{body}"
        )
    return (
        _DOC_HEADER.format(w=width, h=height)
        + "\n".join(styles)
        + "\n"
        + _DOC_EVENTS
        + "\n".join(events)
        + "\n"
    )


def timestamp(seconds: float) -> str:
    """Format ``seconds`` as an ASS timestamp ``H:MM:SS.cc`` (centiseconds).

    Negative input is clamped to zero: ASS has no notation for it, and the
    naive formatting of ``-0.5`` is ``-1:59:59.50`` — a time libass reads as
    valid but far past the clip, so the line silently never shows.
    """
    cs = round(max(0.0, seconds) * 100)
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"
