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
        "schema_version": 4,
        "client": segment.get("client"),
        "run_id": segment.get("run_id"),
        "segment_id": segment.get("id"),
        "scene_id": segment.get("scene_id"),
        "source_shot_ids": segment.get("source_shot_ids") or [],
        "storyboard": {"path": storyboard_path, "sha256": storyboard_sha},
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
