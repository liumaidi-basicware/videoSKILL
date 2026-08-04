#!/usr/bin/env python3
"""Shared duration partitioning for storyboard and video generation."""
from copy import deepcopy

SEEDANCE_MAX_SECONDS = 15

# Per-model maximum generation duration (seconds).
# Verified against BasicRouter /v1/video-models API on 2026-08-04.
# br_client.create_video() validates duration against the API at runtime;
# these values are for pre-splitting in script_splitter.split().
MODEL_MAX_SECONDS = {
    # Seedance family (via Dreamina endpoint)
    "dreamina-seedance-2-0-260128": 15,
    "dreamina-seedance-2-0-fast-260128": 15,
    "dreamina-seedance-2-5-260628": 30,  # offline as of 2026-08-04
    "seedance-1-5-pro-251215": 12,
    # Kling
    "kling-v3-omni": 15,
    "kling-v3": 15,  # offline as of 2026-08-04
    "kling-avatar-image2video": 15,  # offline as of 2026-08-04
    # Wan family
    "wan2.7-i2v": 15,
    "wan2.6-t2v": 15,
    "wan2.6-i2v-flash": 15,
    "wan2.6-r2v-flash": 15,
    "wan2.5-i2v-preview": 12,
    # HappyHorse
    "happyhorse-1.0-t2v": 15,
    "happyhorse-1.0-i2v": 15,
    "happyhorse-1.0-r2v": 15,
    # Google Veo
    "veo-3.1-generate-001": 8,
    "veo-3.1-lite-generate-001": 8,
    # Legacy aliases (mapped by br_client._legacy_video_model_candidates)
    "seedance-2.0": 15,
    "seedance-2.0-fast": 15,
    "kling-v3-omni-video": 15,
}


def max_seconds_for_model(model=None):
    """Return the max generation duration for the given model.

    Uses the actual API modelId when available; falls back to SEEDANCE_MAX_SECONDS.
    br_client.create_video() performs the authoritative runtime validation
    against the gateway's videoDurationMax field.
    """
    if model:
        return MODEL_MAX_SECONDS.get(str(model).lower(), SEEDANCE_MAX_SECONDS)
    return SEEDANCE_MAX_SECONDS


def _duration(shot, minimum=1):
    value = shot.get("duration") or shot.get("seconds") or minimum
    try:
        return max(float(value), float(minimum))
    except (TypeError, ValueError):
        return float(minimum)


def _panel_items(shot):
    value = shot.get("panel_plan") or shot.get("twelve_panel_plan") or []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def partition_shots(shots, max_seconds=SEEDANCE_MAX_SECONDS, scene_aware="auto",
                    preserve_shots=False):
    """Partition shots by scene boundary first, then by model duration limit."""
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    shots = list(shots or [])
    use_scenes = scene_aware is True or (
        scene_aware == "auto" and any(str(s.get("scene_id") or "").strip() for s in shots)
    )
    if preserve_shots:
        result = []
        for shot in shots:
            duration = _duration(shot)
            if duration > max_seconds:
                raise ValueError("SHOT_EXCEEDS_MODEL_LIMIT: %s >%ss" % (duration, max_seconds))
            result.append(deepcopy(shot))
        return result
    result, current, current_seconds, current_scene = [], [], 0.0, None

    def flush():
        nonlocal current, current_seconds, current_scene
        if current:
            result.append(_make_segment(current, len(result) + 1))
            current, current_seconds, current_scene = [], 0.0, None

    for shot in shots:
        scene_id = str(shot.get("scene_id") or "").strip() or None
        if use_scenes and current and scene_id != current_scene:
            flush()
        if not current:
            current_scene = scene_id
        total = _duration(shot)
        remaining, offset, part = total, 0.0, 1
        panel_plan = _panel_items(shot)
        part_count = max(1, int((total + max_seconds - 1e-9) // max_seconds))
        while remaining > 1e-9:
            room = max_seconds - current_seconds
            if room <= 1e-9:
                flush()
                room = max_seconds
            take = min(remaining, room)
            item = deepcopy(shot)
            original_id = str(shot.get("id") or len(result) + len(current) + 1)
            if take < total - 1e-9 or offset > 0:
                item["id"] = "%s_seg%02d" % (original_id, part)
                item["source_shot_id"] = original_id
                item["segment_offset"] = round(offset, 3)
                item["segment_part"] = part
                item["duration"] = round(take, 3)
                if panel_plan:
                    start = int(round((offset / total) * len(panel_plan)))
                    end = int(round(((offset + take) / total) * len(panel_plan)))
                    local = panel_plan[start:max(end, start + 1)]
                    item["panel_plan"] = (local * 12)[:12]
                    item["panel_plan"] += ["continuity beat %d/%d" % (n + 1, part_count)
                                            for n in range(12 - len(item["panel_plan"]))]
            current.append(item)
            current_seconds += take
            remaining -= take
            offset += take
            part += 1
            if current_seconds >= max_seconds - 1e-9:
                flush()
    flush()
    return result


def _make_segment(items, index):
    if len(items) == 1:
        return items[0]
    first = deepcopy(items[0])
    first["id"] = "segment_%02d" % index
    first["source_shot_ids"] = list(dict.fromkeys(
        str(item.get("source_shot_id") or item.get("id") or "") for item in items))
    first["duration"] = round(sum(_duration(item) for item in items), 3)
    first["segment_index"] = index
    first["timeline"] = _merge_timeline(items)
    panels = [panel for item in items for panel in _panel_items(item)]
    if panels:
        first["panel_plan"] = panels[:12]
    first["dialogue"] = "\n".join(
        str(item.get("dialogue") or item.get("voiceover") or "").strip()
        for item in items if item.get("dialogue") or item.get("voiceover"))
    first["visual"] = "\n".join(
        str(item.get("visual") or item.get("scene_prompt") or "").strip()
        for item in items if item.get("visual") or item.get("scene_prompt"))
    for key in ("asset_refs", "characters", "motion_elements", "product_refs",
                "prop_prompts"):
        first[key] = _merge_values(items, key)
    return first


def _merge_values(items, key):
    """Merge list/dict metadata without dropping later shots in an aggregate."""
    values = [item.get(key) for item in items if item.get(key) not in (None, "", [], {})]
    if not values:
        return {} if key == "asset_refs" else []
    if any(isinstance(value, dict) for value in values):
        merged = {}
        for value in values:
            if not isinstance(value, dict):
                continue
            for subkey, subvalue in value.items():
                incoming = subvalue if isinstance(subvalue, list) else [subvalue]
                current = merged.setdefault(subkey, [])
                for item in incoming:
                    if item not in current:
                        current.append(deepcopy(item))
        return merged
    merged = []
    for value in values:
        incoming = value if isinstance(value, (list, tuple)) else [value]
        for item in incoming:
            if item not in merged:
                merged.append(deepcopy(item))
    return merged


def _merge_timeline(items):
    timeline, cursor = [], 0.0
    for item in items:
        duration = _duration(item)
        source = item.get("timeline") or [{
            "start": 0, "end": duration,
            "action": item.get("visual") or item.get("scene_prompt"),
            "camera": item.get("camera_movement") or item.get("camera")
        }]
        for beat in source:
            beat = deepcopy(beat)
            start = max(0.0, min(duration, float(beat.get("start", 0))))
            end = max(start, min(duration, float(beat.get("end", duration))))
            beat["start"] = round(cursor + start, 3)
            beat["end"] = round(cursor + end, 3)
            beat.setdefault("source_shot_id", item.get("source_shot_id") or item.get("id"))
            if item.get("scene_id"):
                beat.setdefault("scene_id", item.get("scene_id"))
            timeline.append(beat)
        cursor += duration
    return timeline
