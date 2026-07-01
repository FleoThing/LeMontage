"""Validate a LeMontage pipeline document against the v1 spec.

The public entry points are :func:`validate_doc` (validate an already-parsed
mapping) and :func:`validate_file` (load a YAML file and validate it). Both
return a list of human-readable error strings; an empty list means the pipeline
is valid.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import spec


def validate_file(path: str | Path) -> list[str]:
    """Load a YAML file and validate it. Returns a list of error strings."""
    path = Path(path)
    if not path.exists():
        return [f"file not found: {path}"]
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"invalid YAML: {exc}"]
    return validate_doc(doc)


def validate_doc(doc: object) -> list[str]:
    """Validate a parsed pipeline mapping. Returns a list of error strings."""
    errors: list[str] = []

    if not isinstance(doc, dict):
        return ["pipeline must be a YAML mapping at the top level"]

    _check_top_level_keys(doc, errors)
    _check_version(doc, errors)
    _check_name(doc, errors)
    _check_input(doc, errors)
    emitted = _check_steps(doc, errors)
    _check_channel_refs(doc, errors, emitted)

    return errors


def _check_top_level_keys(doc: dict, errors: list[str]) -> None:
    for key in spec.REQUIRED_TOP_LEVEL:
        if key not in doc:
            errors.append(f"missing required top-level key: '{key}'")

    for key in doc:
        if key in spec.RESERVED_TOP_LEVEL:
            errors.append(f"'{key}' is reserved and not supported in v1")
        elif key not in spec.KNOWN_TOP_LEVEL:
            errors.append(f"unknown top-level key: '{key}'")


def _check_version(doc: dict, errors: list[str]) -> None:
    if "lemontage" not in doc:
        return
    version = doc["lemontage"]
    if not isinstance(version, str):
        errors.append("'lemontage' version must be a string, e.g. \"1.0\"")
    elif version not in spec.SUPPORTED_VERSIONS:
        supported = ", ".join(sorted(spec.SUPPORTED_VERSIONS))
        errors.append(f"unsupported spec version '{version}' (supported: {supported})")


def _check_name(doc: dict, errors: list[str]) -> None:
    if "name" in doc and not isinstance(doc["name"], str):
        errors.append("'name' must be a string")


def _check_input(doc: dict, errors: list[str]) -> None:
    if "input" not in doc:
        return
    src = doc["input"]
    if not isinstance(src, dict):
        errors.append("'input' must be a mapping")
        return

    itype = src.get("type")
    if itype is None:
        errors.append("input is missing 'type'")
    elif itype in spec.RESERVED_INPUT_TYPES:
        errors.append(f"input.type '{itype}' is reserved and not supported in v1 (use 'video')")
    elif itype not in spec.SUPPORTED_INPUT_TYPES:
        errors.append(f"unknown input.type '{itype}' (v1 supports 'video')")

    source = src.get("source")
    if source is None:
        errors.append("input is missing 'source'")
    elif not isinstance(source, str):
        errors.append("input.source must be a string path")
    elif not source.lower().endswith(spec.SUPPORTED_INPUT_EXTENSIONS):
        errors.append("input.source must be a .mp4 file in v1")


def _check_steps(doc: dict, errors: list[str]) -> set[str]:
    """Validate the steps list and return the set of emitted channel names."""
    emitted: set[str] = set()
    if "steps" not in doc:
        return emitted

    steps = doc["steps"]
    if not isinstance(steps, list) or not steps:
        errors.append("'steps' must be a non-empty list")
        return emitted

    for index, step in enumerate(steps):
        label = f"step #{index + 1}"
        if not isinstance(step, dict):
            errors.append(f"{label}: each step must be a mapping")
            continue

        block_keys = [k for k in step if k not in spec.COMMON_STEP_FIELDS]
        if len(block_keys) == 0:
            errors.append(f"{label}: no block declared")
            continue
        if len(block_keys) > 1:
            errors.append(f"{label}: a step must declare exactly one block, got {block_keys}")
            continue

        block = block_keys[0]
        if "id" in step:
            label = f"step '{step['id']}'"

        if block in spec.RESERVED_BLOCKS:
            errors.append(f"{label}: block '{block}' is reserved and not supported in v1")
            continue
        if block not in spec.BUILTIN_BLOCKS:
            errors.append(f"{label}: unknown block '{block}'")
            continue

        _check_common_fields(step, label, errors)
        _check_block_params(block, step.get(block), label, errors, emitted)

    return emitted


def _check_common_fields(step: dict, label: str, errors: list[str]) -> None:
    on_failure = step.get("on_failure")
    if on_failure is not None and on_failure not in spec.VALID_ON_FAILURE:
        valid = ", ".join(sorted(spec.VALID_ON_FAILURE))
        errors.append(f"{label}: on_failure must be one of {valid}")

    retries = step.get("retries")
    if retries is not None and not isinstance(retries, int):
        errors.append(f"{label}: retries must be an integer")


def _check_block_params(
    block: str, params: object, label: str, errors: list[str], emitted: set[str]
) -> None:
    if params is None:
        return
    if not isinstance(params, dict):
        errors.append(f"{label}: block '{block}' params must be a mapping")
        return

    # Cloud providers are reserved for a later phase.
    for field in ("engine", "model"):
        value = params.get(field)
        if isinstance(value, str) and value.lower() in spec.CLOUD_PROVIDERS:
            errors.append(
                f"{label}: provider '{value}' is reserved for a later phase (v1 is local-only)"
            )

    if block == "detect_clips":
        method = params.get("method")
        if method in spec.RESERVED_DETECT_METHODS:
            errors.append(f"{label}: detect_clips.method '{method}' is reserved in v1")

    if block == "export":
        fit = params.get("fit")
        if fit is not None and (
            not isinstance(fit, str) or fit.lower() not in spec.EXPORT_FIT_MODES
        ):
            valid = ", ".join(sorted(spec.EXPORT_FIT_MODES))
            errors.append(f"{label}: unknown export fit '{fit}' (choose from: {valid})")
        mute = params.get("mute")
        if mute is not None and not isinstance(mute, (bool, list)):
            errors.append(f"{label}: export.mute must be a boolean or a list of booleans")

    emit = params.get("emit")
    if emit is not None:
        if not isinstance(emit, str):
            errors.append(f"{label}: emit must be a channel name (string)")
        else:
            emitted.add(emit)


def _check_channel_refs(doc: dict, errors: list[str], emitted: set[str]) -> None:
    """Every `from:` consumer must reference a channel some step `emit`s."""
    steps = doc.get("steps")
    if not isinstance(steps, list):
        return

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        block_keys = [k for k in step if k not in spec.COMMON_STEP_FIELDS]
        if len(block_keys) != 1:
            continue
        params = step.get(block_keys[0])
        if not isinstance(params, dict):
            continue
        channel = params.get("from")
        if isinstance(channel, str) and channel not in emitted:
            label = f"step '{step['id']}'" if "id" in step else f"step #{index + 1}"
            errors.append(f"{label}: 'from: {channel}' references an unknown channel")
