"""Security regression tests.

Each test pins a hardening added to stop a pipeline file (or the transcript of
an untrusted input video) from escaping the output tree, injecting an ASS render
directive, breaking the concat list, or asking FFmpeg for an absurd allocation.
"""

from pathlib import Path

import pytest

from lemontage.cli import _parse_var_overrides
from lemontage.engine import safepath
from lemontage.engine.assformat import escape_text
from lemontage.engine.blocks.captions import _dialogue
from lemontage.engine.blocks.concat import _concat, _concat_escape
from lemontage.engine.blocks.export import _output_path, _target_size, _title_ass
from lemontage.engine.blocks.speed import _factor
from lemontage.engine.context import RunContext


def ctx(tmp_path, **kw):
    base = dict(
        vars={},
        input={"source": "ep.mp4"},
        matrix={},
        output_dir=tmp_path,
        pipeline_name="demo",
    )
    base.update(kw)
    return RunContext(**base)


# --- S1: output path confinement -------------------------------------------


def test_confine_allows_path_under_output_dir(tmp_path):
    target = tmp_path / "sub" / "reel.mp4"
    assert safepath.confine(target, [tmp_path]) == target.resolve()


def test_confine_rejects_absolute_escape(tmp_path):
    with pytest.raises(safepath.UnsafePathError):
        safepath.confine(Path("/etc/cron.d/x"), [tmp_path])


def test_confine_rejects_dotdot_traversal(tmp_path):
    with pytest.raises(safepath.UnsafePathError):
        safepath.confine(tmp_path / ".." / ".." / "escape.mp4", [tmp_path])


def test_export_output_path_rejects_traversal(tmp_path):
    c = ctx(tmp_path)
    with pytest.raises(safepath.UnsafePathError):
        _output_path({"output": "/tmp/pwned.mp4"}, c, index=0)


def test_export_output_path_rejects_relative_escape(tmp_path):
    c = ctx(tmp_path)
    with pytest.raises(safepath.UnsafePathError):
        _output_path({"output": str(tmp_path / ".." / "escape.mp4")}, c, index=0)


def test_concat_output_path_rejects_traversal(tmp_path):
    from lemontage.engine.blocks.concat import _output_path as concat_output_path

    c = ctx(tmp_path)
    with pytest.raises(safepath.UnsafePathError):
        concat_output_path({"output": "/etc/passwd.mp4"}, c)


# --- S2: ASS text escaping --------------------------------------------------


def test_escape_text_neutralises_override_braces():
    # Braces become parens and the backslashes that would form override tags go.
    assert escape_text(r"{\fs99\pos(0,0)}hi") == "(fs99pos(0,0))hi"
    assert "{" not in escape_text("a{b}c") and "}" not in escape_text("a{b}c")


def test_escape_text_drops_backslash():
    assert "\\" not in escape_text(r"line\Nbreak")


def test_title_text_is_escaped(tmp_path):
    content = _title_ass({"title": r"{\fs200}HACK"}, ctx(tmp_path), "t").read_text()
    # The injected override block must not survive verbatim in the Dialogue line.
    assert r"{\fs200}" not in content
    assert "(" in content and "HACK" in content


def test_caption_word_text_is_escaped():
    line = {
        "start": 0.0,
        "end": 1.0,
        "words": [{"start": 0.0, "end": 1.0, "text": r"{\fs200}boom"}],
        "text": "x",
    }
    dialogue = _dialogue(line)
    # Only our own karaoke tag ({\kNN}) may appear; the injected one is gone.
    assert r"{\fs200}" not in dialogue
    assert "\\k" in dialogue  # our karaoke tag is intact


def test_caption_segment_fallback_is_escaped():
    line = {"start": 0.0, "end": 1.0, "words": [], "text": r"{\an5}evil"}
    assert r"{\an5}" not in _dialogue(line)


# --- S3: concat-list escaping ----------------------------------------------


def test_concat_escape_handles_single_quote():
    assert _concat_escape("a'b.mp4") == "a'\\''b.mp4"


def test_concat_escape_doubles_backslash():
    assert _concat_escape("a\\b.mp4") == "a\\\\b.mp4"


def test_concat_list_quotes_are_escaped(tmp_path):
    clip = tmp_path / "weird'name.mp4"
    clip.write_bytes(b"x")
    list_path = tmp_path / "list.txt"
    captured = {}

    import lemontage.engine.blocks.concat as concat_mod

    def fake_run(args):
        captured["args"] = args

    original = concat_mod.ffmpeg.run
    concat_mod.ffmpeg.run = fake_run
    try:
        _concat([str(clip)], tmp_path / "out.mp4", list_path)
    finally:
        concat_mod.ffmpeg.run = original

    written = list_path.read_text()
    # The apostrophe is closed/escaped/reopened, never left bare inside the quotes.
    assert "'\\''" in written


# --- S4: numeric bounds -----------------------------------------------------


def test_resolution_rejects_oversize():
    with pytest.raises(ValueError, match="out of range"):
        _target_size({"resolution": "999999x999999"})


def test_resolution_rejects_nonnumeric():
    with pytest.raises(ValueError, match="invalid resolution"):
        _target_size({"resolution": "huge"})


def test_resolution_rejects_zero():
    with pytest.raises(ValueError, match="out of range"):
        _target_size({"resolution": "0x100"})


def test_title_size_bound(tmp_path):
    with pytest.raises(ValueError, match="title_size"):
        _title_ass({"title": "hi", "title_size": 99999}, ctx(tmp_path), "t")


def test_speed_factor_capped():
    with pytest.raises(ValueError, match="<="):
        _factor({"factor": 1000})


# --- S5: --var key validation ----------------------------------------------


def test_var_rejects_empty_key():
    with pytest.raises(ValueError, match="empty key"):
        _parse_var_overrides(["=value"])


def test_var_rejects_dotted_key():
    with pytest.raises(ValueError, match="must not contain"):
        _parse_var_overrides(["a.b=value"])


def test_var_accepts_plain_key():
    assert _parse_var_overrides(["title=Hi"]) == {"title": "Hi"}
