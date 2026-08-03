#!/usr/bin/env python3
"""Storyboard plan normalization and fingerprinting.

Extracted from storyboard.py (v3 split). Contains:
  - normalize_panel_plan (canonical 12-panel plan)
  - plan_fingerprint (stable plan identity)
  - canonical_storyboard_plan (full normalization pipeline)
  - load_plan_json (error-localized JSON reader)
  - DEFAULT_PANEL_PLAN constant
  - safe_name, client_slug, run_output_dir helpers

Dependencies: storyboard_validator, br_client (for BRError)
"""
import os
import sys
import json
import hashlib
import re

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import br_client

ROOT = os.path.dirname(HERE)

DEFAULT_PANEL_PLAN = [
    "1 establish", "2 kinetic entry", "3 ritual gesture",
    "4 lateral profile", "5 handheld push", "6 overhead orbit",
    "7 aggressive close-up", "8 long-lens compression",
    "9 fabric/light release", "10 camera wrap",
    "11 emotional payoff", "12 unresolved hold",
]

SEEDANCE_MAX_SECONDS = 8  # seedance single-segment duration limit


def safe_name(value):
    """Sanitize a string for use as a directory or file name."""
    s = re.sub(r"[^A-Za-z0-9_\-.]", "_", str(value or ""))
    return s.strip("_") or "shot"


def client_slug(plan):
    """Stable short slug for run directories."""
    for key in ("client", "client_name", "brand", "brand_name", "project_title"):
        v = plan.get(key)
        if v:
            return safe_name(v).lower()[:48]
    return "client"


def run_output_dir(base_out_dir, plan, run_id=None, flat=False):
    """Return the actual output directory for a storyboard run."""
    if flat:
        return base_out_dir
    if run_id:
        rid = safe_name(run_id)
    else:
        fingerprint = plan_fingerprint(plan)
        rid = "%s_%s" % (client_slug(plan), fingerprint)
    return os.path.join(base_out_dir, rid)


def normalize_panel_plan(shot):
    """Return the canonical 12-panel plan."""
    raw = shot.get("panel_plan") or shot.get("twelve_panel_plan")
    if isinstance(raw, str):
        raw = [raw]
    if not raw:
        return list(DEFAULT_PANEL_PLAN)
    return list(raw)


def canonical_storyboard_plan(plan):
    """Return the exact normalized plan used by storyboard and video handoff."""
    from storyboard_validator import normalize_plan_motion_elements
    canonical = json.loads(json.dumps(plan, ensure_ascii=False))
    canonical, _ = normalize_plan_motion_elements(canonical)
    canonical = _hydrate_plan_asset_refs(canonical)
    continuity = canonical.get("continuity_contract") or {}
    if continuity:
        for shot in canonical.get("shots") or []:
            shot.setdefault("scene_id", continuity.get("scene_id"))
            lock = " CONTINUITY LOCK: %s. %s. %s. %s." % (
                continuity.get("background", "same background"),
                continuity.get("lighting", "same lighting"),
                continuity.get("host_position", "same host position"),
                continuity.get("product_state", "same product state"))
            shot["scene_prompt"] = (str(shot.get("scene_prompt") or "") + lock).strip()
            shot["continuity_in"] = shot.get("continuity_in") or continuity.get("transition")
    canonical["shots"] = partition_shots(
        canonical.get("shots") or [], max_seconds=SEEDANCE_MAX_SECONDS,
        preserve_shots=str(canonical.get("scene_type") or "").lower() in
        {"oral-broadcast", "oralbroadcast", "broadcast", "口播", "普通口播"})
    return canonical


def plan_fingerprint(plan):
    """Stable identity for a canonical storyboard plan."""
    plan = canonical_storyboard_plan(plan)
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _hydrate_plan_asset_refs(plan):
    """Inject resolved asset paths into the plan (for img2img reference)."""
    # This is a complex function that resolves product/cast image paths
    # from the assets directory into the plan's shots.
    # Full implementation remains in storyboard.py for now.
    return plan


def partition_shots(shots, max_seconds=8, preserve_shots=False):
    """Split long-duration shots to fit within model's per-segment limit."""
    if preserve_shots:
        return shots
    result = []
    for shot in shots:
        dur = float(shot.get("duration", 5))
        if dur <= max_seconds:
            result.append(shot)
            continue
        # Split into sub-shots
        remaining = dur
        idx = 1
        while remaining > 0:
            chunk = dict(shot)
            chunk["id"] = "%s_p%d" % (shot.get("id", "shot"), idx)
            chunk["duration"] = min(remaining, max_seconds)
            chunk["parent_shot_id"] = shot.get("id")
            result.append(chunk)
            remaining -= max_seconds
            idx += 1
    return result


def load_plan_json(plan_path):
    """Read and parse storyboard_plan.json with human-readable error location."""
    if not os.path.exists(plan_path):
        raise br_client.BRError(
            "剧本文件不存在：%s。请先把定稿剧本解析成 storyboard_plan.json 再运行。" % plan_path
        )
    with open(plan_path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        lines = raw.split("\n")
        bad_line = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else ""
        pointer = " " * max(e.colno - 1, 0) + "^"
        prev_line = lines[e.lineno - 2] if e.lineno >= 2 else ""
        context = ""
        if prev_line:
            context = "第 %d 行（上一行，供比对）: %s\n" % (e.lineno - 1, prev_line)
        raise br_client.BRError(
            "storyboard_plan.json 第 %d 行第 %d 列有 JSON 语法错误：%s\n"
            "%s第 %d 行: %s\n      %s\n"
            "常见原因：字符串少了收尾引号、对象/数组里多了或少了逗号、中文引号混入了英文 JSON。"
            "修好这一行后重新运行 storyboard.py。"
            % (e.lineno, e.colno, e.msg, context, e.lineno, bad_line, pointer)
        ) from e
