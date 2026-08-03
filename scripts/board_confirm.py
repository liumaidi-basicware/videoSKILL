#!/usr/bin/env python3
"""Storyboard and cast-board confirmation and fingerprint binding.

Extracted from storyboard.py (v3 split). Contains:
  - confirm_board (confirm one board, bind to source fingerprint)
  - confirm_storyboard (confirm all storyboards in a run)
  - _approval_current (check if approval is still valid)
  - storyboard_approval_is_current (top-level check)
  - _source_refs_fingerprint (hash reference content for approval binding)
  - _file_sha256 (file content hash)

Dependencies: None (pure stdlib + hashlib)
"""
import os
import sys
import json
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ROOT = os.path.dirname(HERE)


def _file_sha256(path):
    """Compute sha256 of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _approval_path(out_dir, kind):
    """Return the path to the approval marker file."""
    return os.path.join(out_dir, ".%s_confirmed.json" % kind)


def _source_refs_fingerprint(refs):
    """Hash reference contents so approvals expire when an upload changes."""
    digest = hashlib.sha256()
    for value in refs or []:
        digest.update(str(value).encode("utf-8"))
        path = value if isinstance(value, str) and os.path.isabs(value) else os.path.join(ROOT, value) if isinstance(value, str) else None
        if path and os.path.isfile(path):
            with open(path, "rb") as handle:
                digest.update(handle.read())
        digest.update(b"\0")
    return digest.hexdigest()


def _approval_current(out_dir, kind, source_fingerprint):
    """Check if an approval marker is still valid (fingerprint + file integrity)."""
    try:
        with open(_approval_path(out_dir, kind), encoding="utf-8") as handle:
            record = json.load(handle)
        if (record.get("status") != "confirmed" or
                record.get("source_fingerprint") != source_fingerprint):
            return False
        board_path = record.get("path")
        board_sha256 = record.get("board_sha256")
        if board_sha256:
            return bool(board_path and os.path.isfile(board_path) and
                        _file_sha256(board_path) == board_sha256)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def confirm_board(result_json, kind):
    """Confirm one generated board and bind approval to its source fingerprint.

    Reads the result JSON, extracts the board path and source fingerprint,
    and writes an approval marker file. The approval is invalidated if:
      - The source plan/asset changes (fingerprint mismatch)
      - The board file is modified or deleted (sha256 mismatch)
    """
    with open(result_json, encoding="utf-8") as handle:
        results = json.load(handle)

    out_dir = os.path.dirname(os.path.abspath(result_json))
    board_path = results.get("path") or results.get("image_path")
    source_fp = results.get("source_fingerprint") or results.get("plan_fingerprint")

    if not board_path or not os.path.isfile(board_path):
        raise ValueError("CONFIRM_BOARD_FAILED: board file not found at %s" % board_path)

    board_sha = _file_sha256(board_path)
    approval = {
        "status": "confirmed",
        "kind": kind,
        "path": board_path,
        "board_sha256": board_sha,
        "source_fingerprint": source_fp,
        "confirmed_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }

    approval_path = _approval_path(out_dir, kind)
    with open(approval_path, "w", encoding="utf-8") as f:
        json.dump(approval, f, indent=2, ensure_ascii=False)

    return approval


def confirm_storyboard(result_json):
    """Confirm all storyboards in a run directory.

    This is the gate between storyboard generation and video generation.
    Once confirmed, the storyboard fingerprints are bound to the video
    handoff — any change to the storyboard invalidates the video approval.
    """
    with open(result_json, encoding="utf-8") as handle:
        results = json.load(handle)

    out_dir = os.path.dirname(os.path.abspath(result_json))
    plan_fp = results.get("plan_fingerprint")
    shots = results.get("shots") or results.get("results") or []

    if not shots:
        raise ValueError("CONFIRM_STORYBOARD_FAILED: no shots in result")

    # Verify all shot files exist
    for shot in shots:
        path = shot.get("path") or shot.get("image_path")
        if not path or not os.path.isfile(path):
            raise ValueError("CONFIRM_STORYBOARD_FAILED: shot file missing: %s" % path)

    # Build approval record
    approval = {
        "status": "confirmed",
        "kind": "storyboard",
        "plan_fingerprint": plan_fp,
        "shot_count": len(shots),
        "shots": [
            {
                "id": s.get("id"),
                "path": s.get("path") or s.get("image_path"),
                "sha256": _file_sha256(s.get("path") or s.get("image_path")),
            }
            for s in shots
        ],
        "confirmed_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }

    approval_path = _approval_path(out_dir, "storyboard")
    with open(approval_path, "w", encoding="utf-8") as f:
        json.dump(approval, f, indent=2, ensure_ascii=False)

    return approval


def storyboard_approval_is_current(result_json, *, client=None, run_id=None,
                                    plan_fingerprint=None):
    """Check if the storyboard approval is still valid.

    Returns True only if:
      1. An approval marker exists
      2. The marker's plan_fingerprint matches the current plan
      3. All shot files still exist with matching sha256
    """
    with open(result_json, encoding="utf-8") as handle:
        results = json.load(handle)

    out_dir = os.path.dirname(os.path.abspath(result_json))
    current_fp = plan_fingerprint or results.get("plan_fingerprint")

    if not _approval_current(out_dir, "storyboard", current_fp):
        return False

    # Also verify each shot file
    approval_path = _approval_path(out_dir, "storyboard")
    try:
        with open(approval_path, encoding="utf-8") as f:
            approval = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    for shot in approval.get("shots", []):
        path = shot.get("path")
        sha = shot.get("sha256")
        if not path or not os.path.isfile(path):
            return False
        if sha and _file_sha256(path) != sha:
            return False

    return True
