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
