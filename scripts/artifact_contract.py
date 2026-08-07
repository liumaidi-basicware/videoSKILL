#!/usr/bin/env python3
"""Canonical fingerprints shared by storyboard, render and review handoffs."""
import hashlib
import json
import os


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha256_json(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path):
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_record(reference):
    if isinstance(reference, str):
        reference = {"url": reference}
    source = reference.get("url") or reference.get("source")
    record = {
        "id": reference.get("id"),
        "type": reference.get("type"),
        "scope": reference.get("scope"),
        "source": source,
        "sha256": file_sha256(source) if isinstance(source, str) else None,
    }
    return record


def _reference_priority(tag, reference, order):
    ref_type = (reference or {}).get("type") or ""
    type_rank = {
        "product_usage_identity": 0,
        "character_board": 1,
        "actor_board": 1,
        "cast_board": 1,
        "product_board": 2,
        "scene_board": 3,
    }.get(ref_type, 4)
    tag_rank = {
        "@usage": 0,
        "@mina": 1,
        "@host": 1,
        "@product_hero": 2,
        "@product": 2,
        "@product_angle": 3,
    }.get(tag, 4)
    return (min(type_rank, tag_rank), order)


def storyboard_image_input_tags(segment, max_reference_images=3):
    """Pick source tags for one generated panel/keyframe under image limits.

    The storyboard/contact sheet consumes one image slot, so most providers
    leave three tag-bound slots.  Selection is deterministic and audit-friendly:
    product-use relationship anchors win over extra product angles.
    """
    tags = segment.get("ref_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    refs = {item.get("tag"): item
            for item in (segment.get("references") or [])
            if isinstance(item, dict) and item.get("tag") in tags}
    ranked = sorted(
        ((tag, refs.get(tag), index) for index, tag in enumerate(tags)),
        key=lambda item: _reference_priority(item[0], item[1] or {}, item[2]))
    selected = [tag for tag, _ref, _index in ranked[:max_reference_images]]
    selected_set = set(selected)
    omitted = [tag for tag in tags if tag not in selected_set]
    return {"selected": selected, "omitted": omitted, "max_reference_images": max_reference_images}


def build_storyboard_panel_recipe(segment):
    """Build the paid-request identity for an img2img storyboard panel.

    It is computable before the panel exists, so the approved contact sheet and
    exact tag-bound source bytes are part of the formal video handoff.
    """
    tags = segment.get("ref_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    refs = {item.get("tag"): reference_record(item)
            for item in (segment.get("references") or [])
            if isinstance(item, dict) and item.get("tag") in tags}
    budget = storyboard_image_input_tags(segment)
    storyboard_path = segment.get("storyboard_path")
    return {
        "segment_id": segment.get("id"),
        "source_shot_ids": segment.get("source_shot_ids") or [],
        "panel_mode": segment.get("storyboard_panel_mode") or "shot_representative_keyframe",
        "panel_index": segment.get("storyboard_panel_index"),
        "storyboard_path": storyboard_path,
        "storyboard_sha256": file_sha256(storyboard_path),
        "director_text_sha256": sha256_json({
            "text": segment.get("text"),
            "dialogue": segment.get("dialogue"),
            "ref_tags": list(tags),
        }),
        "ref_tags": list(tags),
        "image_input_ref_tags": budget["selected"],
        "omitted_ref_tags": budget["omitted"],
        "image_input_budget": {"storyboard_slots": 1,
                               "reference_slots": budget["max_reference_images"]},
        "references": [refs[tag] for tag in tags if tag in refs],
    }


def sequence_state_fingerprint(value):
    return sha256_json(value or {})


def build_video_handoff(segment):
    """Bind the semantic request and local artifacts handed to video generation."""
    storyboard_path = segment.get("storyboard_path")
    storyboard_sha = file_sha256(storyboard_path)
    # Segments without a storyboard reference (for example draft utility tests)
    # have no storyboard artifact contract. Once a path is declared, however,
    # it must resolve to a real file and never fingerprint as None.
    if storyboard_path and not storyboard_sha:
        raise ValueError("STALE_STORYBOARD_ARTIFACT_MISSING: %s" % storyboard_path)
    payload = {
        "schema_version": 5,
        "client": segment.get("client"),
        "run_id": segment.get("run_id"),
        "segment_id": segment.get("id"),
        "scene_id": segment.get("scene_id"),
        "source_shot_ids": segment.get("source_shot_ids") or [],
        "storyboard": {"path": storyboard_path, "sha256": storyboard_sha},
        "storyboard_panel_index": segment.get("storyboard_panel_index"),
        "ref_tags": segment.get("ref_tags") or [],
        "reference_bindings": segment.get("reference_bindings") or [],
        "storyboard_panel_recipe": (build_storyboard_panel_recipe(segment)
                                    if segment.get("storyboard_ref") else None),
        "storyboard_plan_fingerprint": segment.get("storyboard_plan_fingerprint"),
        "storyboard_result_fingerprint": segment.get("storyboard_result_fingerprint"),
        "storyboard_approval": segment.get("storyboard_approval") or {},
        "references": [reference_record(item) for item in segment.get("references") or segment.get("urls") or []],
        "text": segment.get("text"),
        "dialogue": segment.get("dialogue"),
        "audio_contract": segment.get("audio_contract") or {},
        "timeline": segment.get("timeline") or [],
        "clip_contract": segment.get("clip_contract") or {},
        "sequence_state": segment.get("sequence_state") or {},
        "continuity_graph": segment.get("continuity_graph") or {},
        "chain_contract": segment.get("chain_contract") or {},
        "render_plan": segment.get("render_plan") or {},
        "render_plan_fingerprint": segment.get("render_plan_fingerprint"),
        "duration": segment.get("duration"),
        "ratio": segment.get("ratio"),
        "resolution": segment.get("resolution"),
        "video_type": segment.get("video_type"),
    }
    return {"payload": payload, "fingerprint": sha256_json(payload)}


def build_generation_dependency(predecessor=None, *, tail_path=None,
                                extend_source=None):
    """Bind a chained paid request to the exact predecessor it consumes."""
    predecessor = predecessor or {}
    payload = {
        "predecessor_segment_id": predecessor.get("segment_id"),
        "predecessor_task_id": predecessor.get("taskId") or predecessor.get("task_id"),
        "predecessor_take_fingerprint": predecessor.get("take_fingerprint"),
        "tail_sha256": file_sha256(tail_path),
        "extend_source": extend_source,
    }
    return {"payload": payload, "fingerprint": sha256_json(payload)}


def verify_video_handoff(segment):
    expected = segment.get("video_handoff_fingerprint")
    actual = build_video_handoff(segment)["fingerprint"]
    return {"ok": bool(expected) and expected == actual,
            "expected": expected, "actual": actual,
            "strict": bool(expected)}
