#!/usr/bin/env python3
"""Video task persistence and resume management.

Extracted from video_engine.py (v3 split). Contains:
  - _persist_task (with v3 cost_ledger integration)
  - _record_task_resume
  - _persist_submission_intent
  - _submission_request_id (deterministic request identity)
  - _current_attempt / _completed_task / _task_video_url
  - _manifest_handoff_matches (video handoff verification)

Dependencies: run_manifest, generation_ledger, artifact_contract, br_client, cost_ledger
"""
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_manifest as _rm
import generation_ledger
import artifact_contract
import br_client
import cost_ledger


def _manifest_handoff_matches(manifest, segments):
    """Verify manifest recorded video handoff matches current segments."""
    recorded = (manifest.get("handoffs") or {}).get("video") or {}
    expected = {seg.get("id"): seg.get("video_handoff_fingerprint") for seg in segments}
    if not recorded or not recorded.get("segments"):
        raise ValueError("VIDEO_HANDOFF_REQUIRED: manifest 未记录当前 segments 的 video handoff")
    if recorded.get("segments") != expected:
        raise ValueError("VIDEO_HANDOFF_MISMATCH: manifest recorded video handoff 与当前 segments 不一致")


def _persist_task(manifest, manifest_path, ledger_path, task, status, **fields):
    """Persist task result to manifest + ledger, with auto cost estimation.

    v3: auto-computes cost_estimate for succeeded tasks (fail-safe).
    """
    persisted_video_url = fields.pop("video_url", None)
    if persisted_video_url:
        fields["video_url_sha256"] = hashlib.sha256(
            str(persisted_video_url).encode("utf-8")).hexdigest()
    attempt = int(fields.pop("attempt", None) or task.get("attempt") or 1)
    # v3: auto-compute cost estimate for succeeded tasks
    if status == "succeeded" and "cost_estimate" not in fields:
        try:
            seg = task.get("segment") or {}
            seg_model = task.get("model") or "seedance-2.0"
            dur = seg.get("duration") or seg.get("duration_sec")
            fields["cost_estimate"] = cost_ledger.cost_estimate_for_task(
                seg_model, dur, "video", attempt)
        except Exception:
            pass
    if ledger_path:
        generation_ledger.append_event(ledger_path, "task_%s" % status, stage="video",
                                       unit_id=task.get("segment", {}).get("id"),
                                       task_id=task.get("task_id"), attempt=attempt, **fields)
    if manifest is not None:
        for name in ("request_id", "dependency_fingerprint", "generation_dependency",
                     "supersedes"):
            if task.get(name) is not None and name not in fields:
                fields[name] = task[name]
        _rm.upsert_task(manifest, dict(stage="video", unit_id=task.get("segment", {}).get("id"),
                                      handoff_fingerprint=task.get("handoff_fingerprint"),
                                       task_id=task.get("task_id"), model=task.get("model"),
                                      attempt=attempt, status=status, **fields))
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)


def _record_task_resume(manifest, manifest_path, ledger_path, task):
    """Record that a task is being resumed (for crash recovery)."""
    if ledger_path:
        generation_ledger.append_event(
            ledger_path, "task_resumed", stage="video",
            unit_id=task.get("segment", {}).get("id"), task_id=task.get("task_id"),
            handoff_fingerprint=task.get("handoff_fingerprint"))
    if manifest is not None:
        _rm.upsert_task(manifest, {"stage": "video",
                                   "unit_id": task.get("segment", {}).get("id"),
                                   "handoff_fingerprint": task.get("handoff_fingerprint"),
                                    "task_id": task.get("task_id"), "model": task.get("model"),
                                    "attempt": task.get("attempt", 1),
                                    "status": "running"})
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)


def _submission_request_id(segment, model, video_type, handoff_fingerprint,
                           attempt=1, dependency_fingerprint=None):
    """Deterministic paid-request identity, stable across process restarts."""
    payload = {
        "stage": "video", "unit_id": segment.get("id"),
        "handoff_fingerprint": handoff_fingerprint, "model": model,
        "video_type": int(video_type or 1), "attempt": int(attempt),
        "dependency_fingerprint": dependency_fingerprint,
    }
    return "video-" + artifact_contract.sha256_json(payload)


def _persist_submission_intent(manifest, manifest_path, ledger_path, segment,
                               model, video_type, handoff_fingerprint, attempt=None,
                               dependency_fingerprint=None):
    """Record the intent to submit a video task (for crash recovery dedup)."""
    if attempt is None:
        attempt = _rm.current_video_attempt(manifest or {}, segment.get("id"))
    request_id = _submission_request_id(
        segment, model, video_type, handoff_fingerprint, attempt,
        dependency_fingerprint)
    fields = {"stage": "video", "unit_id": segment.get("id"),
              "handoff_fingerprint": handoff_fingerprint,
              "attempt": attempt, "model": model,
              "dependency_fingerprint": dependency_fingerprint,
              "request_id": request_id, "status": "submitting"}
    if ledger_path:
        generation_ledger.append_event(
            ledger_path, "task_submitting", **fields)
    if manifest is not None:
        _rm.upsert_task(manifest, fields)
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)
    return request_id


def _current_attempt(manifest, segment_id):
    """Get the current attempt number for a segment."""
    if manifest is None:
        return 1
    return _rm.current_video_attempt(manifest, segment_id)


def _completed_task(manifest, segment_id, handoff_fingerprint):
    """Reuse success only inside the currently authorized attempt."""
    if manifest is None:
        return None
    attempt = _current_attempt(manifest, segment_id)
    return next((item for item in reversed(manifest.get("tasks", []))
                 if item.get("stage") == "video" and item.get("unit_id") == segment_id
                 and item.get("handoff_fingerprint") == handoff_fingerprint
                 and int(item.get("attempt", 1)) == attempt
                  and item.get("status") == "succeeded" and item.get("task_id")), None)


def _task_video_url(api_key, task):
    """Refresh the video URL for a completed task (CDN URLs expire)."""
    info = br_client.get_video(api_key, task["task_id"])
    status = str(info.get("status") or "").lower()
    if status not in ("succeeded", "succeed", "success", "completed") or not info.get("videoUrl"):
        raise br_client.BRError("TASK_URL_REFRESH_FAILED: task %s" % task.get("task_id"))
    return info["videoUrl"]
