"""Constants describing the LeMontage v1 YAML specification.

Single source of truth for what the validator accepts. Keep this aligned with
docs/SPEC.md.
"""

# Spec versions the engine understands (the `lemontage:` key).
SUPPORTED_VERSIONS = frozenset({"1.0"})

# Required top-level keys.
REQUIRED_TOP_LEVEL = ("lemontage", "name", "input", "steps")

# All accepted top-level keys.
KNOWN_TOP_LEVEL = frozenset(
    {"lemontage", "name", "description", "vars", "input", "matrix", "steps", "output"}
)

# Built-in blocks shipped in v1. (tts is deferred to v2 — see TODO.)
BUILTIN_BLOCKS = frozenset(
    {
        "stt",
        "detect_clips",
        "cut",
        "captions",
        "export",
        "concat",
        "speed",
        "reverse",
        "stills",
        "still",
        "music",
    }
)

# Channel aggregators that may merge several channels via a list-valued `from`
# (e.g. `concat: {from: [viral, montage]}`). Mapped blocks read a single channel.
CHANNEL_MERGERS = frozenset({"concat"})

# Transitions the `concat` block can play between clips (a curated subset of
# FFmpeg's xfade set), plus "none" for an explicit hard cut at a single gap.
CONCAT_TRANSITIONS = frozenset(
    {
        "none",
        "fade",
        "fadeblack",
        "zoomin",
        "circleopen",
        "circleclose",
        "dissolve",
        "radial",
        "wipeleft",
        "wiperight",
        "wipeup",
        "wipedown",
        "slideleft",
        "slideright",
        "slideup",
        "slidedown",
    }
)

# How `export` fits the source into the target frame (see SPEC §6.6).
EXPORT_FIT_MODES = frozenset({"contain", "cover"})

# Motion effects `still` can apply while rendering an image to a clip (§6.11).
STILL_MOTIONS = frozenset({"zoomout", "zoomin", "panup", "pandown"})

# Fields a step may carry alongside its single block key (see SPEC §5.1).
COMMON_STEP_FIELDS = frozenset({"id", "cache", "on_failure", "retries", "requires"})

VALID_ON_FAILURE = frozenset({"abort", "skip", "retry"})

# Input: a local MP4 video, or a folder of images (slideshow / photo montage).
SUPPORTED_INPUT_TYPES = frozenset({"video", "images"})
SUPPORTED_INPUT_EXTENSIONS = (".mp4",)
# Image files the `stills` producer will pick up from an `images` source folder.
SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# Reserved but out of scope for v1 — using these is a validation error so that
# pipelines shared today stay forward-compatible (see SPEC §11).
RESERVED_TOP_LEVEL = frozenset({"hooks"})
RESERVED_BLOCKS = frozenset({"tts", "use"})
RESERVED_INPUT_TYPES = frozenset({"audio", "text", "url", "rss"})
RESERVED_DETECT_METHODS = frozenset({"engagement"})
CLOUD_PROVIDERS = frozenset({"elevenlabs", "deepgram", "openai", "claude", "gpt-4"})
