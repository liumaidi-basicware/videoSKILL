#!/usr/bin/env python3
"""Video model selection and capability-aware fallback chain.

Extracted from video_engine.py (v3 split). Contains:
  - Model catalog normalization and resolution
  - Capability-aware model picking (allowVideoType from gateway)
  - Fallback chain constants (VIDEO_MODEL_FALLBACK, VIDEO_MODEL_CAPS, etc.)
  - Image model selection

Dependencies: br_client (for live model catalog queries)
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import br_client

# ── Constants ───────────────────────────────────────────────────────────

DEFAULT_MODEL = "seedance-2.0"

# Model fallback chain: seedance (fast/cheap) → kling (only type4 support) → wan
VIDEO_MODEL_FALLBACK = [
    "seedance-2.0",
    "kling-v3-omni-video",
    "wan2.7-i2v",
]

# Offline capability table (only used when gateway is unreachable)
VIDEO_MODEL_CAPS = {
    "seedance-2.0": {1, 2, 3, 5},
    "kling-v3-omni-video": {1, 2, 3, 4, 5},
    "wan2.7-i2v": {1, 2},
}

VIDEO_MODEL_INTEGRATED_AUDIO = {
    "seedance-2.0": True,
    "kling-v3-omni-video": True,
    "kling-v3-omni": True,
    "wan2.7-i2v": False,
}

IMAGE_MODEL_FALLBACK = [
    "seedream-5.0",
    "nano banana pro",
    "imagen 4 ultra",
    "kling-v3-omni-image",
]


# ── Catalog helpers ─────────────────────────────────────────────────────

def _catalog_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "online", "active", "enabled", "available"}:
            return True
        if normalized in {"0", "false", "no", "off", "offline", "inactive", "disabled", "unavailable"}:
            return False
    return None


def _catalog_list(value, *, integers=False):
    if isinstance(value, str):
        try:
            value = json.loads(value.strip())
        except (TypeError, ValueError):
            value = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        value = [key for key, enabled in value.items() if _catalog_bool(enabled) is not False]
    if not isinstance(value, (list, tuple, set)):
        value = [value] if value not in (None, "") else []
    output = []
    for item in value:
        try:
            normalized = int(item) if integers else str(item).strip()
        except (TypeError, ValueError):
            continue
        if normalized not in output and normalized != "":
            output.append(normalized)
    return output


def _integrated_audio_value(record):
    capabilities = record.get("capabilities") if isinstance(record.get("capabilities"), dict) else {}
    audio = capabilities.get("audio") if isinstance(capabilities.get("audio"), dict) else {}
    for source, key in (
            (record, "integratedAudio"), (record, "integrated_audio"),
            (record, "supportsIntegratedAudio"), (record, "audioIntegrated"),
            (record, "supportsAudio"), (record, "audioSupport"),
            (record, "audio"), (record, "allowAudio"),
            (capabilities, "integratedAudio"), (audio, "integrated")):
        if key in source:
            return _catalog_bool(source.get(key))
    return None


def _catalog_submission_names(raw):
    """Return only names explicitly advertised by the provider catalog."""
    names = set()
    for key in ("modelId", "id", "modelName", "name", "displayName"):
        value = raw.get(key)
        if value:
            names.add(str(value).strip())
    for key in ("alias", "aliases"):
        names.update(_catalog_list(raw.get(key)))
    return {name for name in names if name}


# ── Catalog normalization ──────────────────────────────────────────────

def _normalize_model_catalog(models):
    """Normalize model identities while retaining duplicate/alias ambiguity."""
    records = {}
    for raw in models or []:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("modelId") or raw.get("id") or
                       raw.get("modelName") or "").strip()
        if not model_id:
            continue
        aliases = {model_id}
        for key in ("modelName", "displayName", "name", "alias", "aliases"):
            aliases.update(_catalog_list(raw.get(key)))
        online, status = _catalog_bool(raw.get("online")), _catalog_bool(raw.get("status"))
        raw_types = raw.get("allowVideoType")
        candidate = {
            "id": model_id, "aliases": aliases,
            "submission_names": _catalog_submission_names(raw),
            "active": online is not False and status is not False and (online is True or status is True),
            "allow_types": (set(_catalog_list(raw_types, integers=True))
                            if raw_types not in (None, "") else None),
            "image_count": (int(raw.get("imageCount"))
                            if str(raw.get("imageCount", "")).strip().isdigit() else None),
            "integrated_audio": _integrated_audio_value(raw), "conflict": False,
        }
        current = records.get(model_id)
        if current is None:
            records[model_id] = candidate
            continue
        current["aliases"].update(aliases)
        current["submission_names"].update(_catalog_submission_names(raw))
        current["active"] = current["active"] or candidate["active"]
        for field in ("allow_types", "integrated_audio"):
            old, new = current[field], candidate[field]
            if old is None:
                current[field] = new
            elif new is not None and old != new:
                current[field] = None
                current["conflict"] = True
    alias_index = {}
    for model_id, record in records.items():
        for alias in record["aliases"]:
            alias_index.setdefault(alias.lower(), set()).add(model_id)
    return {"records": records, "aliases": alias_index}


# ── Catalog queries ────────────────────────────────────────────────────

def _model_catalog(category="video"):
    try:
        return _normalize_model_catalog(br_client.list_models(category=category))
    except Exception:
        return {"records": {}, "aliases": {}}


def _resolve_catalog_model(value, catalog):
    matches = catalog.get("aliases", {}).get(str(value or "").strip().lower(), set())
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_MODEL_ALIAS: %s -> %s" % (value, ", ".join(sorted(matches))))
    return next(iter(matches)) if matches else None


def _is_kling_video_model(model):
    if "kling-v3-omni" in str(model or "").lower():
        return True
    record = _model_catalog("video").get("records", {}).get(model) or {}
    return any("kling-v3-omni" in alias.lower() for alias in record.get("aliases", set()))


def _available_models_set(category="video"):
    catalog = _model_catalog(category)
    return {model_id for model_id, record in catalog["records"].items() if record["active"]}


def _model_allow_types(category="video"):
    """Read authoritative allowVideoType from gateway (cached 60s)."""
    catalog = _model_catalog(category)
    return {model_id: record["allow_types"] for model_id, record in catalog["records"].items()
            if record["allow_types"] and not record["conflict"]}


def _model_supports_type(model, video_type):
    """Check if model supports the given videoType."""
    if not video_type:
        return True
    allow = _model_allow_types("video").get(model)
    if allow is not None:
        return int(video_type) in allow
    caps = VIDEO_MODEL_CAPS.get(model)
    if caps is None:
        return False
    return int(video_type) in caps


def _pick_video_model(preferred=None, video_type=None, dialogue=None, formal=False,
                      allow_fallback=True, reference_count=None, exclude_models=None):
    """Pick first available model from VIDEO_MODEL_FALLBACK that supports the type."""
    catalog = _model_catalog("video")
    excluded = {str(item).strip().lower() for item in (exclude_models or [])}
    has_catalog = bool(catalog["records"])
    available = None if has_catalog else _available_models_set("video")
    requested = ([preferred] if preferred else [DEFAULT_MODEL])
    if allow_fallback:
        requested += [item for item in VIDEO_MODEL_FALLBACK if item != preferred]
    ordered = []
    for name in requested:
        canonical = _resolve_catalog_model(name, catalog) or name
        if canonical not in ordered:
            ordered.append(canonical)

    for model_id in ordered:
        if str(model_id).lower() in excluded:
            continue
        record = catalog["records"].get(model_id) if has_catalog else None
        if has_catalog and record is None:
            continue
        if record:
            supports_type = (not video_type or record["allow_types"] is not None and
                             int(video_type) in record["allow_types"])
            supports_reference_count = (
                reference_count is None or record.get("image_count") is None or
                int(reference_count) <= int(record["image_count"]))
            integrated_audio = record["integrated_audio"]
            if integrated_audio is None:
                integrated_audio = VIDEO_MODEL_INTEGRATED_AUDIO.get(model_id)
            eligible = (record["active"] and not record["conflict"] and
                        supports_type and supports_reference_count)
        else:
            eligible = _model_supports_type(model_id, video_type)
            integrated_audio = VIDEO_MODEL_INTEGRATED_AUDIO.get(model_id)
            if available:
                eligible = eligible and model_id in available
        if eligible and not (formal and dialogue and integrated_audio is not True):
            return model_id
    raise ValueError("NO_CAPABLE_VIDEO_MODEL: type=%s integrated_audio=%s" %
                     (video_type, bool(formal and dialogue)))


def _pick_image_model(preferred=None):
    candidates = ([preferred] if preferred else []) + [
        m for m in IMAGE_MODEL_FALLBACK if m != preferred]
    avail = _available_models_set("image")
    if not avail:
        return candidates[0]
    for m in candidates:
        if m in avail:
            return m
    return candidates[0]
