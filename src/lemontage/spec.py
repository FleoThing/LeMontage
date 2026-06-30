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
    {"stt", "detect_clips", "cut", "captions", "export", "concat", "reverse"}
)

# Fields a step may carry alongside its single block key (see SPEC §5.1).
COMMON_STEP_FIELDS = frozenset({"id", "cache", "on_failure", "retries", "requires"})

VALID_ON_FAILURE = frozenset({"abort", "skip", "retry"})

# Input: v1 supports local MP4 video only.
SUPPORTED_INPUT_TYPES = frozenset({"video"})
SUPPORTED_INPUT_EXTENSIONS = (".mp4",)

# Reserved but out of scope for v1 — using these is a validation error so that
# pipelines shared today stay forward-compatible (see SPEC §11).
RESERVED_TOP_LEVEL = frozenset({"hooks"})
RESERVED_BLOCKS = frozenset({"tts", "music", "use"})
RESERVED_INPUT_TYPES = frozenset({"audio", "text", "url", "rss"})
RESERVED_DETECT_METHODS = frozenset({"engagement"})
CLOUD_PROVIDERS = frozenset({"elevenlabs", "deepgram", "openai", "claude", "gpt-4"})
