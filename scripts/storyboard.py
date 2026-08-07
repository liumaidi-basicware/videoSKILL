#!/usr/bin/env python3
"""Storyboard / cast-board generator using BasicRouter image API (gpt-image-2).

Workflow position:
  finalized script JSON -> storyboard_plan.json -> gpt-image-2 storyboards/cast board
  -> customer confirmation -> video_engine.py renders final video.

The local host agent is responsible for parsing the approved script into a
structured plan. This script only turns that plan into preview images and an
index markdown file so the customer can SEE and confirm before Credits are spent
on video.

Input schema (JSON):
{
  "project_title": "Client Brand 15s promo",
  "client": "acme",
   "aspect_ratio": "16:9",
  "visual_style": "clean premium commercial, brand primary-color accents",
  "continuity": {
    "background": "same bright studio / same store scene across stitched segments",
    "character_identity": "same face, hairstyle, wardrobe and posture language",
    "wardrobe": "same outfit and accessories",
    "voice": "same speaker / voice tone / language",
    "bgm": "same music cue, tempo and mood",
    "lighting": "same key-light direction and color temperature"
  },
  "characters": [
    {"id":"host", "name":"粤语女主持", "role":"host", "appearance":"...",
     "costume":"...", "personality":"...", "voice":"..."}
  ],
  "shots": [
     {"id":"s1", "duration":3, "dialogue":"...", "visual":"...",
     "camera":"left-front 45° medium shot, slow push-in", "shot_size":"medium close-up",
     "camera_movement":"slow push-in", "angle_offset":"left-front 45 degrees",
     "composition":"rule of thirds, product on lower-right intersection",
     "lighting":"soft key light from camera-left, warm rim light",
     "character_action":"host picks up product and turns it toward camera",
     "micro_expression":"warm confident smile grows as benefit is revealed",
     "scene_prompt":"premium studio, warm neutral background, clean display table",
     "character_prompt":"same face, same hair, same white blazer, moderate gestures, direct-to-lens trust-building",
     "prop_prompts":["product: stable real shape, color, logo position and material"],
      "panel_plan":["1 establish","2 kinetic entry","3 ritual gesture","4 lateral profile","5 handheld push","6 overhead orbit","7 aggressive close-up","8 long-lens compression","9 fabric/light release","10 camera wrap","11 emotional payoff","12 unresolved hold"],
     "audio":{"voice":"same calm Cantonese host voice", "bgm":"subtle upbeat premium BGM", "sfx":"soft whoosh on transition"},
     "characters":["host"], "props":"client product"}
  ]
}

Outputs:
  output/storyboard/<run-id>/cast_board.jpg              # character reference sheet: six required views per character
   output/storyboard/<run-id>/shot_01_s1.jpg ...          # one 16:9 cinematic storyboard per segment (panel count = script-driven shot beats, default grid 4x3)
  output/storyboard/<run-id>/storyboard_index.md         # markdown gallery with absolute paths
  output/storyboard/<run-id>/storyboard_embedded.md      # markdown gallery with embedded image data
  output/storyboard/<run-id>/storyboard_preview.html     # self-contained preview page
  output/storyboard/<run-id>/storyboard_result.json      # machine-readable result
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from project_utils import require_contained_path  # noqa: E402
import br_client as _br  # noqa: E402
import br_client  # noqa: E402
import asset_prep  # noqa: E402
import artifact_contract  # noqa: E402
import key_setup  # noqa: E402
import ux  # noqa: E402
from image_utils import image_type  # noqa: E402
from video_segmentation import partition_shots, SEEDANCE_MAX_SECONDS  # noqa: E402
from board_confirm import _source_refs_fingerprint  # noqa: E402 — v4 shim


DEFAULT_MODEL = "gpt-image-2"
IMAGE_MAX_WAIT = 900
IMAGE_RETRIES = 2
TAG_PATTERN = re.compile(r"^@[a-z][a-z0-9_]*$")
SHOT_REFERENCE_POLICY_VERSION = "shot-reference-policy-v3-no-character-lock"
PRODUCT_USAGE_POLICY_VERSION = "product-usage-policy-v5-physical-relation-contract"
DEFAULT_PANEL_PLAN = [
    "establishing wide shot",
    "kinetic entry",
    "ritual gesture",
    "lateral profile",
    "handheld push-in",
    "overhead orbit",
    "aggressive close-up",
    "long-lens compression",
    "fabric or light release",
    "camera wrap",
    "emotional payoff",
    "unresolved final hold",
]


def safe_name(s):
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s or ""))
    return s.strip("_") or "shot"


def ratio_to_image_ratio(video_ratio):
    """BasicRouter image ratio uses the same style strings; keep common values."""
    if video_ratio in ("9:16", "16:9", "1:1", "4:3", "3:4"):
        return video_ratio
    return "9:16"


def normalize_panel_plan(shot):
    """Return the shot's panel plan as-is (script-driven panel count).

    2026-08-05 revision: panel count is no longer forced to 12. The script
    co-creation stage decides how many panels/beats a shot needs (see
    AGENTS.md rule #4); this function only supplies the legacy 12-panel
    default when a shot omits panel_plan entirely (e.g. old plans/tests).
    """
    raw = shot.get("panel_plan") or shot.get("twelve_panel_plan")
    if isinstance(raw, str):
        raw = [raw]
    if not raw:
        return list(DEFAULT_PANEL_PLAN)
    return list(raw)


def _grid_for_panel_count(panel_count, portrait):
    """Pick a (cols, rows) grid that fits panel_count panels with minimal empty cells.

    Landscape (16:9) storyboards favour more columns than rows; portrait
    (9:16) storyboards favour more rows than columns. 12 remains a valid
    value (falls through to 4x3/3x4 as before) but is no longer the only one.
    """
    common_landscape = {
        1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2),
        7: (4, 2), 8: (4, 2), 9: (3, 3), 10: (4, 3), 11: (4, 3), 12: (4, 3),
    }
    cols, rows = common_landscape.get(panel_count, (4, 3))
    if panel_count > 12:
        cols = math.ceil(math.sqrt(panel_count * 4 / 3))
        rows = math.ceil(panel_count / cols)
    if portrait:
        cols, rows = rows, cols
    return cols, rows


# Category -> stable base @tag. Mirrors script_splitter._REFERENCE_META so the
# same asset gets the same @tag both in the storyboard preview prompt and in
# the later per-shot video submission prompt (script_splitter.py).
_ASSET_BASE_TAG = {
    "product_images": "@product",
    "product_usage_images": "@usage",
    "scene_images": "@scene",
    "digital_human_portraits": "@host",
}


def _assign_asset_tags(product_imgs, product_usage_imgs, scene_imgs, portrait_imgs):
    """Return [(tag, url, category_key), ...] preserving category order.

    Same-category duplicates get a numeric suffix (@host, @host2, @host3...),
    matching script_splitter._next_tag numbering so tags stay stable when the
    plan later flows into script_splitter.py for video submission.
    """
    buckets = [
        ("product_images", product_imgs),
        ("product_usage_images", product_usage_imgs),
        ("scene_images", scene_imgs),
        ("digital_human_portraits", portrait_imgs),
    ]
    tagged = []
    for category, urls in buckets:
        base = _ASSET_BASE_TAG[category]
        for i, url in enumerate(urls or []):
            tag = base if i == 0 else "%s%d" % (base, i + 1)
            tagged.append((tag, url, category))
    return tagged


def client_slug(plan):
    """Stable short slug for run directories; keeps clients/sessions separated."""
    for key in ("client", "client_name", "brand", "brand_name", "project_title"):
        v = plan.get(key)
        if v:
            return safe_name(v).lower()[:48]
    return "client"


def run_output_dir(base_out_dir, plan, run_id=None, flat=False):
    """Return the actual output directory.

    Best practice for any host agent is one immutable directory per generated
    storyboard session, so reruns never overwrite earlier customer-approved
    preview boards. Passing --flat preserves the legacy behavior for debugging.
    """
    if flat:
        return base_out_dir
    if run_id:
        rid = safe_name(run_id)
    else:
        # A timestamp creates a new empty run on every retry, defeating the
        # progress file. Derive the default from the approved plan instead so
        # an interrupted invocation automatically resumes the same run. A plan
        # edit intentionally produces a new directory.
        fingerprint = plan_fingerprint(plan)
        rid = "%s_%s" % (client_slug(plan), fingerprint)
    return os.path.join(base_out_dir, rid)


def plan_fingerprint(plan):
    """Stable identity for a canonical storyboard plan.

    Callers may provide the authored, normalized, hydrated, or duration-
    partitioned representation. Canonicalization is idempotent, so every stage
    binds approval to the same semantic plan identity.

    Runtime fields injected by prompt review are excluded: they are generation
    inputs, not authored storyboard_plan.json identity. Otherwise producers and
    consumers can disagree about the current revision for the same logical
    run_id.
    """
    plan = canonical_storyboard_plan(plan)
    plan.pop("_asset_composition_briefs", None)
    for shot in (plan.get("shots") or []):
        shot.pop("approved_prompt_zh", None)
        shot.pop("approved_submission_prompt_zh", None)
        shot.pop("references", None)
        if shot.get("panel_plan") == DEFAULT_PANEL_PLAN and not shot.get("twelve_panel_plan"):
            shot.pop("panel_plan", None)
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def _digest_plan_for_prompt_review(plan):
    """Use the same full-plan identity as prompt_review without importing it.

    prompt_review.polish stores storyboard.plan_fingerprint(plan) after
    calling expand_product_sku_refs(canonical_storyboard_plan(plan)).
    Mirror that exactly so the confirmation gate matches.
    """
    canonical = expand_product_sku_refs(canonical_storyboard_plan(plan))
    return plan_fingerprint(canonical)


def visual_plan_fingerprint(plan):
    """Fingerprint of the plan's visual fields, excluding dialogue/voiceover/audio.

    Storyboard image generation (shot_prompt) contains no dialogue — spoken
    lines only appear in the customer-facing index/preview text. A dialogue-only
    edit therefore cannot change the storyboard image, so the storyboard
    prompt-review gate keys off this visual fingerprint instead of the full
    plan_fingerprint. The full plan_fingerprint still gates run directory
    identity and video handoff.

    Canonicalization happens exactly once here (expand + canonical). Callers
    must NOT pre-canonicalize the plan, because partition_shots is not
    idempotent once dialogue has been split — a second canonical pass would
    re-split the already-split dialogue and make the fingerprint drift on
    dialogue-only edits, defeating the whole purpose of this gate.
    """
    plan = expand_product_sku_refs(canonical_storyboard_plan(plan))
    plan.pop("_asset_composition_briefs", None)
    for shot in (plan.get("shots") or []):
        shot.pop("approved_prompt_zh", None)
        shot.pop("approved_submission_prompt_zh", None)
        shot.pop("references", None)
        if shot.get("panel_plan") == DEFAULT_PANEL_PLAN and not shot.get("twelve_panel_plan"):
            shot.pop("panel_plan", None)
        shot.pop("dialogue", None)
        shot.pop("voiceover", None)
        shot.pop("audio", None)
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def shot_fingerprint(shot):
    """Return the identity of one storyboard panel's visual contract.

    Dialogue and audio are intentionally excluded: neither changes the approved
    composition image.  This lets downstream handoff invalidate only the panel
    whose visual direction changed instead of treating an unrelated line edit as
    a reason to discard every confirmed storyboard panel.
    """
    def normalize(value):
        if isinstance(value, dict):
            ignored = {
                "dialogue", "voiceover", "audio", "approved_prompt_zh",
                "approved_submission_prompt_zh", "references", "panel_plan",
                "motion_elements",
            }
            return {key: normalize(item) for key, item in value.items()
                    if key not in ignored}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            text = value.replace("字幕", "")
            for char in (" ", "\t", "\n", "。", ".", "，", ",", "；", ";", "、", "：", ":"):
                text = text.replace(char, "")
            return text
        return value
    visual = normalize(json.loads(json.dumps(shot or {}, ensure_ascii=False)))
    return hashlib.sha256(
        json.dumps(visual, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def reference_fingerprint(registry):
    def ref_identity(item):
        url = str(item.get("url") or "")
        identity = {"tag": item.get("tag"), "url": url, "type": item.get("type")}
        if url and not re.match(r"^(https?|data):", url) and os.path.isfile(url):
            identity["sha256"] = _file_sha256(url)
        return identity

    payload = {
        "policy": SHOT_REFERENCE_POLICY_VERSION,
        "refs": [ref_identity(item) for item in (registry or [])],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def _digest_visual_plan_for_review(plan):
    """Visual-only fingerprint mirroring prompt_review's stored value."""
    return visual_plan_fingerprint(plan)


def _load_prompt_review_for_shots(path, plan):
    if not path:
        raise br_client.BRError(
            "PROMPT_REVIEW_REQUIRED: 正式生图前必须先生成并确认中文提示词审核文件。")
    if not os.path.isfile(path):
        raise br_client.BRError(
            "PROMPT_REVIEW_REQUIRED: 中文提示词审核文件不存在，请先运行 prompt_review.py polish。")
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    if review.get("status") != "confirmed":
        raise br_client.BRError(
            "PROMPT_REVIEW_REQUIRED: 提示词审核文件未确认（status=%s）。"
            "请运行 prompt_review.py confirm 确认后再生成。" % review.get("status"))
    if review.get("stage") != "storyboard":
        raise br_client.BRError(
            "PROMPT_REVIEW_REQUIRED: 提示词审核文件阶段不匹配（stage=%s，期望 storyboard）。"
            "请重新运行 prompt_review.py polish --stage storyboard。" % review.get("stage"))
    expected_visual_fp = _digest_visual_plan_for_review(plan)
    review_visual_fp = review.get("visual_plan_fingerprint")
    if review_visual_fp is not None:
        # New-style review carries a visual fingerprint; only visual changes
        # (visual/camera/action/panel_plan/...) force re-polish. Dialogue-only
        # edits keep the approved review valid because shot_prompt contains no
        # dialogue — the storyboard image is unchanged.
        if review_visual_fp != expected_visual_fp:
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 提示词审核的画面指纹与当前计划不匹配\n"
                "  审核画面指纹: %s\n  当前画面指纹: %s\n"
                "  原因：计划的画面相关字段（visual/camera/动作/分镜等）在审核后被修改过。"
                "请重新运行 prompt_review.py polish + confirm。"
                % (review_visual_fp, expected_visual_fp))
    else:
        # Legacy review file (pre visual_plan_fingerprint): fall back to the
        # strict full-plan fingerprint to preserve existing behavior.
        expected_fp = _digest_plan_for_prompt_review(plan)
        if review.get("plan_fingerprint") != expected_fp:
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 提示词审核的指纹与当前计划不匹配\n"
                "  审核文件指纹: %s\n  当前计划指纹: %s\n"
                "  原因：计划在审核确认后被修改过。请重新运行 prompt_review.py polish + confirm。"
                % (review.get("plan_fingerprint"), expected_fp))
    prompts = {str(item.get("shot_id")): item
               for item in review.get("prompts") or []}
    expected_assets = {
        item.get("asset_id"): item
        for item in asset_prompt_review_items(plan)
        if item.get("asset_id")
    }
    reviewed_assets = {
        item.get("asset_id"): item
        for item in (review.get("asset_prompts") or [])
        if isinstance(item, dict) and item.get("asset_id")
    }
    for asset_id, expected in expected_assets.items():
        reviewed = reviewed_assets.get(asset_id) or {}
        if not reviewed.get("submission_prompt_zh"):
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 缺少资产级提示词 %s。"
                "请重新运行 prompt_review.py capture-storyboard + confirm。"
                % asset_id)
        if reviewed.get("prompt_fingerprint") != expected.get("prompt_fingerprint"):
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 资产级提示词 %s 已过期，产品身份/使用关系合同"
                "或生成策略已变化。请重新运行 prompt_review.py capture-storyboard + confirm。"
                % asset_id)
    composition_briefs = {
        asset_id: item.get("composition_brief")
        for asset_id, item in reviewed_assets.items()
        if isinstance(item, dict) and item.get("composition_brief")
    }
    if composition_briefs:
        plan["_asset_composition_briefs"] = composition_briefs
    global_refs = [ref for ref in (plan.get("references") or []) if isinstance(ref, dict)]
    for shot in plan.get("shots") or []:
        item = prompts.get(str(shot.get("id"))) or {}
        if not (item.get("prompt_zh") or item.get("submission_prompt_zh")):
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 缺少镜头 %s 的确认提示词。"
                "请重新运行 prompt_review.py polish + confirm。" % shot.get("id"))
        if item.get("prompt_zh"):
            shot["approved_prompt_zh"] = item["prompt_zh"]
        if item.get("submission_prompt_zh"):
            shot["approved_submission_prompt_zh"] = item["submission_prompt_zh"]
        if not (shot.get("panel_plan") or shot.get("twelve_panel_plan")):
            # Legacy and co-created plans can arrive with the panel detail
            # embedded in the approved prompt rather than as a structured field.
            # Fill the runtime contract after the review fingerprint check so
            # old confirmed reviews stay valid while generation preflight still
            # sees an explicit panel plan.
            shot["panel_plan"] = normalize_panel_plan(shot)
        if global_refs and not shot.get("references"):
            wanted = shot.get("ref_tags") or []
            if isinstance(wanted, str):
                wanted = [wanted]
            wanted = set(wanted)
            selected = [ref for ref in global_refs
                        if not wanted or ref.get("tag") in wanted]
            if selected:
                shot["references"] = json.loads(
                    json.dumps(selected, ensure_ascii=False))


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
    shots = canonical.get("shots") or []
    preserve = str(canonical.get("scene_type") or "").lower() in {
        "oral-broadcast", "oralbroadcast", "broadcast", "口播", "普通口播"}
    # Storyboard contact sheets are approved by the customer at the script-shot
    # level. Do not merge adjacent short shots here; video segmentation can
    # still aggregate later when preparing model submissions. Only split when a
    # single shot exceeds the model duration limit.
    if not preserve:
        preserve = all(float(shot.get("duration") or shot.get("seconds") or 1)
                       <= SEEDANCE_MAX_SECONDS for shot in shots)
    canonical["shots"] = partition_shots(
        shots, max_seconds=SEEDANCE_MAX_SECONDS, preserve_shots=preserve)
    # A shot is a storyboard panel and a video generation unit. Persist its
    # position in the client-approved contact sheet so later img2img expansion
    # can identify it without relying on filename order or a separate timeline.
    for index, shot in enumerate(canonical["shots"], 1):
        shot["panel_index"] = index
    return canonical


def _read_result(path):
    """Read a checkpoint without making a damaged/legacy file fatal."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _legacy_plan_matches(plan, previous):
    """Best-effort compatibility check for checkpoints created before fingerprints."""
    old_shots = previous.get("shots") or []
    new_shots = plan.get("shots") or []
    if len(old_shots) > len(new_shots):
        return False
    current_by_id = {str(s.get("id")): s for s in new_shots}
    for item in old_shots:
        old_shot = item.get("shot") or {}
        if current_by_id.get(str(old_shot.get("id"))) != old_shot:
            return False
    return True


def resolve_run_output_dir(base_out_dir, plan, run_id=None, flat=False):
    """Resolve resume versus revision without overwriting an approved preview."""
    target = run_output_dir(base_out_dir, plan, run_id=run_id, flat=flat)
    if flat or not run_id:
        return target
    fingerprint = plan_fingerprint(plan)
    previous = _read_result(os.path.join(target, "storyboard_result.json"))
    if not previous:
        return target
    if (previous.get("plan_fingerprint") == fingerprint or
            (not previous.get("plan_fingerprint") and _legacy_plan_matches(plan, previous))):
        return target
    # Panel-scoped checkpoints may safely stay in the same run directory: each
    # rendered panel carries its own visual fingerprint and stale panels are
    # regenerated independently below. Legacy checkpoints still revision.
    previous_shots = previous.get("shots") or []
    if previous_shots and all(item.get("shot_fingerprint") for item in previous_shots):
        return target
    revision = 2
    while True:
        candidate = os.path.join(target, "..", "%s__r%02d" % (safe_name(run_id), revision))
        candidate = os.path.abspath(candidate)
        candidate_result = _read_result(os.path.join(candidate, "storyboard_result.json"))
        if candidate_result and candidate_result.get("plan_fingerprint") == fingerprint:
            return candidate
        if not os.path.exists(candidate):
            return candidate
        revision += 1


def _run_pointer_path(base_out_dir, run_id):
    if not run_id:
        return None
    return os.path.join(os.path.abspath(base_out_dir),
                        ".%s_current.json" % safe_name(run_id))


def _write_run_pointer(base_out_dir, run_id, plan, out_dir, result_path, *, stage=None,
                       plan_fingerprint_value=None,
                       visual_plan_fingerprint_value=None):
    """Record the authoritative storyboard revision for a logical run_id.

    A run_id is a logical workflow identity, not necessarily the filesystem
    directory. When the plan changes, storyboard.py may write to
    ``<run_id>__rNN``. This pointer prevents later tools from silently reading
    the stale base directory.
    """
    pointer_path = _run_pointer_path(base_out_dir, run_id)
    if not pointer_path:
        return None
    record = {
        "schema_version": 1,
        "run_id": safe_name(run_id),
        "client": plan.get("client") or client_slug(plan),
        "plan_fingerprint": plan_fingerprint_value or plan_fingerprint(plan),
        "visual_plan_fingerprint": (visual_plan_fingerprint_value or
                                    visual_plan_fingerprint(plan)),
        "out_dir": os.path.abspath(out_dir),
        "result_json": os.path.abspath(result_path),
        "stage": stage,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp_path = pointer_path + ".tmp"
    Path(tmp_path).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, pointer_path)
    return pointer_path


def resolve_current_storyboard_dir(base_out_dir, run_id, plan=None):
    """Return the current revision directory recorded for a run_id, if valid."""
    pointer_path = _run_pointer_path(base_out_dir, run_id)
    if not pointer_path or not os.path.isfile(pointer_path):
        return None
    try:
        record = json.loads(Path(pointer_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    out_dir = record.get("out_dir")
    result_json = record.get("result_json") or (
        os.path.join(out_dir, "storyboard_result.json") if out_dir else None)
    if not out_dir or not result_json or not os.path.isdir(out_dir):
        return None
    result = _read_result(result_json)
    if not result:
        return None
    if str(record.get("run_id")) != safe_name(run_id):
        return None
    if str(result.get("run_id")) != safe_name(run_id):
        return None
    if plan is not None:
        expected = plan_fingerprint(expand_product_sku_refs(canonical_storyboard_plan(plan)))
        if record.get("plan_fingerprint") != expected:
            return None
        if result.get("plan_fingerprint") != expected:
            return None
    return os.path.abspath(out_dir)


def download_first_image(api_key, prompt, out_path, *, model=DEFAULT_MODEL,
                         ratio="9:16", image_urls=None, on_progress=None,
                         resume_task_id=None, sync_img2img=False, force=False):
    """生成一张图并下载到 out_path。

    image_urls: 可选参考图列表（本地 data URL 或 https URL）。
    传入时走 img2img，不传则纯文生图。sync_img2img 保留为旧调用方兼容参数；
    正式流程统一走 BasicRouter 文档的异步 /v1/image-generations + retrieve
    轮询，避免同步 /ai/createImage 长连接在大图生成时断开。
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    from image_utils import image_type
    if (not force and os.path.isfile(out_path) and os.path.getsize(out_path) > 0 and
            image_type(out_path) in {"png", "jpeg", "webp"}):
        return {"url": "", "path": out_path, "abspath": os.path.abspath(out_path),
                "skipped": True}
    last_error = None
    result_url = None
    task_id = resume_task_id
    request_payload = {
        "stage": "storyboard_image", "prompt": prompt, "model": model,
        "ratio": ratio, "resolution": "2k", "image_urls": image_urls or [],
        "out_name": os.path.basename(out_path),
    }
    request_id = "image-" + artifact_contract.sha256_json(request_payload)
    for attempt in range(IMAGE_RETRIES + 1):
        try:
            task_id = task_id or br_client.create_image_generation(
                api_key, prompt, model=model, count=1, resolution="2k",
                ratio=ratio, image_urls=image_urls or [], request_id=request_id)
            if on_progress:
                on_progress({"status": "submitted", "task_id": task_id, "attempt": attempt + 1})

            def tick(status, waited):
                if on_progress and (waited == 0 or waited % 30 == 0):
                    on_progress({"status": status, "waited": waited, "task_id": task_id})

            urls = br_client.wait_image_generation(
                api_key, task_id, interval=5, max_wait=IMAGE_MAX_WAIT, on_tick=tick)
            if not urls:
                raise br_client.BRError("image task succeeded but returned no image")
            result_url = urls[0]
            # Result URLs come from BasicRouter's image-generation retrieve
            # endpoint. Some customer machines terminate HTTPS through a
            # local loopback proxy even when the result hostname resolves
            # publicly, so allow a non-public socket peer only for this
            # provider-returned media download path. User-supplied remote
            # source downloads keep the default stricter SSRF check.
            br_client.download(result_url, out_path, allow_nonpublic_peer=True)
            if image_type(out_path) not in {"png", "jpeg", "webp"}:
                raise br_client.BRError("image task downloaded an invalid image")
            return {"url": result_url, "path": out_path,
                    "abspath": os.path.abspath(out_path), "task_id": task_id,
                    "request_id": request_id, "sha256": _file_sha256(out_path)}
        except Exception as exc:
            last_error = exc
            if result_url:
                # The paid task has already succeeded. Never submit another task
                # merely because the local download exhausted its own retries.
                raise
            # Once a task ID exists, timeout or polling transport failure is
            # resumable. Never submit another paid task unless the provider
            # explicitly reported a terminal task failure.
            if task_id and "task failed" not in str(exc).lower():
                raise
            if attempt < IMAGE_RETRIES:
                delay = 5 * (attempt + 1)
                print("[gpt-image-2] image task failed, retrying in %ss: %s" %
                      (delay, exc), flush=True)
                time.sleep(delay)
    raise last_error


def expand_storyboard_panel(api_key, segment, *, out_dir):
    """Create the high-resolution keyframe reference for one approved shot.

    The source 12-panel storyboard is never pixel-cropped. gpt-image-2 receives
    the confirmed shot sheet and only the assets selected by the segment's
    ``ref_tags``. The output is a single representative 16:9 keyframe for the
    current segment, not "panel N" copied from a contact sheet.
    """
    sheet = segment.get("storyboard_path")
    panel_index = int(segment.get("storyboard_panel_index") or 1)
    if not sheet or not os.path.isfile(sheet):
        raise br_client.BRError("STORYBOARD_PANEL_SOURCE_MISSING: %s" % sheet)
    refs = [ref for ref in (segment.get("references") or []) if isinstance(ref, dict)]
    wanted = set(segment.get("ref_tags") or [])
    selected = [ref for ref in refs if ref.get("tag") in wanted]
    if not selected:
        raise br_client.BRError("STORYBOARD_PANEL_REFS_MISSING: 镜头 %s 未绑定 ref_tags" %
                                segment.get("id"))
    recipe = artifact_contract.build_storyboard_panel_recipe(segment)
    recipe["panel_index"] = panel_index
    if not recipe.get("storyboard_sha256"):
        raise br_client.BRError("STORYBOARD_PANEL_SOURCE_MISSING: %s" % sheet)
    if set(recipe.get("ref_tags") or []) != wanted or len(recipe.get("references") or []) != len(wanted):
        raise br_client.BRError("STORYBOARD_PANEL_REFS_MISSING: 镜头 %s 的标签与参考契约不一致" %
                                segment.get("id"))
    budget = artifact_contract.storyboard_image_input_tags(segment)
    input_tags = budget["selected"]
    omitted_tags = budget["omitted"]
    recipe_sha = artifact_contract.sha256_json(recipe)
    panel_dir = os.path.join(out_dir, "expanded_panels")
    out = os.path.join(panel_dir, "shot_%s_keyframe.jpg" %
                       safe_name(segment.get("id")))
    meta = out + ".json"
    previous = _read_result(meta)
    if (previous and previous.get("recipe_sha256") == recipe_sha and
            os.path.isfile(out) and os.path.getsize(out) > 0):
        return {"path": out, "abspath": os.path.abspath(out), "sha256": _file_sha256(out),
                "recipe_sha256": recipe_sha, "skipped": True}
    os.makedirs(panel_dir, exist_ok=True)
    source_urls = [_br.to_image_ref(sheet, api_key=api_key, prefer_hosted=False)]
    selected_by_tag = {ref.get("tag"): ref for ref in selected}
    for tag in input_tags:
        ref = selected_by_tag[tag]
        source_urls.append(_br.to_image_ref(ref["url"], api_key=api_key, prefer_hosted=False))
    source_urls = list(dict.fromkeys(source_urls))
    prompt = (
        "Create ONE high-resolution 16:9 photorealistic live-action keyframe for video generation. "
        "Use the approved 12-panel storyboard sheet as the storyboard for THIS CURRENT SHOT only. "
        "Synthesize one representative full-frame keyframe that best matches the current shot's director text, "
        "product/character references and intended action. Do not copy or crop any grid cell literally. "
        "Do not show any grid, border, annotation, sketch, text, subtitle, "
        "watermark or logo. Reconstruct this single panel as a clean full-frame commercial plate. "
        "Preserve the same character identity, wardrobe, product geometry, scene, props, lighting and action. "
        "This shot uses %s. Segment id: %s. Source shot ids: %s. Shot instruction: %s"
        % (" ".join(input_tags), segment.get("id"),
           ",".join(str(x) for x in (segment.get("source_shot_ids") or [])),
           segment.get("text") or "the approved shot action"))
    if omitted_tags:
        prompt += (" Lower-priority reference tags were intentionally not uploaded due to the provider "
                   "4-image limit: %s. Preserve their semantic role from the shot instruction and storyboard, "
                   "but treat the uploaded tags as the visual anchors." % " ".join(omitted_tags))
    result = download_first_image(api_key, prompt, out, model=DEFAULT_MODEL, ratio="16:9",
                                  image_urls=source_urls, sync_img2img=True)
    # Keep the handoff shape stable even for alternate image backends/tests that
    # return only ``path``.
    result["path"] = out
    result["abspath"] = os.path.abspath(out)
    result["recipe_sha256"] = recipe_sha
    result["panel_index"] = panel_index
    Path(meta).write_text(json.dumps({"recipe_sha256": recipe_sha, "recipe": recipe,
                                      "path": os.path.abspath(out)}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result


def _as_feature_dict(val):
    """把 facial_features / body_features 归一成 dict。

    容错：LLM 生成的 plan 里这些字段可能是 dict，也可能被写成一段纯文本 string
    （真机实测遇到过）。string 时包成 {"_raw": <描述>}，让后续 .get(key) 不崩，
    并能通过 _feat() 把整段描述当兜底文本用。None/其它类型归一成空 dict。
    """
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip():
        return {"_raw": val.strip()}
    return {}


def _feat(d, key, char, char_key=None):
    """取特征字段：优先 features dict 的具体键，其次 character 顶层同名键，
    最后回落到 string 形态被包进来的整段描述 (_raw)。全空返回 ''。"""
    v = d.get(key)
    if v:
        return v
    if char_key is None:
        char_key = key
    v = char.get(char_key)
    if v:
        return v
    return d.get("_raw", "")


def cast_prompt(plan):
    title = plan.get("project_title") or "marketing video"
    style = plan.get("visual_style") or "premium commercial storyboard concept art"
    chars = plan.get("characters") or []
    if not chars:
        return None
    lines = []
    for i, c in enumerate(chars, 1):
        face = _as_feature_dict(c.get("facial_features"))
        body = _as_feature_dict(c.get("body_features"))
        # face_shape 兼容两种键名：features 里可能叫 face_shape 或 face
        face_shape = face.get("face_shape") or face.get("face") or c.get("face_shape", "")
        body_raw = body.get("_raw", "")
        body_desc = " ".join(str(x) for x in [
            body.get("height", ""), body.get("build", ""),
            body.get("proportions", ""), c.get("body_type", ""), body_raw,
        ] if x).strip()
        parts = [
            f"Character {i}: id={c.get('id','')}, name={c.get('name','')}, role={c.get('role','')}",
            f"age/gender/ethnicity={c.get('age','')} {c.get('gender','')} {c.get('ethnicity','')}",
            f"appearance={c.get('appearance','')}",
            f"face={face_shape}; eyebrows={_feat(face,'eyebrows',c)}; eyes={_feat(face,'eyes',c)}; nose={_feat(face,'nose',c)}; lips={_feat(face,'lips',c)}; ears={_feat(face,'ears',c)}; skin={_feat(face,'skin',c)}; special_marks={_feat(face,'special_marks',c)}",
            f"hair={c.get('hair','')}; makeup={c.get('makeup','')}; costume={c.get('costume','')}; shoes={c.get('shoes','')}; accessories={c.get('accessories','')}",
            f"body={body_desc}",
            f"personality={c.get('personality','')}; default_expression={c.get('default_expression','')}; posture/body_language={c.get('body_language','')}; voice={c.get('voice','')}",
        ]
        immutable = c.get("immutable_features") or plan.get("immutable_features") or []
        if immutable:
            parts.append("immutable identity locks=" + (", ".join(immutable) if isinstance(immutable, list) else str(immutable)))
        # 保留有实际内容的 part：只要任一 "key=value" 的 value 非空就留下。
        # （旧逻辑用 not p.endswith('=') 判空，对多字段复合行会误杀——末字段为空
        #  就丢掉整行，导致 face=... 这类多描述行里前面的有效内容一起消失。）
        def _has_content(part):
            for seg in part.split(";"):
                if "=" in seg and seg.split("=", 1)[1].strip():
                    return True
            return "=" not in part and bool(part.strip())
        lines.append("; ".join([p for p in parts if _has_content(p)]))

    # ── 注入客户上传的数字人参考图 ────────────────────────────────────────────
    # asset_refs.digital_human_portraits: 客户真实上传的人物图，是人物板的最高优先级参考。
    # 若存在，强制要求模型严格保持人脸/发型/服装与参考图一致，而不是凭空想象。
    ref_block = ""
    asset_refs = plan.get("asset_refs") or {}
    portrait_refs = asset_refs.get("digital_human_portraits") or []
    if portrait_refs:
        ref_block = (
            "\n\n[CRITICAL — UPLOADED CHARACTER REFERENCE IMAGES]\n"
            "The client has provided REAL uploaded reference photos for the characters below.\n"
            "These photos define the EXACT face, hairstyle, skin tone, and outfit.\n"
            "You MUST reproduce these features faithfully — do NOT invent or alter them.\n"
            "Reference images are provided inline as image_url blocks in the API call.\n"
            "Character portrait references:\n"
            + "\n".join("  - %s" % r for r in portrait_refs)
        )
    # ── 近景人脸 ↔ 全身一致性强锁（seedance 文档 ID 漂移根因对策）────────────────
    # seedance「人物 ID 漂移」根因：人脸参考有效性不足——人脸与全身/姿态混在一起、
    # 人脸占比过小、权重不够。对策：把「人脸特写(大头照)」当成最高权重的身份锚，
    # 明确要求六视图里的正脸特写与全身照里的脸是「同一张脸」，五官逐项对齐；
    # 大头照要占比大、五官清晰、无表情最佳、减少肩颈/背景干扰。
    face_lock_block = (
        "\n\n[CRITICAL — FACE ↔ FULL-BODY IDENTITY LOCK / 近景人脸与全身一致性强锁]\n"
        "The single most important requirement: the SAME face must appear in BOTH the "
        "face close-up views AND the full-body views. Treat the FRONT FACE CLOSE-UP (大头照) "
        "as the primary identity anchor — render it LARGE, high-detail, sharp, front-lit, "
        "ideally neutral expression, with minimal shoulder/neck/background distraction so the "
        "facial features carry maximum weight. Then reproduce EXACTLY that same face on the "
        "full-body panels: identical face shape, eye shape and spacing, eyebrow shape, nose "
        "bridge and tip, lip shape, jawline, ear shape, skin tone, and hairline. "
        "The near-shot face and the full-body face must be unmistakably the SAME PERSON — "
        "no age drift, no face-slimming, no beautify filter that changes bone structure, "
        "no different makeup between the close-up and the full-body panels. "
        "If the close-up face and the full-body face look even slightly like different people, "
        "the sheet is REJECTED. Keep hairstyle, wardrobe, shoes, accessories and body "
        "proportions identical across every panel too."
    )
    return (
        "Create a professional CHARACTER REFERENCE SHEET / 人物六视图参考板 for a marketing video, based on the character design sheet standard. "
        "For EACH appearing character, create ONE clean reference-sheet image that clearly contains SIX REQUIRED VIEWS in a neat 2x3 or 3x2 grid, with visible panel separation and consistent scale. "
        "The six panels MUST be: "
        "(1) full-body front view / 全身正视图, standing straight, full body in frame, feet visible; "
        "(2) full-body back view / 全身后视图, back facing camera, full outfit back, hair back and silhouette visible; "
        "(3) full-body side view / 全身侧视图, exact left or right profile, nose/chin/profile and full outfit side seam visible; "
        "(4) face close-up front view / 正脸正视图, LARGE high-detail 85mm portrait (大头照, the identity anchor), neutral or gentle expression, eyes and all facial features crisp, minimal shoulder/background; "
        "(5) face/head close-up back view / 正脸后视图/后脑勺视图, back of head, hairline/hairstyle/ear/back-neck details visible; "
        "(6) face close-up side view / 正脸侧视图, exact side profile close-up, cheekbone, brow, eye, nose bridge, lips and jawline visible. "
        "Lock the SAME identity across all six panels: face shape, eye shape/color, eyebrow shape, nose bridge, lip shape, ear shape, skin tone, hairstyle, body proportions, height/build, wardrobe, shoes, accessories, makeup, scars/moles/special marks, and color palette must remain consistent. "
        "Use pure white seamless background #FFFFFF, neutral studio backdrop, high-key soft studio lighting, three-point lighting, neutral 5500K color temperature, photorealistic commercial character-design reference sheet, ultra detailed. "
        "Use only tiny panel labels if needed; no subtitles, no long text, no watermark, no logo distortion. "
        "Negative constraints: different face, inconsistent identity, face close-up and full-body looking like different people, changed hairstyle, changed outfit, different body proportions, wrong back view, duplicate panels, missing feet, cropped full body, extra limbs, bad hands, asymmetrical face, blurry, low quality, text blocks, watermark, dark/patterned background. "
        "If multiple characters are listed, include a separate six-view reference block for each character in the same image, without mixing identities; keep each character visually separable.\n"
        f"Project: {title}\nVisual style: {style}\n" + "\n".join(lines)
        + face_lock_block
        + ref_block
    )


def _subject_definition_block(plan, shot, chars):
    """seedance 主体定义句式：把每个出场人物用「将<主体>的核心特征定义为<标签>」锁定，
    后续统一用同一标签指代，降低 ID 漂移/双胞胎。返回一段可拼进 prompt 的文本或 ''。"""
    shot_char_ids = shot.get("characters") or []
    if not shot_char_ids:
        return ""
    lines = ["\n[SUBJECT DEFINITION / 主体定义（seedance 强指代，防 ID 漂移与双胞胎）]"]
    for cid in shot_char_ids:
        c = chars.get(cid, {"id": cid})
        label = c.get("name") or cid
        # 2-3 个稳定静态特征（服饰/发型/外观/类别）唯一可识别
        feats = []
        if c.get("costume"):
            feats.append(str(c["costume"]))
        if c.get("hair"):
            feats.append("发型:" + str(c["hair"]))
        if c.get("appearance"):
            feats.append(str(c["appearance"]))
        feat_str = "，".join([f for f in feats if f][:3]) or "该角色"
        lines.append(
            "  - 将「%s」（%s）定义为主体【%s】，后续全程仅用【%s】指代，保持同一身份，"
            "不得出现第二个外形/着装/配饰完全相同的人物。" % (label, feat_str, label, label)
        )
    return "\n".join(lines)


def _panel_prompt_block(shot):
    """Turn panel_plan into explicit director beats instead of a loose list.

    Image models respond more reliably when each panel has one observable event
    and a camera/composition change.  Keep free-form strings supported for old
    plans, while allowing new plans to provide structured panel dictionaries.

    2026-08-05 revision: panel count is script-driven (no [:12] truncation),
    and each panel line inlines its @tag reference(s) when the plan/shot
    specifies which uploaded asset that beat depends on (panel-level
    ref_tags, falling back to shot-level ref_tags).
    """
    panel_plan = normalize_panel_plan(shot)
    if not panel_plan:
        return ""
    total = len(panel_plan)
    shot_ref_tags = shot.get("ref_tags") or []
    if isinstance(shot_ref_tags, str):
        shot_ref_tags = [shot_ref_tags]
    lines = [
        "\n[PANEL-BY-PANEL DIRECTOR BEATS / 逐格导演执行表 — %d panels total]" % total,
        "Render each numbered panel as one readable instant, not a collage or repeated pose.",
        "For every panel preserve screen direction, subject identity, prop geometry and the previous panel's cause-and-effect.",
    ]
    for number, item in enumerate(panel_plan, 1):
        if isinstance(item, dict):
            panel = item.get("panel") or item.get("index") or number
            values = [
                ("beat", item.get("beat") or item.get("action") or item.get("event")),
                ("shot", item.get("shot_size") or item.get("size")),
                ("camera", item.get("camera_movement") or item.get("camera")),
                ("angle", item.get("angle") or item.get("angle_offset")),
                ("composition", item.get("composition") or item.get("layout")),
                ("lighting", item.get("lighting")),
                ("expression", item.get("micro_expression") or item.get("emotion")),
                ("continuity", item.get("continuity")),
            ]
            detail = "; ".join("%s=%s" % (key, value) for key, value in values if value not in (None, ""))
            panel_tags = item.get("ref_tags") or shot_ref_tags
            if isinstance(panel_tags, str):
                panel_tags = [panel_tags]
            tag_str = (" refs=%s" % " ".join(panel_tags)) if panel_tags else ""
            lines.append("  Panel %s: %s%s" % (panel, detail or "one distinct observable beat", tag_str))
        else:
            tag_str = (" refs=%s" % " ".join(shot_ref_tags)) if shot_ref_tags else ""
            lines.append("  Panel %d: beat=%s%s" % (number, str(item).strip(), tag_str))
    return "\n".join(lines)


def _cinematic_grammar_block(shot):
    """Add model-agnostic visual grammar and a practical acceptance checklist."""
    panel_count = len(normalize_panel_plan(shot))
    return (
        "\n[CINEMATIC PROMPT GRAMMAR / 电影提示词语法]\n"
        "Priority order: 1) subject identity and count, 2) spatial relationships and screen direction, "
        "3) one physical action with a visible result, 4) shot size and camera angle, 5) camera movement, "
        "6) composition and negative space, 7) lighting/material response, 8) emotional state. "
        "Use concrete visible nouns and verbs; do not describe invisible marketing claims as imagery. "
        "Each panel is a single decisive frame from a continuous take, with motivated camera movement and "
        "a clear foreground/midground/background layer. Keep product scale, hand contact, eyelines and "
        "left-to-right screen direction physically plausible.\n"
        "[DIRECTOR QA / 导演验收约束]\n"
        "The result is acceptable only if all %d panels are countable and readable, each panel has a distinct "
        "story beat, adjacent panels provide a 30-50 degree angle change or a clear shot-size/composition change, "
        "hands actually contact the intended prop, faces remain the same person, and no panel contradicts the "
        "location, wardrobe, lighting, product geometry or timeline. Prefer simple achievable actions over "
        "simultaneous complex actions." % panel_count
    )


def shot_prompt(plan, shot, idx, bw=True, strict_bw=False):
    title = plan.get("project_title") or "marketing video"
    style = plan.get("visual_style") or "premium commercial storyboard, director's frame"
    chars = {c.get("id"): c for c in (plan.get("characters") or [])}
    shot_chars = []
    for cid in shot.get("characters") or []:
        c = chars.get(cid, {"id": cid})
        shot_chars.append(
            f"{c.get('name') or cid}: {c.get('appearance','')} {c.get('costume','')} {c.get('personality','')}"
        )
    no_character_block = ""
    if not shot_chars:
        no_character_block = (
            "\n[NO-CHARACTER SHOT HARD LOCK / 无人物镜头硬约束]\n"
            "This shot is explicitly characters: none. Do NOT draw any human face, head, body, torso, seated person, presenter, model, or full person in any panel. "
            "If the action explicitly requires interaction, show only cropped hands/fingers needed for the physical action; otherwise show product/props only. "
            "A lifestyle desktop may imply a user, but the user must remain off camera. This rule overrides any ambiguous wording such as 'young lifestyle scene' or old approved notes."
        )
    subject_def_block = _subject_definition_block(plan, shot, chars)
    continuity = shot.get("continuity") or plan.get("continuity") or {}
    continuity_bits = []
    bg = continuity.get("background") or shot.get("background") or shot.get("scene") or shot.get("props")
    if bg:
        continuity_bits.append(f"Background continuity: {bg}")
    if continuity.get("character_identity"):
        continuity_bits.append(f"Character identity continuity: {continuity['character_identity']}")
    if continuity.get("voice"):
        continuity_bits.append(f"Voice continuity: {continuity['voice']}")
    if continuity.get("bgm"):
        continuity_bits.append(f"BGM continuity: {continuity['bgm']}")
    if continuity.get("wardrobe"):
        continuity_bits.append(f"Wardrobe continuity: {continuity['wardrobe']}")
    if continuity.get("lighting"):
        continuity_bits.append(f"Lighting continuity: {continuity['lighting']}")
    prop_prompts = shot.get("prop_prompts") or shot.get("asset_prompts") or []
    if isinstance(prop_prompts, str):
        prop_prompts = [prop_prompts]
    panel_plan = normalize_panel_plan(shot)
    audio = shot.get("audio") or {}
    director_bits = []
    for key, label in (("narrative_function", "Narrative function"),
                       ("felt_intent", "Audience feeling target"),
                       ("director_voice", "Directorial voice"),
                       ("arc_position", "Story arc position")):
        value = shot.get(key) or plan.get(key)
        if value:
            director_bits.append("%s: %s" % (label, value))
    director_block = (
        "\nDIRECTOR'S READ — derive camera, light, blocking, performance and sound from one intention; "
        "do not stack generic cinematic adjectives:\n  " + "\n  ".join(director_bits)
        if director_bits else ""
    )

    # ── 注入客户上传的素材图和数字人参考图 ──────────────────────────────────────
    # asset_refs 挂在 plan 级别（所有镜位共享），也可在 shot 级别覆盖。
    # 三类注入：
    #   1) product_images    — 产品图，每个镜位的产品外观必须严格对照
    #   2) scene_images      — 场景图，背景/陈列要与上传图一致
    #   3) digital_human_portraits — 数字人肖像，人脸/服装锁定
    # 这些 URL/路径会在 render_storyboard 里通过 API image_urls 参数实际传给模型。
    # 这里仅在文本 prompt 里明确声明，确保模型知道参考图的存在和用途。
    asset_refs = plan.get("asset_refs") or {}
    shot_asset_refs = shot.get("asset_refs") or {}  # shot 级覆盖

    product_imgs = shot_asset_refs.get("product_images") or asset_refs.get("product_images") or []
    product_usage_imgs = (shot_asset_refs.get("product_usage_images") or
                          asset_refs.get("product_usage_images") or [])
    if not _shot_needs_usage_reference(shot):
        product_usage_imgs = []
    scene_imgs = shot_asset_refs.get("scene_images") or asset_refs.get("scene_images") or []
    portrait_imgs = shot_asset_refs.get("digital_human_portraits") or asset_refs.get("digital_human_portraits") or []
    # @tag assignment: computed once here, reused both by asset_block (URL ->
    # tag legend) and by the tag_legend block further below (tag -> role).
    tagged_assets = _assign_asset_tags(product_imgs, product_usage_imgs, scene_imgs, portrait_imgs)
    _url_tag = {url: tag for tag, url, _category in tagged_assets}

    product_facts = shot.get("product_facts") or plan.get("product_facts") or {}
    product_fact_block = ""
    if product_facts and (product_imgs or shot.get("product") or shot.get("props")):
        facts = []
        for key in ("product_name", "product_type", "color", "price"):
            if product_facts.get(key):
                facts.append("%s=%s" % (key, product_facts[key]))
        if product_facts.get("usps"):
            facts.append("verified USPs=" + "; ".join(map(str, product_facts["usps"])))
        if product_facts.get("specs"):
            facts.append("verified specs=" + "; ".join(
                "%s:%s" % (k, v) for k, v in product_facts["specs"].items()))
        product_fact_block = (
            "\nPRODUCT FACTS FROM THE CLIENT BRIEF — MUST BE CONSISTENT WITH THE "
            "UPLOADED PRODUCT IMAGES; do not invent or contradict these visible facts:\n"
            "  " + "\n  ".join(facts)
        )
    usage_plan = dict(plan)
    usage_plan["asset_refs"] = dict(asset_refs)
    if product_imgs:
        usage_plan["asset_refs"]["product_images"] = product_imgs
    usage_plan["shots"] = [shot]
    usage_block = (
        "\n" + product_usage_prompt(usage_plan, shot)
        if needs_product_usage_image(usage_plan) and
        _shot_needs_usage_reference(shot)
        else ""
    )

    asset_block = ""
    if product_imgs or product_usage_imgs or scene_imgs or portrait_imgs:
        lines_a = [
            "\n\n[CRITICAL — CLIENT UPLOADED REFERENCE ASSETS]",
            "The following images were uploaded by the client and MUST be strictly referenced.",
            "Do NOT invent product shapes, colors, logos, or character faces from imagination.",
            "Every panel in this storyboard must be consistent with these references.",
        ]
        if product_imgs:
            lines_a.append("PRODUCT reference images (exact shape, color, logo, material must match):")
            lines_a.extend("  - %s %s" % (_url_tag.get(u, ""), u) for u in product_imgs)
        if product_usage_imgs:
            lines_a.append("CONFIRMED PRODUCT-IN-USE reference (match the person's real operation, hand contact, product orientation and visible functional details):")
            lines_a.extend("  - %s %s" % (_url_tag.get(u, ""), u) for u in product_usage_imgs)
        if scene_imgs:
            lines_a.append("SCENE / BACKGROUND reference images (layout, props, environment must match):")
            lines_a.extend("  - %s %s" % (_url_tag.get(u, ""), u) for u in scene_imgs)
        if portrait_imgs:
            lines_a.append("CHARACTER / DIGITAL HUMAN portrait references (face, hair, outfit IDENTITY LOCKED):")
            lines_a.extend("  - %s %s" % (_url_tag.get(u, ""), u) for u in portrait_imgs)
        asset_block = "\n".join(lines_a)


    # ── 黑白画面约束（用户要求：故事板必须是黑白）────────────────────────────────
    # 支持 plan/shot 级 color_mode 覆盖：'bw'（默认黑白）或 'color'。
    color_mode = ("bw" if strict_bw else
                  (shot.get("color_mode") or plan.get("color_mode") or ("bw" if bw else "color")))
    if str(color_mode).lower() in ("bw", "black_white", "grayscale", "mono", "黑白"):
        bw_block = (
             "STRICT BLACK-AND-WHITE DRAWING / 严格黑白绘画: render the ENTIRE %d-panel sheet in monochrome "
            "grayscale only — pure black, white and grays, NO color hue anywhere, like a classic "
            "pencil/charcoal film previsualization storyboard. Use tonal contrast and shading "
            "(not color) to convey depth, material and lighting. This is a black-and-white "
            "storyboard, not a colored render. " % len(panel_plan)
        )
        bw_negative = "任何彩色, 色相, 饱和度, 上色, colored, color tint, saturation, "
    else:
        bw_block = ""
        bw_negative = ""
    # ── N 格电影预演：每格都必须包含身体动量和明确摄影运动（N 由剧本分镜数决定）。
    seq_block = (
        "Treat the %d panels as an ORDERED SHOT SEQUENCE 镜头1→镜头%d in strict event order "
        "(先主后次). Every panel MUST show visible movement and body momentum; avoid static standing poses. "
        "Each panel = subject + location + physical action + camera movement + emotional pressure. "
        % (len(panel_plan), len(panel_plan))
    )
    # ── 双胞胎/字幕/Logo/水印全局约束（seedance 常见问题对策 + AGENTS.md 铁律#15）───
    twin_block = (
        "GLOBAL ANTI-TWIN CONSTRAINT 双胞胎全局约束: the whole sheet must NEVER show two people "
        "with identical face/outfit/accessories; NO clone/twin/duplicate person of any defined "
        "subject; each panel keeps only the single correct subject. "
        "ABSOLUTELY NO TEXT IN FRAME / 画面背景严禁出现任何文字: NO subtitles, NO on-screen "
        "captions, NO caption bar, NO speech bubbles, NO watermark, NO platform Logo, NO kinetic "
        "typography, NO floating slogan text, NO data/metric label callouts, NO UI text overlays "
        "that the model has to invent. All of these belong to a SEPARATE post-production motion-"
        "graphics layer (HyperFrames) added AFTER this storyboard/video is generated — this frame "
        "must render as a clean, text-free cinematic plate (人物/场景/产品实拍画面), even if the "
        "shot's narrative mentions slogans, stats, or captions appearing on screen. The ONLY "
        "exception is text that is a physical, pre-existing part of a real prop (e.g. text already "
        "printed on product packaging or visible in an uploaded screenshot reference image) — never "
        "invent new floating/animated text. "
    )
    # ── motion_elements：只作为后期交接数据，不把具体文字/动效设计发给图像模型。
    motion_elements = shot.get("motion_elements") or []
    if isinstance(motion_elements, str):
        motion_elements = [motion_elements]
    motion_note = (
        "\nA separate post-production motion layer may be added after the base video; "
        "do not render any such graphics in this storyboard image."
        if motion_elements else ""
    )
    # Grid layout is script-driven: pick cols x rows from the actual panel
    # count (2026-08-05 revision). 12 remains a valid value (falls through to
    # 4x3/3x4 as before) but is no longer forced.
    aspect = plan.get("aspect_ratio", "16:9")
    portrait = aspect in ("9:16", "3:4")
    panel_count = len(panel_plan)
    grid_cols, grid_rows = _grid_for_panel_count(panel_count, portrait)
    grid_desc = "%dx%d" % (grid_cols, grid_rows)
    canvas_desc = "9:16" if aspect == "9:16" else ("3:4" if aspect == "3:4" else "16:9")

    # ── @tag inline legend so the model maps each panel beat to a concrete
    # uploaded reference image instead of only seeing a bare URL list below.
    # (tagged_assets computed earlier alongside product_imgs/... for asset_block)
    tag_legend = ""
    if tagged_assets:
        _tag_role_label = {
            "product_images": "product reference",
            "product_usage_images": "confirmed product-in-use reference",
            "scene_images": "scene/background reference",
            "digital_human_portraits": "character/digital-human portrait reference",
        }
        legend_lines = ["\n[REFERENCE TAG LEGEND / 参考图标签]",
                        "Use these @tags when a panel beat below says refs=@xxx; each @tag points to one uploaded image."]
        for tag, _url, category in tagged_assets:
            legend_lines.append("  %s = %s" % (tag, _tag_role_label.get(category, category)))
        tag_legend = "\n".join(legend_lines)

    return (
        "Create a %s cinematic storyboard TABLE with %d movie-style panels arranged as a clean %s grid / %s电影风格%d格故事板表格。"
        % (canvas_desc, panel_count, grid_desc, canvas_desc, panel_count)
        + bw_block
        + "The single image must contain exactly %d panels arranged in a clean %s grid inside one %s canvas, each panel showing a distinct beat of the SAME segment. "
        % (panel_count, grid_desc, canvas_desc)
        + "The drawing itself is ONLY rough black-and-white pencil lines, minimal detail, quick loose construction, simple anatomy, strong silhouette readability, lightweight dynamic unfinished early choreography previsualization. "
        + "Add director annotation marks as a separate overlay layer: RED arrows for body motion, BLUE arrows for camera motion, GREEN marks for framing/composition notes, ORANGE marks for lighting direction, PURPLE marks for sound/emotional emphasis, and BLACK short shot notes/panel labels. Annotation colors are allowed only for these marks; all characters, environment, smoke, fabric, reflections and drawn imagery remain black/white/gray. "
        + "Use handheld energy, whip pans, orbiting camera, overhead view, side profile silhouette, aggressive close-up, and long-lens compression across the sequence. Keep the environment minimal: open space, smoke, fabric motion, reflected light. Make performers feel trapped between ritual and emotional release. "
        + seq_block
        + "Use film-style previsualization, readable composition, strong scene continuity, and edit-friendly coverage. "
        "The panel-by-panel director beats below define the exact %d-beat progression for this segment; follow them in order while preserving the same segment continuity. " % panel_count
        + "Use professional camera language: rule-of-thirds or symmetry or foreground framing, clear eyeline/product placement, lens-like depth, directional lighting, and intentional negative space for later overlays. "
        "Keep adjacent panels visually different: 30-50 degree camera/subject angle offsets, or wide/medium/close/detail shot-size variation, or composition center shift. "
        "Keep the SAME background, SAME character identity, SAME wardrobe, SAME product appearance, SAME prop relationships, SAME lighting, and SAME art direction across the %s grid. " % grid_desc
        + "If this segment belongs to a multi-part stitched sequence, keep background, character appearance, voice tone, and BGM mood consistent across ALL segments; only vary camera angle, framing, and beat progression. "
         + twin_block
         + director_block
         + _cinematic_grammar_block(shot)
         + subject_def_block + "\n"
        f"Project: {title}\n"
        f"Shot number: {idx}\n"
        f"Duration: {shot.get('duration','')} seconds\n"
         f"Visual action: {shot.get('visual','')}\n"
        f"Camera/lens/movement: {shot.get('camera') or shot.get('camera_movement','')}\n"
        f"Shot size: {shot.get('shot_size','')}\n"
        f"Angle offset: {shot.get('angle_offset','')}\n"
        f"Composition/layout: {shot.get('composition') or shot.get('layout','')}\n"
        f"Lighting: {shot.get('lighting','')}\n"
        f"Character action/performance: {shot.get('character_action','')}\n"
        f"Character prompt / identity lock: {shot.get('character_prompt') or shot.get('actor_prompt','')}\n"
        f"Micro-expression: {shot.get('micro_expression','')}\n"
        f"Characters in shot: {'; '.join(shot_chars) if shot_chars else 'none / product only'}\n"
        f"Scene prompt: {shot.get('scene_prompt') or shot.get('scene','')}\n"
        f"Scene relationship / continuity: {shot.get('scene_relationship','')}\n"
        f"Props/product/scene: {shot.get('props','')} {shot.get('scene','')}\n"
        f"Important prop/asset prompts: {'; '.join(prop_prompts)}\n"
         f"Custom {panel_count}-panel plan: {'; '.join(str(p) if not isinstance(p, dict) else json.dumps(p, ensure_ascii=False, sort_keys=True) for p in panel_plan)}\n"
        f"Audio continuity notes: voice={audio.get('voice','')}; bgm={audio.get('bgm','')}; sfx={audio.get('sfx','')}\n"
        f"Video prompt notes: {shot.get('video_prompt_notes','')}\n"
        f"Overall visual style: {style}\n"
        f"Negative constraints: {bw_negative}字幕, 文字, 水印, logo, kinetic typography, 悬浮文字, 数据标签, 双胞胎, 分身, 同款重复人物, 畸形, 多手指, 面部扭曲, 低质量\n"
        + (("\n".join(continuity_bits)) + "\n" if continuity_bits else "")
          + asset_block
          + tag_legend
          + product_fact_block
          + usage_block
          + _panel_prompt_block(shot)
          + motion_note
          + (("\n【用户确认的中文导演提示词】\n" + str(shot.get("approved_prompt_zh")))
             if shot.get("approved_prompt_zh") else "")
          + no_character_block
    )


def contact_sheet_prompt(plan, bw=True, reference_registry=None):
    """Prompt for a single-shot contact sheet with explicit reference mapping."""
    shots = plan.get("shots") or []
    shot = shots[0] if shots else {}
    if shot.get("approved_submission_prompt_zh"):
        return str(shot["approved_submission_prompt_zh"])
    base = shot_prompt(plan, shot, 1, bw=bw, strict_bw=False)
    registry = reference_registry or []
    if not registry:
        return base
    tags = {str(item.get("tag") or "") for item in registry}
    priority_parts = []
    if "@usage" in tags:
        priority_parts.append("@usage product-in-use relationship first")
    if any("product" in str(item.get("type") or "") for item in registry):
        priority_parts.append("product-board/product refs for product shape")
    if any("character" in str(item.get("type") or "") for item in registry):
        priority_parts.append("character refs for face/body/wardrobe")
    if any("scene" in str(item.get("type") or "") for item in registry):
        priority_parts.append("scene refs for environment only")
    priority_text = "; ".join(priority_parts) or "only the attached refs listed below"
    lines = [
        "\n[CONTACT SHEET REFERENCE REGISTRY / 故事板联系表参考图注册表]",
        "These uploaded images are attached to this generation request. Treat each @tag as an identity-locked visual anchor, not as optional inspiration.",
        "When the shot or panel plan says refs=@tag, the matching uploaded image must control the visible subject identity, product geometry, operation relationship, and continuity.",
        "Do not average conflicting references. Priority for this request: %s." % priority_text,
    ]
    for item in registry:
        lines.append(
            "  %s = %s; role=%s; source=%s" % (
                item.get("tag", ""),
                item.get("source") or item.get("url") or "",
                item.get("type") or "generic_visual",
                item.get("url") or "",
            )
        )
    return base + "\n" + "\n".join(lines)



def _data_uri(path):
    import base64
    ext = os.path.splitext(path)[1].lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def write_index(plan, results, out_dir):
    shot_results = results.get("shots", [])
    panel_counts = [len(normalize_panel_plan(item.get("shot", {}))) for item in shot_results]
    panel_summary = (
        "、".join(str(n) for n in panel_counts) if panel_counts else "N"
    )
    def _abs_result_path(record):
        path = (record or {}).get("abspath") or (record or {}).get("path")
        if not path:
            return path
        return path if os.path.isabs(path) else os.path.abspath(path)

    lines = []
    lines.append(f"# {plan.get('project_title','Storyboard')} — 16:9 黑白铅笔预演故事板确认稿（格数按剧本分镜数量）")
    lines.append("")
    if results.get("reference_registry"):
        lines.append("## 分镜引用映射 / Panel reference mapping")
        for item in results["reference_registry"]:
            lines.append("- %s：%s" % (item["tag"], item["source"]))
        lines.append("")
    lines.append("> 故事板为 16:9 黑白铅笔线稿预演，每个分段的格数由剧本设计的分镜/片段数量决定（本次各段格数：%s）；红/蓝/绿/橙/紫仅用于导演运动、机位、构图、灯光和声音情绪标注。请客户先确认人物、镜头、构图和产品表达，确认后才进入视频生成。" % panel_summary)
    lines.append("")
    if results.get("cast_board"):
        p = results["cast_board"]["abspath"]
        lines.append("## 人物板 / Cast board")
        lines.append(f"![人物板](<{p}>)")
        lines.append("")
    lines.append("## 本次生成内容 / Generation checklist")
    lines.append("- 人物板：%s" % ("已生成，待确认" if results.get("cast_board") else "未生成（计划没有出场人物）"))
    lines.append("- 产品本体九宫格：%s" % ("已生成，待确认" if results.get("product_board") else "未生成（检测到产品图后必须生成）"))
    lines.append("- 产品使用九宫格：%s" % ("已生成，待确认" if results.get("product_usage_image") else "未生成（存在产品图时必须同步生成）"))
    lines.append("- 分段故事板：%d 张，均为 16:9，格数按剧本分镜数量（%s），待确认" % (len(shot_results), panel_summary))
    lines.append("")
    lines.append("## 16:9 电影级分镜故事板 / 16:9 cinematic storyboard (panel count is script-driven)")
    if results.get("product_board"):
        lines.append("## 产品本体九宫格 / Product-only 3x3 board")
        status = results["product_board"].get("status", "pending")
        lines.append("产品板状态：%s%s" % (
            status, "，必须先确认产品九宫格，再进入后续生成。" if status != "confirmed" else "。"))
        lines.append("![产品九宫格产品板](<%s>)" % _abs_result_path(results["product_board"]))
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        lines.append("## 产品使用九宫格 / Product-in-use 3x3 board")
        lines.append("状态：%s。请确认九个使用镜面、数字人身份、手部接触和产品操作关系。" % usage.get("status", "pending"))
        lines.append("![产品使用九宫格](<%s>)" % _abs_result_path(usage))
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        lines.append(f"### {shot.get('id','shot')} · {shot.get('duration','?')}s")
        lines.append(f"- 台词/旁白：{shot.get('dialogue','')}")
        lines.append(f"- 画面：{shot.get('visual','')}")
        lines.append(f"- 镜头：{shot.get('camera','')}")
        if shot.get("ref_tags"):
            lines.append("- 参考绑定：%s" % " ".join(shot["ref_tags"]))
        lines.append(f"![{shot.get('id','shot')}](<{p}>)")
        lines.append("")
    index_path = os.path.join(out_dir, "storyboard_index.md")
    Path(index_path).write_text("\n".join(lines), encoding="utf-8")

    embedded = []
    embedded.append(f"# {plan.get('project_title','Storyboard')} — 内嵌故事板预览")
    embedded.append("")
    if results.get("reference_registry"):
        embedded.append("## 分镜引用映射 / Panel reference mapping")
        for item in results["reference_registry"]:
            embedded.append("- %s：%s" % (item["tag"], item["source"]))
        embedded.append("")
    embedded.append("> 如果当前 Agent 不能直接渲染本地文件路径图片，请打开这个版本。它把图片编码进 markdown，便于直接在聊天里预览。")
    embedded.append("")
    if results.get("cast_board"):
        p = results["cast_board"]["abspath"]
        embedded.append("## 人物板 / Cast board")
        embedded.append(f"![人物板]({_data_uri(p)})")
        embedded.append("")
    if results.get("product_board"):
        p = _abs_result_path(results["product_board"])
        status = results["product_board"].get("status", "pending")
        embedded.append("## 产品板 / Product consistency board")
        embedded.append("> 状态：%s%s" % (
            status, "；请确认产品九宫格后再进入后续生成。" if status != "confirmed" else "。"))
        embedded.append(f"![产品九宫格产品板]({_data_uri(p)})")
        embedded.append("")
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        p = _abs_result_path(usage)
        embedded.append("## 产品使用图 / Product-in-use reference")
        embedded.append("> 状态：%s。请确认人物实际使用动作、手部接触和产品细节。" % usage.get("status", "pending"))
        embedded.append("![人物使用产品细节图](%s)" % _data_uri(p))
        embedded.append("")
    embedded.append("## 分镜故事板 / Shot storyboard")
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        embedded.append(f"### {shot.get('id','shot')} · {shot.get('duration','?')}s")
        if shot.get("ref_tags"):
            embedded.append("- 参考绑定：%s" % " ".join(shot["ref_tags"]))
        embedded.append(f"- 台词/旁白：{shot.get('dialogue','')}")
        embedded.append(f"- 画面：{shot.get('visual','')}")
        embedded.append(f"- 镜头：{shot.get('camera','')}")
        embedded.append(f"![{shot.get('id','shot')}]({_data_uri(p)})")
        embedded.append("")
    embedded_path = os.path.join(out_dir, "storyboard_embedded.md")
    Path(embedded_path).write_text("\n".join(embedded), encoding="utf-8")

    html = []
    html.append("<!doctype html><html><head><meta charset='utf-8'>")
    html.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    html.append("<title>Storyboard Preview</title>")
    html.append("<style>body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;margin:24px;background:#111;color:#eee} .card{background:#1b1b1b;border:1px solid #333;border-radius:16px;padding:18px;margin:18px 0} img{max-width:100%;height:auto;border-radius:12px;border:1px solid #444} .meta{color:#bbb;line-height:1.7}</style>")
    html.append("</head><body>")
    html.append(f"<h1>{plan.get('project_title','Storyboard')} — 故事板预览</h1>")
    html.append("<p class='meta'>这些图由 gpt-image-2 生成。请确认人物、镜头、构图和产品表达；确认后才进入视频生成。</p>")
    if results.get("reference_registry"):
        html.append("<div class='card'><h2>分镜引用映射 / Panel reference mapping</h2><p class='meta'>" +
                    "<br>".join("%s: %s" % (item["tag"], item["source"])
                                for item in results["reference_registry"]) + "</p></div>")
    if results.get("cast_board"):
        p = results["cast_board"]["abspath"]
        html.append("<div class='card'><h2>人物板 / Cast board</h2>")
        html.append(f"<img alt='人物板' src='{_data_uri(p)}'></div>")
    if results.get("product_board"):
        p = _abs_result_path(results["product_board"])
        status = results["product_board"].get("status", "pending")
        html.append("<div class='card'><h2>产品板 / Product consistency board</h2>")
        html.append("<p class='meta'>状态：%s%s</p>" % (
            status, "；请确认产品九宫格后再进入后续生成。" if status != "confirmed" else "。"))
        html.append(f"<img alt='产品九宫格产品板' src='{_data_uri(p)}'></div>")
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        p = _abs_result_path(usage)
        html.append("<div class='card'><h2>产品使用图 / Product-in-use reference</h2>")
        html.append("<p class='meta'>状态：%s。请确认人物实际使用动作、手部接触和产品细节。</p>" % usage.get("status", "pending"))
        html.append("<img alt='人物使用产品细节图' src='%s'></div>" % _data_uri(p))
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        html.append("<div class='card'>")
        html.append(f"<h2>{shot.get('id','shot')} · {shot.get('duration','?')}s</h2>")
        html.append(f"<p class='meta'><b>台词/旁白：</b>{shot.get('dialogue','')}<br><b>画面：</b>{shot.get('visual','')}<br><b>镜头：</b>{shot.get('camera','')}<br><b>参考绑定：</b>{' '.join(shot.get('ref_tags') or [])}</p>")
        html.append(f"<img alt='{shot.get('id','shot')}' src='{_data_uri(p)}'>")
        html.append("</div>")
    html.append("</body></html>")
    html_path = os.path.join(out_dir, "storyboard_preview.html")
    Path(html_path).write_text("\n".join(html), encoding="utf-8")
    return os.path.abspath(index_path), os.path.abspath(embedded_path), os.path.abspath(html_path)


def _collect_image_urls(refs_list, api_key, *, fail_on_invalid=False, label="参考图"):
    """把 asset_refs 里的本地路径/URL 转成 API 可接受的 URL 列表。

    本地路径 → base64 data URL（图生图 createImage 支持）。
    已是 http(s)/data URL → 直接透传。
    生产链路传 ``fail_on_invalid=True``：已声明的素材损坏时必须阻断，禁止
    静默跳过后退化为纯文字生成。默认保留宽松模式供诊断工具兼容。
    最多取前 4 张（API 通常支持 1-4 张参考图，防止超限）。
    """
    result = []
    for r in refs_list or []:
        try:
            url = _br.to_image_ref(r, api_key=api_key, prefer_hosted=False)
            result.append(url)
        except Exception as e:
            if fail_on_invalid:
                raise br_client.BRError(
                    "%s不可用：%s（%s）。请替换为真实有效的 PNG/JPEG/WebP 图片；"
                    "为避免退化成纯文字生成，本次故事板已停止。" % (label, r, e)
                ) from e
            print(f"  [asset_ref] 跳过参考图 {r}：{e}", flush=True)
    return result[:4]


def _merge_reference_urls(portraits=None, products=None, scenes=None, limit=4):
    """Merge reference URLs without crowding product anchors out.

    A character plus several scene refs used to consume all four API slots and
    silently drop the product image. For product shots, identity and product
    geometry are the hard constraints; scene refs are secondary context.
    """
    groups = [list(portraits or []), list(products or []), list(scenes or [])]
    result = []
    seen = set()

    def add(value):
        if value and value not in seen and len(result) < limit:
            result.append(value)
            seen.add(value)

    # Keep one character anchor, then reserve the next slots for product views.
    for value in groups[0][:1]:
        add(value)
    for value in groups[1]:
        add(value)
    for value in groups[0][1:]:
        add(value)
    for value in groups[2]:
        add(value)
    return result


def _usage_reference_urls(product_refs=None, cast_refs=None, pose_refs=None, limit=3):
    """Build product-in-use image references with product identity first."""
    return _merge_reference_urls(product_refs, cast_refs, pose_refs, limit=limit)


def _reference_url_abs(url):
    if not isinstance(url, str) or not url:
        return url
    if re.match(r"^(https?|data):", url):
        return url
    return os.path.abspath(url if os.path.isabs(url) else os.path.join(ROOT, url))


def build_reference_registry(plan, limit=4):
    """Build the image reference registry for contact-sheet storyboard generation."""
    registry = []
    seen_tags = set()

    def add(tag, url, ref_type, source=None):
        if not tag or not url or tag in seen_tags or len(registry) >= limit:
            return
        registry.append({
            "tag": str(tag),
            "url": _reference_url_abs(url),
            "type": ref_type or "generic_visual",
            "source": source or _reference_url_abs(url),
        })
        seen_tags.add(str(tag))

    asset_refs = plan.get("asset_refs") or {}
    for index, url in enumerate(asset_refs.get("product_usage_images") or []):
        add("@usage" if index == 0 else "@usage%d" % (index + 1),
            url, "product_usage_identity", url)

    wanted_tags = []
    ref_by_tag = {}
    for shot in plan.get("shots") or []:
        tags = shot.get("ref_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for tag in tags:
            if tag not in wanted_tags:
                wanted_tags.append(tag)
        for ref in (shot.get("references") or []):
            if isinstance(ref, dict) and ref.get("tag") and ref.get("url"):
                ref_by_tag.setdefault(ref["tag"], ref)
    for ref in (plan.get("references") or []):
        if isinstance(ref, dict) and ref.get("tag") and ref.get("url"):
            ref_by_tag.setdefault(ref["tag"], ref)

    # Product identity next, then character identity, then any remaining tags.
    def tag_priority(tag):
        ref_type = str((ref_by_tag.get(tag) or {}).get("type") or "")
        if "product" in ref_type:
            return 0
        if "character" in ref_type:
            return 1
        return 2

    for tag in sorted(wanted_tags, key=tag_priority):
        ref = ref_by_tag.get(tag) or {}
        add(tag, ref.get("url"), ref.get("type"), ref.get("label") or ref.get("url"))

    for index, url in enumerate(asset_refs.get("product_boards") or []):
        add("@product_board" if index == 0 else "@product_board%d" % (index + 1),
            url, "product_board", url)
    for index, url in enumerate(asset_refs.get("cast_boards") or []):
        add("@host_board" if index == 0 else "@host_board%d" % (index + 1),
            url, "character_board", url)
    return registry


def _validate_reference_registry(plan, registry):
    seen = set()
    for item in registry:
        tag = item.get("tag")
        url = item.get("url")
        if not tag or not TAG_PATTERN.match(str(tag)):
            raise br_client.BRError("REFERENCE_REGISTRY_INVALID_TAG: %s" % tag)
        if tag in seen:
            raise br_client.BRError("REFERENCE_REGISTRY_DUPLICATE_TAG: %s" % tag)
        seen.add(tag)
        if not url:
            raise br_client.BRError("REFERENCE_REGISTRY_MISSING_URL: %s" % tag)
        if not re.match(r"^(https?|data):", str(url)) and not os.path.isfile(url):
            raise br_client.BRError("REFERENCE_REGISTRY_MISSING_FILE: %s %s" % (tag, url))
    wanted = set()
    for shot in plan.get("shots") or []:
        tags = shot.get("ref_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        wanted.update(tags)
    missing = sorted(tag for tag in wanted if tag not in seen and tag != "@momax_logo")
    if missing:
        raise br_client.BRError(
            "REFERENCE_REGISTRY_MISSING_TAGS: %s" % ", ".join(missing))


def _shot_ref_tags(shot):
    tags = (shot or {}).get("ref_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return [str(tag) for tag in tags if tag]


def _physical_use_trigger_text(shot):
    """Text fields that describe the current shot's physical action.

    Keep identity/wardrobe/product-category text out of this extraction.  A
    product being magnetic, wearable or held by a presenter is not enough to
    attach the product-in-use board; the shot must describe a real physical
    operation, contact, attachment, wearing, mounting or control action.
    """
    if not isinstance(shot, dict):
        return ""
    parts = []
    for key in (
            "visual", "character_action", "action", "scene_prompt",
            "video_prompt_notes", "director_notes"):
        value = shot.get(key)
        if value:
            parts.append(str(value))
    panels = shot.get("panel_plan") or []
    if isinstance(panels, str):
        # Free-form panel labels often contain camera grammar such as
        # "long-lens compression"; matching action substrings inside those
        # labels can falsely classify product-only shots as physical-use shots.
        pass
    elif isinstance(panels, list):
        for panel in panels:
            if isinstance(panel, dict):
                for key in ("beat", "action", "event", "continuity"):
                    if panel.get(key):
                        parts.append(str(panel[key]))
    return " ".join(parts).lower()


def _has_structured_use_relation(source):
    if not isinstance(source, dict):
        return False
    for key in ("use_relation", "physical_relation", "usage_relation",
                "interaction_relation", "product_use_relation"):
        value = source.get(key)
        if isinstance(value, dict) and _as_relation_dict(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _has_physical_use_action(shot):
    if not shot:
        return False
    if _has_structured_use_relation(shot):
        return True
    text = _physical_use_trigger_text(shot)
    if not text:
        return False
    strong_terms = (
        "贴合", "贴到", "吸附", "扣上", "夹住", "夹到", "佩戴", "戴上",
        "插入", "接入", "连接", "安装", "固定", "支撑", "展开支撑",
        "按下", "长按", "点击", "旋转", "滑动", "操作产品", "正确使用",
        "真实使用", "使用产品",
        "snap", "attach", "mount", "install", "dock", "plug", "connect",
        "clip", "clamp", "wear", "put on", "press", "tap", "operate",
        "switch on", "turn on", "use the product", "contact point",
    )
    if any(term in text for term in strong_terms):
        return True
    magnetic_terms = ("磁吸", "magnetic", "magnet", "magsafe")
    receiver_terms = (
        "手机背", "手机的背", "手机背面", "背面", "平板背", "tablet back",
        "phone back", "smartphone back", "receiver", "target surface",
        "contact surface", "接触面", "目标面", "承载面",
    )
    return any(term in text for term in magnetic_terms) and any(
        term in text for term in receiver_terms)


def _shot_needs_usage_reference(shot):
    """Whether product-in-use board should be attached to this shot."""
    return _has_physical_use_action(shot)


def shot_reference_registry(reference_registry, shot):
    """Filter the global registry to the exact images this shot may receive."""
    registry = reference_registry or []
    by_tag = {item.get("tag"): item for item in registry}
    allowed = []
    seen = set()

    def add(tag):
        item = by_tag.get(tag)
        if item and tag not in seen:
            allowed.append(item)
            seen.add(tag)

    if _shot_needs_usage_reference(shot):
        add("@usage")
    for tag in _shot_ref_tags(shot):
        add(tag)
    return allowed


def _confirmed_product_identity_paths(plan=None, results=None, client=None, limit=2):
    """Return product-board plus confirmed product images for strict usage identity."""
    paths = []
    results = results or {}
    board = (results.get("product_board") or {}).get("path") or (results.get("product_board") or {}).get("abspath")
    if board and os.path.isfile(board):
        paths.append(os.path.abspath(board))
    refs = ((plan or {}).get("asset_refs") or {}).get("product_images") or []
    for ref in refs:
        candidate = ref if os.path.isabs(ref) else os.path.join(ROOT, ref)
        if not os.path.isfile(candidate):
            continue
        if client and not asset_prep.is_product_asset_ready(client, candidate):
            continue
        abs_path = os.path.abspath(candidate)
        if abs_path not in paths:
            paths.append(abs_path)
        if len(paths) >= limit:
            break
    return paths[:limit]


def _hydrate_plan_asset_refs(plan):
    """Use the client brief as the source of truth for storyboard references.

    The plan is authored by an LLM and may describe a product correctly while
    omitting ``asset_refs``. Never let that omission turn a real uploaded hero
    image into text-only product prompting: recover local brief images before
    validation and product-board generation.
    """
    refs = dict(plan.get("asset_refs") or {})
    # Filter stale local paths even when no brief exists yet. Otherwise an
    # LLM-authored path can survive hydration and fail only after generation
    # credits have been requested.
    existing = []
    for ref in refs.get("product_images") or []:
        if not isinstance(ref, str):
            continue
        absolute = ref if os.path.isabs(ref) else os.path.join(ROOT, ref)
        if os.path.isfile(absolute):
            existing.append(os.path.abspath(absolute))
        elif ref.startswith(("http://", "https://", "data:")):
            existing.append(ref)
    if existing:
        refs["product_images"] = existing[:4]
    elif "product_images" in refs:
        refs.pop("product_images")
    plan["asset_refs"] = refs

    client = plan.get("client")
    if not client:
        return plan
    try:
        import asset_prep
        brief = asset_prep._load_brief(client)
    except Exception:
        brief = {}
    # Recover briefs from older runs only when the canonical client brief has
    # no material data. This keeps legacy product facts available without
    # overriding the canonical assets/<client>/brief.json source of truth.
    if not brief.get("images") and not brief.get("product_name"):
        legacy_path = os.path.join(ROOT, "output", client, "assets", "brief.json")
        try:
            if os.path.isfile(legacy_path):
                with open(legacy_path, encoding="utf-8") as handle:
                    legacy_brief = json.load(handle)
                if isinstance(legacy_brief, dict):
                    brief = legacy_brief
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    # A file copied into assets/<client>/images by a host Agent is still an
    # unregistered upload. Do not silently skip it: surface an actionable
    # ingestion error instead of treating the product as absent.
    images_dir = os.path.join(ROOT, "assets", client, "images")
    if os.path.isdir(images_dir):
        registered_paths = {
            os.path.realpath(
                p if os.path.isabs(p) else os.path.join(ROOT, p))
            for item in (brief.get("images") or []) if isinstance(item, dict)
            for p in [item.get("path")] if isinstance(p, str)
        }
        unregistered = []
        for name in sorted(os.listdir(images_dir)):
            path = os.path.join(images_dir, name)
            if (os.path.isfile(path) and not os.path.islink(path) and
                    image_type(path) in {"png", "jpeg", "gif", "webp", "bmp", "tiff"} and
                    os.path.realpath(path) not in registered_paths):
                unregistered.append(path)
        if unregistered:
            raise br_client.BRError(
                "UNREGISTERED_PRODUCT_IMAGE: 检测到客户目录中的产品图尚未登记：%s；"
                "请先执行 `python3 scripts/asset_prep.py ingest-image --client %s --file <图片>`，"
                "再用 confirm-image 确认。" % (", ".join(unregistered), client))

    # Keep only usable local references. A stale path in an LLM-authored plan
    # must not mask the real upload recorded in brief.json. When confirmed
    # product anchors exist, authored product refs must pass the stricter
    # product-ready contract too, so canonical preflight and render_storyboard
    # see the same trusted material set.
    has_ready_product_entries = any(
        isinstance(entry, dict) and
        asset_prep.is_product_asset_ready(client, entry.get("path"))
        for entry in (brief.get("images") or []))
    existing = list(refs.get("product_images") or [])
    if has_ready_product_entries:
        existing = [
            path for path in existing
            if isinstance(path, str) and asset_prep.is_product_asset_ready(client, path)
        ]
    tracked = {
        os.path.abspath(p if os.path.isabs(p) else os.path.join(ROOT, p))
        for p in existing if isinstance(p, str) and
        os.path.isfile(p if os.path.isabs(p) else os.path.join(ROOT, p))
    }
    ranked_entries = sorted(
        [entry for entry in (brief.get("images") or []) if isinstance(entry, dict)],
        key=lambda entry: {
            "hero": 0, "front": 1, "product": 2, "detail": 3,
            "side": 4, "pack": 5,
        }.get(str(entry.get("tag") or "").lower(), 99))
    for entry in ranked_entries:
        path = entry.get("path") if isinstance(entry, dict) else None
        tag = str(entry.get("tag") or "").lower() if isinstance(entry, dict) else ""
        status = entry.get("status") if isinstance(entry, dict) else None
        if not path or (tag not in {"hero", "product", "pack", "detail", "front", "side"}
                        and not tag.startswith("product-")):
            continue
        # Product identity assets are upstream dependencies. Generated pending
        # candidates cannot drive another generated board before customer approval.
        if status not in (None, "confirmed", "trusted_upload"):
            continue
        if status == "trusted_upload" and not asset_prep.is_confirmed(client, path):
            continue
        absolute = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if os.path.isfile(absolute) and os.path.abspath(absolute) not in tracked:
            existing.append(absolute)
            tracked.add(os.path.abspath(absolute))
    if existing:
        refs["product_images"] = existing[:4]
    if brief.get("product_type") and not plan.get("product_type"):
        plan["product_type"] = brief["product_type"]
    # Send verified brief facts together with the product images. Text is not a
    # substitute for the image, but it makes non-negotiable product details
    # explicit to the image model.
    product_facts = {
        key: brief.get(key) for key in
        ("product_name", "product_type", "product_color", "color", "price",
         "features", "usps", "specs", "selling_points", "key_messages")
        if brief.get(key)
    }
    if product_facts:
        plan["product_facts"] = product_facts
    plan["asset_refs"] = refs
    return plan


def expand_product_sku_refs(plan):
    """Resolve a product SKU before fingerprinting so every stage sees one plan."""
    asset_refs = dict(plan.get("asset_refs") or {})
    product_sku = asset_refs.get("product_sku")
    if not product_sku:
        return plan
    try:
        import product_library as _pl
        client = plan.get("client") or asset_refs.get("product_client") or ""
        if not client:
            return plan
        product = _pl.resolve(client, product_sku)
        expanded = []
        if product.get("hero") and os.path.isfile(product["hero"]):
            expanded.append(product["hero"])
        for path in product.get("refs") or []:
            if os.path.isfile(path) and path not in expanded:
                expanded.append(path)
            if len(expanded) >= 4:
                break
        for path in asset_refs.get("product_images") or []:
            if path not in expanded:
                expanded.append(path)
        if expanded:
            asset_refs["product_images"] = expanded[:4]
            plan["asset_refs"] = asset_refs
    except Exception as exc:
        print("  [product_sku] 展开失败，降级用 product_images 列表: %s" % exc, flush=True)
    return plan


def needs_product_board(plan):
    """Return whether user-provided or planned product references require a product board."""
    refs = plan.get("asset_refs") or {}
    if refs.get("product_sku") or refs.get("product_images"):
        return True
    for shot in plan.get("shots") or []:
        shot_refs = shot.get("asset_refs") or {}
        if shot_refs.get("product_sku") or shot_refs.get("product_images"):
            return True
        if shot.get("product_sku") or shot.get("product_refs"):
            return True
        # A finalized TVC may describe a product in props without the LLM
        # emitting asset_refs. The brief hydration step supplies the image;
        # this fallback makes the missing reference impossible to overlook.
        product_text = " ".join(str(shot.get(k, "")) for k in
                                 ("props", "scene", "visual", "prop_prompts"))
        if re.search(r"产品|product|earbud|earphone|耳机|charging case|充电盒", product_text, re.I):
            return True
    return False


def needs_product_usage_image(plan):
    """Return whether the plan needs an explicit human-product interaction anchor."""
    has_human = bool(plan.get("characters"))
    refs = plan.get("asset_refs") or {}
    has_product = bool(refs.get("product_images") or refs.get("product_sku"))
    for shot in plan.get("shots") or []:
        shot_refs = shot.get("asset_refs") or {}
        has_human = has_human or bool(shot.get("characters"))
        has_human = has_human or bool(
            shot.get("character_prompt") or shot.get("character_action") or
            shot.get("digital_human") or shot.get("actor"))
        has_product = has_product or bool(
            shot_refs.get("product_images") or shot_refs.get("product_sku") or
            shot.get("product_sku") or shot.get("product_refs")
        )
    # Product references always require a separate usage board. If no human is
    # defined, the workflow must stop with a missing-character instruction
    # rather than silently skipping the required product-use asset.
    return has_product


def _plan_product_facts(plan, shot=None):
    """Merge product facts from old and new storyboard plan shapes."""
    shot = shot or {}
    facts = {}
    for source in (plan.get("product_facts"), shot.get("product_facts")):
        if isinstance(source, dict):
            facts.update(source)
    brief = plan.get("client_brief") if isinstance(plan.get("client_brief"), dict) else {}
    for key in ("product_type", "features", "usps", "specs"):
        if brief.get(key) and key not in facts:
            facts[key] = brief.get(key)
    for key in ("product_name", "product_type", "product_model", "model",
                "product_color", "color", "features", "specs", "usps"):
        value = shot.get(key)
        if value is None:
            value = plan.get(key)
        if value is not None and key not in facts:
            facts[key] = value
    return facts


def _product_identity_lock(facts):
    product_name = facts.get("product_name") or facts.get("product_type") or "the confirmed product"
    details = []
    for key in ("product_type", "product_model", "model", "product_color",
                "color", "features", "usps", "specs"):
        value = facts.get(key)
        if value:
            details.append("%s=%s" % (key, json.dumps(value, ensure_ascii=False)))
    detail_text = "; ".join(details)
    return (
        "Product identity lock: every panel must copy the exact same product from the confirmed product board. "
        "Product name: %s. %s "
        "Preserve its true silhouette, volume, proportions, material, color, seams, buttons, ports, logos, grille/opening pattern, "
        "surface curvature and all visible functional parts. Do not simplify, flatten, round off, change category, add/remove modules, "
        "or replace it with a generic object. 产品身份锁定：每一格都必须严格继承已确认产品板中的同一件产品，"
        "不得因使用姿势而改变造型、比例、颜色、按钮、接口、Logo、孔位或品类。 "
        % (product_name, detail_text)
    )


def _usage_action_shot(plan, shot=None):
    """Pick the shot that best represents the required product-use action."""
    shot = dict(shot or {})
    if shot and (_has_structured_use_relation(shot) or _has_physical_use_action(shot)):
        return shot
    candidates = []
    for index, candidate in enumerate(plan.get("shots") or []):
        if not isinstance(candidate, dict):
            continue
        score = 0
        if _has_structured_use_relation(candidate):
            score += 100
        text = _physical_use_trigger_text(candidate)
        if _has_physical_use_action(candidate):
            score += 20
        if any(term in text for term in ("磁吸", "吸附", "magnetic", "magnet", "magsafe")):
            score += 20
        if any(term in text for term in (
                "底部", "底座", "bottom", "base",
                "背面", "手机背", "phone back", "smartphone back")):
            score += 20
        if any(term in text for term in (
                "贴合", "贴向", "贴到", "接触", "contact", "flush", "attach", "snap")):
            score += 10
        if any(term in text for term in ("支撑", "支架", "stand", "support")):
            score += 5
        if score:
            candidates.append((score, -index, candidate))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2]
    if shot.get("character_action") or shot.get("action") or shot.get("visual"):
        return shot
    return shot


def _usage_contract_text(plan, shot=None):
    facts = _plan_product_facts(plan, shot)
    parts = [json.dumps(facts, ensure_ascii=False)]
    for source in (shot or {}, plan):
        if isinstance(source, dict):
            parts.extend(str(source.get(key, "")) for key in (
                "product_name", "product_type", "visual", "character_action",
                "action", "props", "prop_prompts", "features", "specs"))
    return " ".join(parts).lower()


def product_usage_outcome_context(plan, primary_shot=None):
    """Collect downstream use-result beats for the usage board.

    The physical relation contract answers "what touches what"; this context
    answers "what practical selling-point result must the image prove".
    Keep it generic so any product category can contribute outcomes through
    shot visual/action/props fields.
    """
    lines = []
    facts = _plan_product_facts(plan, primary_shot)
    for key, label in (("features", "feature"), ("usps", "usp"),
                       ("selling_points", "selling_point"),
                       ("key_messages", "key_message")):
        value = facts.get(key) if key in facts else plan.get(key)
        if isinstance(value, dict):
            for name, detail in value.items():
                lines.append("%s:%s=%s" % (label, name, detail))
        elif isinstance(value, (list, tuple)):
            for item in value:
                lines.append("%s:%s" % (label, item))
        elif value:
            lines.append("%s:%s" % (label, value))
    specs = facts.get("specs") or plan.get("specs") or {}
    if isinstance(specs, dict):
        for name, detail in specs.items():
            lines.append("spec:%s=%s" % (name, detail))
    elif specs:
        lines.append("spec:%s" % specs)
    primary_id = (primary_shot or {}).get("id")
    for candidate in (plan.get("shots") or []):
        if primary_id and candidate.get("id") == primary_id:
            include = True
        else:
            include = _has_physical_use_action(candidate)
        if not include:
            continue
        bits = []
        for key in ("id", "visual", "character_action", "action", "props", "scene_prompt"):
            value = candidate.get(key)
            if value:
                bits.append("%s=%s" % (key, value))
        if bits:
            lines.append("; ".join(bits))
    deduped = []
    seen = set()
    for line in lines:
        if line not in seen:
            deduped.append(line)
            seen.add(line)
    return deduped[:10]


def product_usage_geometry_contract(plan, shot=None):
    """Return a named physical geometry contract for fragile use relations."""
    selected = _usage_action_shot(plan, shot)
    text = _usage_contract_text(plan, selected)
    magnetic = any(term in text for term in ("磁吸", "吸附", "magnetic", "magnet"))
    receiver_back = any(term in text for term in (
        "背面", "back plane", "phone back", "smartphone back", "手机背", "手机的背", "手机背面"))
    product_bottom = any(term in text for term in (
        "底部", "底座", "bottom", "base"))
    if magnetic and receiver_back and product_bottom:
        return "bottom_surface_magnetic_attach_to_receiver_back"
    return None


def _as_relation_dict(value):
    if not isinstance(value, dict):
        return {}
    aliases = {
        "relation_type": ("relation_type", "type", "verb", "action"),
        "active_object": ("active_object", "product", "object", "moving_object"),
        "receiver_object": ("receiver_object", "target", "carrier", "receiving_object", "body_part"),
        "product_contact_surface": (
            "product_contact_surface", "contact_surface", "object_contact_surface",
            "product_surface", "moving_object_surface"),
        "receiver_contact_surface": (
            "receiver_contact_surface", "target_contact_surface", "carrier_contact_surface",
            "receiving_surface", "body_contact_surface"),
        "outward_surface": (
            "outward_surface", "visible_surface", "control_surface", "non_contact_surface"),
        "final_state": ("final_state", "result", "visible_result"),
        "forbidden": ("forbidden", "negative", "forbidden_orientations", "must_not"),
    }
    result = {}
    for key, names in aliases.items():
        for name in names:
            if value.get(name):
                result[key] = value.get(name)
                break
    return result


def product_usage_physical_relation(plan, shot=None):
    """Normalize product-use spatial mechanics before any image generation.

    Preferred input is a structured ``use_relation``/``physical_relation`` dict
    on the shot or plan. Older plans fall back to conservative extraction for
    known high-risk verbs such as magnetic attach, clip, wear, plug and dock.
    """
    selected = _usage_action_shot(plan, shot)
    for source in (selected, plan):
        if isinstance(source, dict):
            for key in ("use_relation", "physical_relation", "usage_relation",
                        "interaction_relation", "product_use_relation"):
                relation = _as_relation_dict(source.get(key))
                if relation:
                    relation.setdefault("source", key)
                    return relation
    text = _usage_contract_text(plan, selected)
    action_text = _physical_use_trigger_text(selected)
    contract = product_usage_geometry_contract(plan, selected)
    facts = _plan_product_facts(plan, selected)
    product_name = facts.get("product_name") or facts.get("product_type") or "confirmed product"
    if contract == "bottom_surface_magnetic_attach_to_receiver_back":
        return {
            "source": "auto:magnetic-bottom-surface-to-receiver-back",
            "relation_type": "magnetic_attach",
            "active_object": product_name,
            "receiver_object": "smartphone" if any(term in text for term in ("手机", "phone", "smartphone")) else "receiver object",
            "product_contact_surface": "bottom/base magnetic surface",
            "receiver_contact_surface": "receiver back plane",
            "outward_surface": "non-contact visible/operable product surface",
            "final_state": "product bottom/base sits flush on the receiver back, product body protrudes outward",
            "forbidden": [
                "front/back/side/control/display/decorative face attached when bottom/base is specified",
                "product redesigned as a flat patch, case, badge, puck, different category or decorative sticker",
                "contact plane hidden or swapped",
            ],
        }
    high_risk = any(term in action_text for term in (
        "attach", "magnetic", "magnet", "磁吸", "吸附", "贴合", "贴到", "佩戴",
        "wear", "clip", "夹", "clamp", "dock", "支架", "stand", "plug", "插入",
        "connect", "连接", "mount", "安装"))
    if not high_risk:
        return {}
    receiver = ""
    if any(term in action_text for term in ("手机", "phone", "smartphone")):
        receiver = "smartphone"
    elif any(term in action_text for term in ("耳", "ear")):
        receiver = "outer ear / ear area"
    elif any(term in action_text for term in ("桌", "desktop", "table")):
        receiver = "desktop/table surface"
    product_surface = "the named or visibly functional product contact surface, never an arbitrary face"
    receiver_surface = "the named receiving surface/body area in the script"
    if any(term in action_text for term in ("底部", "底座", "bottom", "base")):
        product_surface = "bottom/base contact surface"
    if any(term in action_text for term in ("背面", "phone back", "smartphone back", "手机背")):
        receiver_surface = "smartphone back plane"
    return {
        "source": "auto:generic-high-risk-use",
        "relation_type": "physical_use_or_attachment",
        "active_object": product_name,
        "receiver_object": receiver or "the target/carrier object or body part named in the shot",
        "product_contact_surface": product_surface,
        "receiver_contact_surface": receiver_surface,
        "outward_surface": "the product surface that must remain visible/operable after contact",
        "final_state": "the final frame must visibly prove the intended use relation without swapping contact faces",
        "forbidden": [
            "swapping the product contact surface with the visible/control surface",
            "attaching a random product side when a specific surface is named",
            "turning the product into another category or a flat decorative patch",
        ],
    }


def _physical_relation_lock(plan, shot=None):
    relation = product_usage_physical_relation(plan, shot)
    if not relation:
        return ""
    forbidden = relation.get("forbidden") or []
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    return (
        "[PRODUCT-USE PHYSICAL RELATION CONTRACT / 产品使用物理关系合同]\n"
        "Resolve the use action as a rigid mechanical relation before drawing. "
        "This contract overrides generic posing and generic product-on-target imagery.\n"
        "- Relation type: %s\n"
        "- Active product/object: %s\n"
        "- Receiving object/body/scene part: %s\n"
        "- Product contact surface: %s\n"
        "- Receiver contact surface: %s\n"
        "- Product outward/visible/operable surface after contact: %s\n"
        "- Required final state: %s\n"
        "- Required proof panels: include separated-before-contact, alignment-before-contact, side/edge contact-line proof, post-contact result, and real-use result. The viewer must be able to name which two surfaces touch.\n"
        "- Rejection rule: if the contact surfaces are swapped, hidden, inferred from the wrong face, or the product category/shape changes, the image is invalid.\n"
        "- Forbidden: %s\n"
        "中文：先把动作解析成机械装配关系，再生成画面。必须明确“哪个产品面”接触“哪个目标面”，并保留产品外露/可操作面；禁止把接触面、展示面、控制面互换。\n"
        % (
            relation.get("relation_type") or "physical_use",
            relation.get("active_object") or "confirmed product",
            relation.get("receiver_object") or "target object/body part",
            relation.get("product_contact_surface") or "specified product contact surface",
            relation.get("receiver_contact_surface") or "specified receiver contact surface",
            relation.get("outward_surface") or "visible/control surface",
            relation.get("final_state") or "correct visible use result",
            "; ".join(str(item) for item in forbidden) or "wrong contact face or impossible use relation",
        )
    )


def _surface_attachment_lock(relation):
    relation = relation or {}
    forbidden = relation.get("forbidden") or []
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    return (
        "[MANDATORY SURFACE ATTACHMENT GEOMETRY / 表面连接几何硬合同]\n"
        "This is a product-agnostic attachment contract, not a category-specific rule. "
        "The product contact surface is the ONLY surface allowed to touch the receiver contact surface. "
        "Product contact surface: %s. Receiver contact surface: %s. "
        "After contact, the required outward/visible/operable surface is: %s. "
        "Required final state: %s. "
        "Show side/edge contact-line proof in multiple panels so the viewer can identify exactly which two surfaces touch. "
        "Forbidden mistakes: %s. Do not swap the contact surface with the display/control/decorative/visible surface; do not hide the contact plane; do not flatten or redesign the product into another category. "
        "The receiver is a receiving plane, not a platform, tray, shelf, top edge, base or desktop. The active product must protrude outward from that receiving plane after its contact surface is flush against the receiver contact surface. "
        "Never show the active product standing, sitting, resting, balanced, stacked, perched, leaning or placed on top of the receiver, on its edge, or on its front/display side. "
        "中文硬约束：这是通用表面连接合同，不是某个物品的专用规则。只有“产品接触面”可以接触“目标接触面”；产品外露/可操作面必须保持朝外可见。禁止把接触面、展示面、控制面、装饰面互换，禁止把产品画成其它品类或扁平贴片。\n"
        "中文补充：目标物是承载平面，不是托盘/桌面/上沿/底座；产品必须由接触面贴合该平面并向外突出，禁止画成站在、立在、放在、搁在、靠在目标物上。\n"
        % (
            relation.get("product_contact_surface") or "specified product contact surface",
            relation.get("receiver_contact_surface") or "specified receiver contact surface",
            relation.get("outward_surface") or "specified visible/operable surface",
            relation.get("final_state") or "correct visible attachment result",
            "; ".join(str(item) for item in forbidden) or "wrong contact face or impossible attachment",
        )
    )


def _asset_composition_brief_text(plan, asset_id):
    briefs = (plan or {}).get("_asset_composition_briefs") or {}
    brief = briefs.get(asset_id) if isinstance(briefs, dict) else None
    if not isinstance(brief, dict):
        return ""
    panel_plan = brief.get("panel_plan") or []
    lines = [
        "[MODEL-GENERATED COMPOSITION BRIEF / 模型构图提示词 skill 输出]",
        "Use this only as composition execution guidance. It cannot override product identity, the physical relation contract, contact surfaces, receiver object, character identity, or no-text rules.",
    ]
    for key, label in (
            ("composition_strategy", "Composition strategy"),
            ("primary_subject_scope", "Primary subject scope"),
            ("camera_scope", "Camera scope"),
            ("range_limits", "Range limits")):
        if brief.get(key):
            lines.append("- %s: %s" % (label, brief[key]))
    if panel_plan:
        lines.append("- Nine-panel composition plan:")
        for index, panel in enumerate(panel_plan, 1):
            if isinstance(panel, dict):
                bits = []
                for key in ("shot_size", "composition", "must_show", "proof_goal", "forbidden"):
                    if panel.get(key):
                        bits.append("%s=%s" % (key, panel[key]))
                lines.append("  %d. %s" % (index, "; ".join(bits) or json.dumps(panel, ensure_ascii=False)))
            elif panel:
                lines.append("  %d. %s" % (index, panel))
    for key, label in (("must_include", "Must include"),
                       ("must_exclude", "Must exclude")):
        values = brief.get(key) or []
        if isinstance(values, str):
            values = [values]
        if values:
            lines.append("- %s: %s" % (label, "; ".join(str(v) for v in values)))
    return "\n".join(lines) + "\n"


def product_usage_prompt(plan, shot=None, include_human=True):
    """Build the physical interaction lock for product-in-use reference imagery."""
    # When called without a specific shot (board-level usage image), fall back
    # to the most action-specific shot in the plan so the model focuses on the
    # core interaction (e.g. magnetic snap-on) instead of generic posing.
    shot = _usage_action_shot(plan, shot)
    facts = _plan_product_facts(plan, shot)
    product_name = facts.get("product_name") or facts.get("product_type") or "the exact uploaded product"
    action = (shot.get("character_action") or shot.get("action") or shot.get("visual") or
              "the person actively and correctly uses the product for its intended purpose")
    subject = (
        "Every panel must show the same confirmed digital human actively using the same confirmed product. "
        if include_human else
        "No digital human is configured. Show the same confirmed product in nine physically credible usage contexts, using only anonymous hands or operator POV when needed; never invent a recognizable person. "
    )
    reference_priority = (
        "The confirmed digital-human board and confirmed product board are the primary subjects of every panel. "
        "The confirmed product board is the highest-priority visual identity anchor; every panel must preserve "
        "the exact product from that board. Any supplied wearing-position reference is only a pose and fit guide; "
        "do not replace the confirmed person or confirmed product with the reference image's person or product. "
    )
    action_line = (
        "Show the person ACTIVELY AND CORRECTLY USING %s, not merely posing beside it. "
        % product_name
        if include_human else
        "Show the exact product performing or being operated for its intended use; do not add a recognizable person. "
    )
    required_action = "Required action: %s. " % action
    shape_lock = _product_identity_lock(facts)
    geometry_contract = product_usage_geometry_contract(plan, shot)
    geometry_lock = ""
    panel_order = (
        "Panel order: establish, front interaction, left interaction, right interaction, "
        "over-shoulder operation, hand/contact close-up, control detail, action result, wider context. "
    )
    relation = product_usage_physical_relation(plan, shot)
    if geometry_contract == "bottom_surface_magnetic_attach_to_receiver_back":
        geometry_lock = _surface_attachment_lock(relation)
        receiver_name = relation.get("receiver_object") or "receiver object"
        receiver_surface = relation.get("receiver_contact_surface") or "receiver contact surface"
        product_surface = relation.get("product_contact_surface") or "product contact surface"
        outward_surface = relation.get("outward_surface") or "outward visible/operable product surface"
        subject = (
            "This is a close-up physical attachment operation board, not a presenter portrait or generic lifestyle usage board. "
            "Every panel must prioritize the confirmed product, the receiver object, the two named contact surfaces, and cropped operator hands only when needed. "
            "A full digital-human face/body may appear in at most one wider context panel; do not center the presenter. "
        )
        reference_priority = (
            "The confirmed product board is the highest-priority visual identity anchor. The confirmed digital-human board is only a secondary hand/operator identity reference. "
            "For this attachment board, the primary subject in every proof panel is the mechanical relation between the same product and the receiver object. "
            "Do not turn the board into presenter portraits, handheld beauty shots, tabletop solo product shots, or app-control phone screen shots. "
        )
        panel_order = (
            "Panel order for this surface attachment: 1 product and receiver separated, clearly showing the product contact surface; "
            "2 hand aligns the product contact surface toward the receiver contact surface; 3 side/edge view just before contact; "
            "4 side/edge view after attachment, contact surfaces flush and product volume protruding outward if applicable; "
            "5 real use with product attached to the receiver; 6 macro contact line; "
            "7 outward visible/operable product surface; 8 stability/action result; "
            "9 wider context proving the product remains the same object from the confirmed product board. "
        )
        action_line = (
            "This board is NOT a generic lifestyle usage board. At least seven of nine panels must visibly prove the same surface-attachment relation: "
            "the active product's specified contact surface touches the receiver object's specified contact surface, while the product's outward/visible/operable surface stays facing outward. "
            "At least seven panels must include BOTH the product and the receiver object in the same frame, with the receiver contact surface visible or inferable from a side/edge contact-line view. "
        )
        required_action = (
            "Required attachment action: %s. Product contact surface=%s. Receiver object=%s. Receiver contact surface=%s. Outward/visible product surface=%s. "
            "Do not replace this with handheld posing, button pressing, tabletop product beauty shots, a receiver front-screen/control-app scene, or a standalone presenter lifestyle scene. "
            % (action, product_surface, receiver_name, receiver_surface, outward_surface)
        )
    relation_lock = _physical_relation_lock(plan, shot)
    composition_brief = _asset_composition_brief_text(plan, "product_usage_image")
    outcome_context = product_usage_outcome_context(plan, shot)
    outcome_block = ""
    if outcome_context:
        outcome_block = (
            "[USAGE OUTCOME / 使用结果与卖点证明]\n"
            "The board must prove not only the physical contact relation, but also the practical use result/selling point that follows from it. "
            "Use these approved script beats as outcome constraints; do not invent unrelated uses:\n"
            + "\n".join("  - %s" % item for item in outcome_context)
            + "\n"
        )
    return "".join((
        "[PRODUCT-IN-USE NINE-PANEL BOARD]\n",
        "Create one 16:9 landscape 3x3 board with exactly nine distinct panels. ",
        subject,
        reference_priority,
        panel_order,
        action_line, required_action,
        outcome_block,
        composition_brief,
        shape_lock,
        relation_lock,
        geometry_lock,
        "Preserve the exact uploaded product geometry, scale, color, orientation and functional parts. "
        "Show anatomically plausible finger placement, all real contact points, grip pressure and occlusion; "
        "the product must remain on the correct side and in the correct operating position. Hands must have exactly five fingers each: "
        "no extra fingers, fused fingers, missing fingers, duplicated hands, floating product, hand-product intersection, "
        "skin passing through the product, or impossible contact. The visible result of the action must match the intended use. "
        "No captions, subtitles, non-product text overlays, watermarks, extra logos or extra product. "
        "Preserve genuine logos, labels, buttons and markings that are physically printed on the confirmed product."
    ))


def asset_prompt_review_items(plan):
    """Return non-shot image prompts that must be reviewed before paid generation."""
    items = []
    if needs_product_board(plan):
        import product_board as _product_board
        facts = _plan_product_facts(plan)
        facts.setdefault("product_type",
                         plan.get("product_type") or
                         ((plan.get("asset_refs") or {}).get("product_type")) or
                         "the exact product")
        if plan.get("product_color"):
            facts.setdefault("product_color", plan.get("product_color"))
        facts.setdefault("style_hint",
                         plan.get("visual_style") or
                         "commercial product reference photography")
        prompt = _product_board.product_board_prompt(facts)
        items.append({
            "asset_id": "product_board",
            "kind": "product_board",
            "stage": "storyboard_asset",
            "submission_prompt_zh": prompt,
            "negative_prompt_zh": "错误品类、错误材质、错误颜色、错误按钮/接口/Logo、错误尺寸比例、人物或使用场景",
            "policy_version": _product_board.PRODUCT_BOARD_POLICY_VERSION,
        })
    if needs_product_usage_image(plan):
        has_human = bool(plan.get("characters") or any(
            shot.get("characters") or shot.get("character_prompt") or
            shot.get("character_action") or shot.get("digital_human") or
            shot.get("actor") for shot in plan.get("shots") or []))
        usage_shot = _usage_action_shot(plan)
        prompt = product_usage_prompt(plan, usage_shot, include_human=has_human)
        item = {
            "asset_id": "product_usage_image",
            "kind": "product_usage_image",
            "stage": "storyboard_asset",
            "submission_prompt_zh": prompt,
            "negative_prompt_zh": "错误接触面、错误朝向、错误外露面、产品形变、手部畸形、人物或产品替换",
            "policy_version": PRODUCT_USAGE_POLICY_VERSION,
            "physical_relation_contract": product_usage_physical_relation(plan, usage_shot),
            "usage_outcome_context": product_usage_outcome_context(plan, usage_shot),
        }
        geometry = product_usage_geometry_contract(plan, usage_shot)
        if geometry:
            item["geometry_contract"] = geometry
        items.append(item)
    for item in items:
        item["prompt_fingerprint"] = hashlib.sha256(
            json.dumps(item, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:16]
    return items


def load_plan_json(plan_path):
    """读取并解析 storyboard_plan.json，语法错误时给出人话定位（铁律#14：错误说人话）。

    裸 json.load() 抛出的 JSONDecodeError 只有字符偏移量，Agent/客户都难以
    直接定位到剧本文件里具体哪一行漏了引号/逗号。这里补上：出错行号、出错列号、
    该行原文 + 列指示箭头，让排查一次到位，而不是让 agent 去猜「第几行」。
    """
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
        # 附带出错行的前一行，多数「漏引号/漏逗号」的真实报错点在上一行的行尾
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




def _approval_path(out_dir, kind):
    return os.path.join(out_dir, ".%s_confirmed.json" % kind)


def _asset_registry_path(client):
    """Return the per-client registry without accepting a path as a client id."""
    return os.path.join(ROOT, "assets", safe_name(client).lower(), "asset_registry.json")


def _asset_registry_key(kind, source_fingerprint):
    return "%s:%s" % (kind, source_fingerprint)


def _load_asset_registry(client):
    try:
        with open(_asset_registry_path(client), encoding="utf-8") as handle:
            registry = json.load(handle)
        if isinstance(registry, dict) and isinstance(registry.get("assets"), dict):
            return registry
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"version": 1, "assets": {}}


def _save_asset_registry(client, registry):
    path = _asset_registry_path(client)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    Path(tmp).write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _registered_board(client, kind, source_fingerprint):
    """Return a still-valid confirmed board from another run, if available."""
    entry = _load_asset_registry(client).get("assets", {}).get(
        _asset_registry_key(kind, source_fingerprint))
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if (entry.get("kind") != kind or
            entry.get("source_fingerprint") != source_fingerprint or
            not path or not os.path.isfile(path) or
            entry.get("board_sha256") != _file_sha256(path)):
        return None
    return dict(entry, path=os.path.abspath(path), abspath=os.path.abspath(path),
                status="confirmed", reused_from_registry=True, skipped=True)


def _approval_record(out_dir, kind):
    try:
        with open(_approval_path(out_dir, kind), encoding="utf-8") as handle:
            record = json.load(handle)
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _remote_media_url(item):
    if not isinstance(item, dict):
        return None
    for key in ("url", "result_url", "remote_url", "download_url", "imageUrl"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    values = item.get("imageUrls")
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None


def _backup_existing_board(path, out_dir, kind):
    if not path or not os.path.isfile(path):
        return None
    backup_dir = os.path.join(out_dir, ".force_regen_backups")
    os.makedirs(backup_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    backup = os.path.join(
        backup_dir, "%s_%s%s" %
        (stem, datetime.now().strftime("%Y%m%d%H%M%S"), ext or ".jpg"))
    shutil.move(path, backup)
    approval = _approval_path(out_dir, kind)
    if os.path.exists(approval):
        os.remove(approval)
    return os.path.abspath(backup)


def _clear_generated_media_fields(record):
    for field in ("url", "result_url", "remote_url", "download_url", "imageUrl",
                  "imageUrls", "task_id", "taskId", "request_id", "sha256",
                  "board_sha256", "confirmed_source_fingerprint"):
        record.pop(field, None)


def _mark_board_regeneration_pending(results, key, *, kind, path,
                                     source_fingerprint, backup_path=None,
                                     reason="force_regeneration"):
    """Replace stale completed board metadata before submitting a new task."""
    previous = results.get(key) if isinstance(results.get(key), dict) else {}
    superseded = {
        "status": previous.get("status"),
        "path": os.path.abspath(previous.get("path") or previous.get("abspath") or path),
        "source_fingerprint": previous.get("source_fingerprint"),
        "cleared_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
    }
    for field in ("url", "result_url", "remote_url", "download_url", "imageUrl",
                  "task_id", "taskId", "request_id", "sha256", "board_sha256"):
        if previous.get(field):
            superseded[field] = previous[field]
    if backup_path:
        superseded["previous_backup"] = os.path.abspath(backup_path)
    pending = dict(previous)
    _clear_generated_media_fields(pending)
    pending.update({
        "path": os.path.abspath(path),
        "abspath": os.path.abspath(path),
        "source_fingerprint": source_fingerprint,
        "status": "pending_regeneration",
        "superseded": superseded,
        "regeneration_started_at": datetime.now().isoformat(timespec="seconds"),
    })
    results[key] = pending
    results["stage"] = key
    results["needs_confirmation"] = True
    results.pop("in_progress", None)
    approval = _approval_path(os.path.dirname(os.path.abspath(path)), kind)
    if os.path.exists(approval):
        os.remove(approval)
    return pending


def _register_confirmed_board(client, kind, record):
    """Persist a confirmed board only after its content-bound approval succeeds."""
    if kind not in ("product", "cast") or not client:
        return
    entry = {
        "kind": kind,
        "source_fingerprint": record["source_fingerprint"],
        "path": record["path"],
        "board_sha256": record["board_sha256"],
        "model": record.get("model"),
        "confirmed_at": record["confirmed_at"],
    }
    for key in ("url", "result_url", "task_id", "request_id"):
        if record.get(key):
            entry[key] = record[key]
    registry = _load_asset_registry(client)
    registry["assets"][_asset_registry_key(kind, record["source_fingerprint"])] = entry
    _save_asset_registry(client, registry)


def _approval_current(out_dir, kind, source_fingerprint):
    try:
        with open(_approval_path(out_dir, kind), encoding="utf-8") as handle:
            record = json.load(handle)
        if (record.get("status") != "confirmed" or
                record.get("source_fingerprint") != source_fingerprint):
            return False
        board_path = record.get("path")
        board_sha256 = record.get("board_sha256")
        if board_sha256:
            if not bool(board_path and os.path.isfile(board_path) and
                        _file_sha256(board_path) == board_sha256):
                return False
            if kind == "usage":
                refs = record.get("identity_reference_paths") or []
                if ((record.get("geometry_contract") or
                     record.get("physical_relation_contract")) and
                        not record.get("geometry_reviewed")):
                    return False
                return bool(refs) and all(os.path.isfile(path) for path in refs)
            return True
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _usage_identity_reference_paths(board):
    paths = list(board.get("identity_reference_paths") or [])
    refinement = board.get("refinement") or {}
    paths.extend(refinement.get("identity_reference_paths") or [])
    result = []
    seen = set()
    for path in paths:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if abs_path not in seen:
            result.append(abs_path)
            seen.add(abs_path)
    return result


def _validate_usage_board_identity_references(board):
    refs = _usage_identity_reference_paths(board)
    if not refs:
        raise br_client.BRError(
            "USAGE_IDENTITY_REFERENCES_REQUIRED: 产品使用图缺少产品身份锚点记录，"
            "请用最新 storyboard.py --stage next 或 --refine-board usage 重新生成后再确认。")
    missing = [path for path in refs if not os.path.isfile(path)]
    if missing:
        raise br_client.BRError(
            "USAGE_IDENTITY_REFERENCE_MISSING: 产品使用图记录的产品身份锚点文件不存在：%s。"
            "请重新生成产品使用图。" % ", ".join(missing))
    return refs


def confirm_board(result_json, kind, geometry_reviewed=False):
    """Confirm one generated board and bind approval to its source fingerprint."""
    with open(result_json, encoding="utf-8") as handle:
        results = json.load(handle)
    keys = {
        "product": ("product_board", "产品板"),
        "cast": ("cast_board", "人物板"),
        "usage": ("product_usage_image", "产品使用图"),
    }
    if kind not in keys:
        raise br_client.BRError("不支持的确认类型: %s" % kind)
    key, label = keys[kind]
    board = results.get(key)
    if not board:
        raise br_client.BRError("没有可确认的%s。" % label)
    path = board.get("path") or board.get("abspath")
    if not path or not os.path.isfile(path):
        raise br_client.BRError("待确认板文件不存在: %s" % path)
    fingerprint = board.get("source_fingerprint")
    if not fingerprint:
        raise br_client.BRError("待确认板缺少源素材指纹，请重新生成后再确认。")
    out_dir = os.path.dirname(os.path.abspath(result_json))
    try:
        path = require_contained_path(
            out_dir, path, label="storyboard_board", must_exist=True)
    except ValueError as exc:
        raise br_client.BRError(str(exc)) from exc
    identity_refs = []
    if kind == "usage":
        identity_refs = _validate_usage_board_identity_references(board)
        if (board.get("geometry_contract") or board.get("physical_relation_contract")) and not geometry_reviewed:
            raise br_client.BRError(
                "USAGE_GEOMETRY_REVIEW_REQUIRED: 这张产品使用图带有物理使用关系合同 "
                "(%s)，确认前必须人工复核产品与目标物/身体的接触面、方向和使用结果。"
                "请展示图片并确认无误后，使用 --geometry-reviewed 再确认。"
                % (board.get("geometry_contract") or
                   (board.get("physical_relation_contract") or {}).get("relation_type") or
                   "physical_relation"))
    remote_url = _remote_media_url(board)
    if not remote_url:
        raise br_client.BRError(
            "%s缺少 BasicRouter 图片 retrieve URL，不能确认进入视频链路；"
            "请用 --force-board 或 --only-shot 重新生成该图。" % label)
    record = {"status": "confirmed", "kind": kind,
               "source_fingerprint": fingerprint,
              "path": os.path.abspath(path),
              "board_sha256": _file_sha256(path),
              "client": results.get("client"),
              "run_id": results.get("run_id"),
              "plan_fingerprint": results.get("plan_fingerprint"),
              "model": results.get("model"),
              "confirmed_at": datetime.now().isoformat(timespec="seconds"),
              "url": remote_url}
    for field in ("url", "result_url", "task_id", "request_id"):
        if board.get(field):
            record[field] = board[field]
    if identity_refs:
        record["identity_reference_paths"] = identity_refs
    if board.get("geometry_contract"):
        record["geometry_contract"] = board["geometry_contract"]
        record["geometry_reviewed"] = bool(geometry_reviewed)
    if board.get("physical_relation_contract"):
        record["physical_relation_contract"] = board["physical_relation_contract"]
        record["geometry_reviewed"] = bool(geometry_reviewed)
    if board.get("usage_policy_version"):
        record["usage_policy_version"] = board["usage_policy_version"]
    tmp = _approval_path(out_dir, kind) + ".tmp"
    Path(tmp).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _approval_path(out_dir, kind))
    board["status"] = "confirmed"
    board["confirmed_source_fingerprint"] = fingerprint
    tmp_result = result_json + ".tmp"
    Path(tmp_result).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_result, result_json)
    _register_confirmed_board(results.get("client"), kind, record)
    return record


def refine_board(result_json, kind, edit_prompt, *, feedback_refs=None, model=None):
    """Regenerate a pending board from customer feedback without advancing gates.

    Storyboard stage boards are not normal asset_prep candidates: downstream
    code expects stable filenames such as ``product_usage_board.jpg``. Refining
    a board therefore backs up the currently displayed file, replaces the same
    fixed path with the new candidate, and removes the old approval marker so
    the customer must review the revised image before the next stage can run.
    """
    keys = {
        "product": ("product_board", "产品板"),
        "cast": ("cast_board", "人物板"),
        "usage": ("product_usage_image", "产品使用图"),
    }
    if kind not in keys:
        raise br_client.BRError("不支持的精修类型: %s" % kind)
    if not edit_prompt or not str(edit_prompt).strip():
        raise br_client.BRError("精修必须提供 --edit 修改说明。")
    with open(result_json, encoding="utf-8") as handle:
        results = json.load(handle)
    key, label = keys[kind]
    board = results.get(key)
    if not board:
        raise br_client.BRError("没有可精修的%s。" % label)
    out_dir = os.path.dirname(os.path.abspath(result_json))
    board_path = board.get("path") or board.get("abspath")
    try:
        board_path = require_contained_path(
            out_dir, board_path, label="storyboard_board", must_exist=True)
    except ValueError as exc:
        raise br_client.BRError(str(exc)) from exc

    key_setup.ensure_session_id()
    api_key = key_setup.load_key()
    if not api_key:
        raise br_client.BRError("no API key; run key onboarding first")

    feedback_paths = []
    for ref in ([feedback_refs] if isinstance(feedback_refs, str)
                else list(feedback_refs or [])):
        candidate = ref if os.path.isabs(ref) else os.path.join(ROOT, ref)
        if not os.path.isfile(candidate):
            raise br_client.BRError("feedback image not found: %s" % ref)
        feedback_paths.append(os.path.abspath(candidate))

    # BasicRouter image refs are limited. For product-in-use refinements the
    # confirmed product board must be the strongest identity anchor; otherwise
    # a previously drifted usage board can teach the model the wrong shape.
    reference_paths = []
    identity_reference_paths = []
    if kind == "usage":
        plan_for_refs = {}
        plan_path = results.get("plan_source")
        if plan_path and os.path.isfile(plan_path):
            try:
                plan_for_refs = load_plan_json(plan_path)
            except Exception:
                plan_for_refs = {}
        identity_reference_paths = _confirmed_product_identity_paths(
            plan_for_refs, results, client=results.get("client"), limit=2)
        reference_paths.extend(identity_reference_paths)
        reference_paths.append(board_path)
        reference_paths.extend(feedback_paths)
        cast = (results.get("cast_board") or {}).get("path") or (results.get("cast_board") or {}).get("abspath")
        if cast and os.path.isfile(cast) and os.path.abspath(cast) not in {
                os.path.abspath(p) for p in reference_paths}:
            reference_paths.append(cast)
    else:
        reference_paths = [board_path]
        reference_paths.extend(feedback_paths)
    reference_paths = reference_paths[:4]
    image_refs = _collect_image_urls(
        reference_paths, api_key, fail_on_invalid=True, label="%s精修参考图" % label)

    product_identity_lock = ""
    if kind == "usage":
        facts = {}
        plan_path = results.get("plan_source")
        if plan_path and os.path.isfile(plan_path):
            try:
                facts = _plan_product_facts(load_plan_json(plan_path))
            except Exception:
                facts = {}
        product_identity_lock = (
            "The first product reference images are the confirmed product identity anchors and have the highest priority: "
            + _product_identity_lock(facts) +
            "The previous usage/pose board is only for human pose and phone attachment relation, "
            "but do not inherit any product-shape drift from it. "
            "Do not simplify the product into a flat puck, disk, badge, phone grip, charger, case, or generic object. "
        )
    prompt = (
        "Refine the displayed %s according to the customer's feedback. "
        "Keep the same board format, same confirmed product identity, same confirmed digital human identity, "
        "same clean commercial photography style, and the same general composition unless the feedback explicitly asks to change it. "
        "%s"
        "Only change the requested issue: %s. "
        "If feedback reference images are provided, use them only to understand the correction target; "
        "do not copy unrelated people, text, UI, borders, watermarks, logos, or backgrounds from feedback images. "
        "No subtitles, no added graphic text, no watermark."
        % (label, product_identity_lock, str(edit_prompt).strip())
    )

    backup_dir = os.path.join(out_dir, ".refine_backups")
    os.makedirs(backup_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(board_path))
    backup_path = os.path.join(
        backup_dir, "%s_%s%s" % (stem, datetime.now().strftime("%Y%m%d%H%M%S"), ext or ".jpg"))
    os.replace(board_path, backup_path)
    tmp_out = board_path + ".refine.jpg"
    if os.path.exists(tmp_out):
        os.remove(tmp_out)

    def refine_progress(event):
        if event.get("status") in ("submitted", "success", "failed"):
            print("[storyboard] refine %s: %s" % (kind, event.get("status")), flush=True)
        elif event.get("waited", 0) and event["waited"] % 30 == 0:
            print("[storyboard] refine %s still processing (%ss)" %
                  (kind, event["waited"]), flush=True)

    try:
        refined = download_first_image(
            api_key, prompt, tmp_out, model=model or results.get("model") or DEFAULT_MODEL,
            ratio="16:9", image_urls=image_refs, on_progress=refine_progress)
        os.replace(tmp_out, board_path)
    except Exception:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        if os.path.exists(backup_path) and not os.path.exists(board_path):
            os.replace(backup_path, board_path)
        raise

    approval_path = _approval_path(out_dir, kind)
    if os.path.exists(approval_path):
        os.remove(approval_path)
    previous_sha = board.get("sha256") or board.get("board_sha256")
    board.update(refined)
    board["path"] = os.path.abspath(board_path)
    board["abspath"] = os.path.abspath(board_path)
    board["sha256"] = _file_sha256(board_path)
    board["status"] = "pending"
    board.pop("confirmed_source_fingerprint", None)
    board["refinement"] = {
        "edit_prompt": str(edit_prompt).strip(),
        "feedback_refs": feedback_paths,
        "generation_reference_paths": [os.path.abspath(p) for p in reference_paths],
        "identity_reference_paths": [os.path.abspath(p) for p in identity_reference_paths],
        "previous_backup": os.path.abspath(backup_path),
        "previous_sha256": previous_sha,
        "refined_at": datetime.now().isoformat(timespec="seconds"),
    }
    results[key] = board
    results["stage"] = key
    results["needs_confirmation"] = True
    plan_path = results.get("plan_source")
    if plan_path and os.path.isfile(plan_path):
        try:
            plan = load_plan(plan_path)
            index_md, embedded_md, preview_html = write_index(plan, results, out_dir)
            results["index_md"] = index_md
            results["embedded_md"] = embedded_md
            results["preview_html"] = preview_html
        except Exception:
            pass
    tmp_result = result_json + ".tmp"
    Path(tmp_result).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_result, result_json)
    return {"status": "pending", "kind": kind, "path": os.path.abspath(board_path),
            "sha256": board["sha256"], "backup_path": os.path.abspath(backup_path),
            "feedback_refs": feedback_paths, "needs_confirmation": True}


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_shot_matches_plan(existing, shot, plan_fingerprint_value, out_path,
                                expected_reference_fingerprint=None):
    """Reuse a shot only when checkpoint metadata and bytes prove provenance."""
    if not isinstance(existing, dict) or not os.path.isfile(out_path):
        return False
    # New checkpoints are panel-scoped. Legacy checkpoints retain the stricter
    # whole-plan fallback because they cannot prove which visual fields matched.
    expected_shot_fp = shot_fingerprint(shot)
    recorded_shot_fp = existing.get("shot_fingerprint")
    if recorded_shot_fp:
        if recorded_shot_fp != expected_shot_fp:
            return False
        if (expected_reference_fingerprint and
                existing.get("reference_fingerprint") != expected_reference_fingerprint):
            return False
    elif existing.get("plan_fingerprint") != plan_fingerprint_value:
        return False
    if not recorded_shot_fp and (existing.get("shot") or {}) != shot:
        return False
    recorded_sha = existing.get("sha256")
    return bool(recorded_sha and recorded_sha == _file_sha256(out_path))


def _storyboard_approval_record(results, result_json):
    """Build a content-bound approval record for the complete shot storyboard."""
    result_json = os.path.abspath(result_json)
    out_dir = os.path.abspath(results.get("out_dir") or os.path.dirname(result_json))
    if os.path.dirname(result_json) != out_dir:
        raise br_client.BRError("故事板结果的 out_dir 与 result_json 所在目录不一致。")
    plan_fp = results.get("plan_fingerprint")
    client = results.get("client")
    run_id = results.get("run_id")
    if not plan_fp or not client or not run_id:
        raise br_client.BRError("故事板结果缺少 plan fingerprint、client 或 run id，请重新生成。")
    shots = []
    seen = set()
    for item in results.get("shots") or []:
        shot_id = str((item.get("shot") or {}).get("id") or "").strip()
        path = item.get("abspath") or item.get("path")
        path = os.path.abspath(path) if path else None
        if not shot_id or shot_id in seen:
            raise br_client.BRError("故事板 shot id 缺失或重复，不能整体确认。")
        if not path or not os.path.isfile(path):
            raise br_client.BRError("待确认故事板文件不存在: %s" % path)
        remote_url = _remote_media_url(item)
        if not remote_url:
            raise br_client.BRError(
                "故事板 shot %s 缺少 BasicRouter 图片 retrieve URL，不能确认进入视频链路；"
                "请用 --only-shot %s 重新生成该分镜。" % (shot_id, shot_id))
        try:
            path = require_contained_path(
                out_dir, path, label="storyboard_shot", must_exist=True)
        except ValueError as exc:
            raise br_client.BRError(str(exc)) from exc
        seen.add(shot_id)
        shot_fp = item.get("shot_fingerprint") or shot_fingerprint(item.get("shot") or {})
        shot_record = {"id": shot_id, "path": path, "sha256": _file_sha256(path),
                       "shot_fingerprint": shot_fp, "url": remote_url}
        for field in ("task_id", "request_id"):
            if item.get(field):
                shot_record[field] = item[field]
        shots.append(shot_record)
    if not shots:
        raise br_client.BRError("没有可确认的分段故事板。")
    expected_ids = [str(value) for value in (results.get("expected_shot_ids") or [])]
    if expected_ids and (len(expected_ids) != len(set(expected_ids)) or set(expected_ids) != seen):
        missing = sorted(set(expected_ids) - seen)
        extra = sorted(seen - set(expected_ids))
        raise br_client.BRError(
            "故事板不完整，不能整体确认。缺失=%s，多出=%s" % (missing, extra))
    return {
        "status": "confirmed",
        "kind": "storyboard",
        "client": client,
        "run_id": run_id,
        "out_dir": out_dir,
        "plan_fingerprint": plan_fp,
        "shots": shots,
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
    }


def confirm_storyboard(result_json):
    """Persist confirmation of the complete storyboard and all shot bytes."""
    result_json = os.path.abspath(result_json)
    results = _read_result(result_json)
    if not results:
        raise br_client.BRError("无法读取待确认的 storyboard_result.json。")
    record = _storyboard_approval_record(results, result_json)
    approval_path = _approval_path(record["out_dir"], "storyboard")
    tmp = approval_path + ".tmp"
    Path(tmp).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, approval_path)
    results["storyboard_approval"] = record
    results["needs_confirmation"] = False
    tmp_result = result_json + ".tmp"
    Path(tmp_result).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_result, result_json)
    return record


def storyboard_approval_is_current(result_json, *, client=None, run_id=None,
                                   out_dir=None, plan_fingerprint_value=None):
    """Re-read approval and shot files for downstream identity/generation gates."""
    result_json = os.path.abspath(result_json)
    results = _read_result(result_json)
    if not results:
        return False
    actual_out_dir = os.path.abspath(results.get("out_dir") or os.path.dirname(result_json))
    record = _read_result(_approval_path(actual_out_dir, "storyboard"))
    if not record or record.get("status") != "confirmed":
        return False
    expected = {
        "client": client if client is not None else results.get("client"),
        "run_id": run_id if run_id is not None else results.get("run_id"),
        "out_dir": os.path.abspath(out_dir) if out_dir is not None else actual_out_dir,
        "plan_fingerprint": (plan_fingerprint_value if plan_fingerprint_value is not None
                             else results.get("plan_fingerprint")),
    }
    # The aggregate plan identity may change for dialogue/audio-only edits. In
    # that case panel approvals remain sound when every approved image is bound
    # to the current panel visual fingerprint and unchanged bytes.
    identity_keys = ("client", "run_id", "out_dir")
    if any(record.get(key) != expected[key] for key in identity_keys):
        return False
    try:
        current = _storyboard_approval_record(results, result_json)
    except (OSError, br_client.BRError):
        return False
    if record.get("shots") != current.get("shots"):
        return False
    expected_plan = expected["plan_fingerprint"]
    if record.get("plan_fingerprint") == expected_plan:
        return True
    return bool(record.get("shots")) and all(
        item.get("shot_fingerprint") for item in record.get("shots")
    )


def render_storyboard(plan_path, out_dir, model=DEFAULT_MODEL, run_id=None, flat=False, bw=True,
                      stage="next", debug_allow_all=False, prompt_review=None,
                      only_shot_ids=None, force_boards=None):
    if stage == "all":
        raise br_client.BRError(
            "STAGE_ALL_BLOCKED: 生产流程禁止一次生成全部素材；请使用 --stage next 逐阶段推进。")
    if model != DEFAULT_MODEL:
        raise br_client.BRError(
            "STORYBOARD_MODEL_REQUIRED: 人物板、产品板和故事板必须使用 %s。" % DEFAULT_MODEL)
    only_shot_ids = set(str(value) for value in (only_shot_ids or []) if value)
    if only_shot_ids and stage != "storyboard":
        raise br_client.BRError("ONLY_SHOT_REQUIRES_STORYBOARD_STAGE")
    force_boards = set(str(value) for value in (force_boards or []) if value)
    unsupported_force = sorted(force_boards - {"cast", "usage"})
    if unsupported_force:
        raise br_client.BRError("UNSUPPORTED_FORCE_BOARD: %s" % ", ".join(unsupported_force))
    if force_boards and stage not in ("cast", "usage", "storyboard", "next"):
        raise br_client.BRError("FORCE_BOARD_STAGE_UNSUPPORTED: %s" % stage)
    key_setup.ensure_session_id()
    api_key = key_setup.load_key()
    if not api_key:
        raise br_client.BRError(ux.friendly_error("No API key. Run key onboarding first."))
    plan = load_plan_json(plan_path)
    from storyboard_validator import normalize_plan_motion_elements
    _, migrated_motion = normalize_plan_motion_elements(
        json.loads(json.dumps(plan, ensure_ascii=False)))
    if migrated_motion:
        print("[storyboard] 已将 %d 条文字/图形动效说明移到 motion_elements，" 
              "仅供底片生成后 HyperFrames 使用" % len(migrated_motion), flush=True)
    plan = expand_product_sku_refs(canonical_storyboard_plan(plan))
    requested_base_out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    # Use the plan's aspect ratio, not hardcoded 16:9. A 9:16 vertical project
    # must generate 9:16 storyboards so the composition matches the final video.
    ratio = plan.get("aspect_ratio") or "16:9"
    if run_id and not only_shot_ids and os.path.basename(out_dir) != safe_name(run_id):
        print("[storyboard] plan changed; creating new revision: %s" %
              os.path.basename(out_dir), flush=True)

    # ── 收集 plan 级素材参考图 URL（数字人肖像 + 产品图 + 场景图）──────────────
    # 用于 img2img：把这些图传给 gpt-image-2 作参考，确保故事板准确对照真实素材。
    # 优先顺序：数字人肖像（最多 1 张）> 产品图 > 场景图
    # 新增：若 asset_refs 里有 product_sku，则通过 product_library.resolve()
    # 自动展开多方位图，用 hero+refs 替代手动列 product_images。
    asset_refs = plan.get("asset_refs") or {}
    import asset_prep
    client = plan.get("client") or client_slug(plan)
    brief = asset_prep._load_brief(client)
    if brief.get("images") and asset_refs.get("product_images"):
        ready_product_paths = [entry.get("path") for entry in (brief.get("images") or [])
                               if isinstance(entry, dict) and
                               asset_prep.is_product_asset_ready(
                                   brief.get("client"), entry.get("path"))]
        # Never let an authored plan reintroduce a raw or pending product image
        # when this client has a canonical asset brief.
        asset_refs = dict(asset_refs)
        authored_product_paths = {
            os.path.realpath(path if os.path.isabs(path) else os.path.join(ROOT, path))
            for path in (asset_refs.get("product_images") or []) if isinstance(path, str)
        }
        asset_refs["product_images"] = [path for path in ready_product_paths
                                         if os.path.realpath(path if os.path.isabs(path)
                                                             else os.path.join(ROOT, path))
                                         in authored_product_paths]
        plan["asset_refs"] = asset_refs

    portrait_urls = _collect_image_urls(
        asset_refs.get("digital_human_portraits"), api_key,
        fail_on_invalid=True, label="人物/佩戴参考图")
    product_urls = _collect_image_urls(
        asset_refs.get("product_images"), api_key,
        fail_on_invalid=True, label="产品参考图")
    scene_urls = _collect_image_urls(
        asset_refs.get("scene_images"), api_key,
        fail_on_invalid=True, label="场景参考图")
    # Collect/validate references before the user-prompt gate so diagnostics can
    # still show expanded SKU assets without allowing any model submission.
    _load_prompt_review_for_shots(prompt_review, plan)

    # Resolve output dir AFTER prompt_review injects approved_prompt_zh, so the
    # fingerprint compared against the previous run matches the stored one.
    if only_shot_ids and run_id and not flat:
        out_dir = os.path.join(out_dir, safe_name(run_id))
    else:
        out_dir = resolve_run_output_dir(out_dir, plan, run_id=run_id, flat=flat)
    os.makedirs(out_dir, exist_ok=True)
    if run_id:
        print("[storyboard] resolved output dir: %s" %
              os.path.abspath(out_dir), flush=True)

    from storyboard_validator import validate_plan
    validation = validate_plan(plan)
    if not validation["ok"]:
        raise br_client.BRError(
            "故事板计划未通过生成前校验：%s" % "；".join(validation["errors"])
        )

    # 合并参考图（去重，最多 4 张）：人物 > 产品 > 场景
    usage_urls = []
    product_board_urls = []
    cast_board_urls = []
    plan_ref_urls = _merge_reference_urls(portrait_urls, product_urls, scene_urls)

    result_path = os.path.join(out_dir, "storyboard_result.json")
    current_plan_fingerprint = plan_fingerprint(plan)
    current_visual_plan_fingerprint = visual_plan_fingerprint(plan)
    results = {"ok": True, "model": model, "plan_fingerprint": current_plan_fingerprint,
               "client": plan.get("client") or client_slug(plan),
               "run_id": safe_name(run_id) if run_id else os.path.basename(out_dir),
               "out_dir": os.path.abspath(out_dir), "shots": [],
               "storyboard_base_out_dir": requested_base_out_dir,
               "plan_source": os.path.abspath(plan_path),
               "plan_title": plan.get("project_title") or "",
               "expected_shot_ids": [str(shot.get("id") or index)
                                     for index, shot in enumerate(plan.get("shots") or [], 1)]}
    run_pointer = _write_run_pointer(
        requested_base_out_dir, run_id, plan, out_dir, result_path, stage="started",
        plan_fingerprint_value=current_plan_fingerprint,
        visual_plan_fingerprint_value=current_visual_plan_fingerprint)
    if run_pointer:
        results["current_run_pointer"] = os.path.abspath(run_pointer)
    if os.path.isfile(result_path):
        try:
            previous = json.loads(Path(result_path).read_text(encoding="utf-8"))
            same_plan = (not previous.get("plan_fingerprint") or
                         previous.get("plan_fingerprint") == results["plan_fingerprint"])
            if previous.get("model") == model and same_plan:
                for key in ("cast_board", "product_board", "product_usage_image", "shots"):
                    if key in previous:
                        results[key] = previous[key]
                print("[storyboard] resuming existing result: %s" % result_path, flush=True)
            elif previous.get("model") == model:
                print("[storyboard] checkpoint belongs to a different plan; starting clean", flush=True)
        except (OSError, ValueError):
            pass
    if only_shot_ids:
        plan_ids = {str(shot.get("id") or index)
                    for index, shot in enumerate(plan.get("shots") or [], 1)}
        unknown = sorted(only_shot_ids - plan_ids)
        if unknown:
            raise br_client.BRError("ONLY_SHOT_UNKNOWN: %s" % ", ".join(unknown))
        # A stale in-progress task for another shot must not be resumed when
        # the operator explicitly asked to regenerate only one shot.
        state = results.get("in_progress") or {}
        if state.get("kind") == "storyboard" and state.get("id") not in only_shot_ids:
            results.pop("in_progress", None)

    def save_progress():
        # Atomic replace prevents an interruption during JSON serialization from
        # destroying the only resume checkpoint.
        tmp_path = result_path + ".tmp"
        Path(tmp_path).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, result_path)

    def finish(stage_name=None, needs_confirmation=False):
        results["stage"] = stage_name or "storyboard"
        results["needs_confirmation"] = bool(needs_confirmation)
        results["result_json"] = os.path.abspath(result_path)
        run_pointer = _write_run_pointer(
            requested_base_out_dir, run_id, plan, out_dir, result_path,
            stage=results["stage"],
            plan_fingerprint_value=current_plan_fingerprint,
            visual_plan_fingerprint_value=current_visual_plan_fingerprint)
        if run_pointer:
            results["current_run_pointer"] = os.path.abspath(run_pointer)
        index_md, embedded_md, preview_html = write_index(plan, results, out_dir)
        results["index_md"] = index_md
        results["embedded_md"] = embedded_md
        results["preview_html"] = preview_html
        save_progress()
        return results

    def progress_callback(kind, asset_id, out_path):
        def update(event):
            current = {"kind": kind, "id": asset_id,
                       "out_path": os.path.abspath(out_path)}
            current.update(event)
            results["in_progress"] = current
            save_progress()
            if event.get("status") in ("submitted", "success", "failed"):
                print("[storyboard] %s %s: %s" %
                      (kind, asset_id, event.get("status")), flush=True)
            elif event.get("waited", 0) and event["waited"] % 30 == 0:
                print("[storyboard] %s %s still processing (%ss)" %
                      (kind, asset_id, event["waited"]), flush=True)
        return update

    def previous_task(kind, asset_id):
        state = results.get("in_progress") or {}
        if state.get("kind") == kind and state.get("id") == asset_id:
            return state.get("task_id")
        return None

    print("[storyboard] staged generation: confirmed source assets → product board → cast board → product-in-use image → shot storyboards", flush=True)

    # Product board is conditional on actual user/product references. A pure
    # character or environment project receives only cast + storyboard.
    if needs_product_board(plan):
        product_entries = [entry for entry in (brief.get("images") or [])
                           if isinstance(entry, dict) and
                           asset_prep.is_product_asset_ready(client, entry.get("path"))]
        if not product_entries:
            raise br_client.BRError(
                "PRODUCT_CLEANUP_CONFIRMATION_REQUIRED: 产品素材必须先经过 gpt-image-2 清洗并确认；"
                "请先用 standardize/clean-image 生成 pending 候选，再执行 confirm-image。")
        if not product_urls:
            raise br_client.BRError(
                "检测到产品镜头，但没有可用的产品参考图。请先用 asset_prep.py ingest-image 导入真实产品图，"
                "并确认后再生成产品板/故事板；不会用文字描述替代产品锚图。")
        try:
            import product_board as _pb
            product_meta = _plan_product_facts(plan)
            product_fp = _pb.product_board_source_fingerprint(
                product_urls[:4], product_meta)
            registered_product = _registered_board(client, "product", product_fp)
            if registered_product:
                print("[storyboard] reusing confirmed product board from asset registry", flush=True)
                results["product_board"] = registered_product
            else:
                results["product_board"] = _pb.generate_from_reference_urls(
                    api_key, product_urls, out_dir,
                    product_type=(plan.get("product_type") or asset_refs.get("product_type") or "the exact product"),
                    product_color=(plan.get("product_color") or (brief.get("product_color") if isinstance(brief, dict) else None)),
                    style_hint=(plan.get("visual_style") or "commercial product reference photography"),
                    model=model,
                    product_meta=product_meta)
            results["product_board"]["board_type"] = "product_only_3x3"
            results["product_board"]["description"] = (
                "产品本体九宫格：只展示同一产品的多角度、结构、材质和接口，不包含人物或使用场景。")
            save_progress()
            if not registered_product:
                print("[gpt-image-2] rendering conditional product board…", flush=True)
        except Exception as exc:
            raise br_client.BRError("检测到产品素材，但产品板生成失败：%s" % exc)
        product_fp = results["product_board"].get("source_fingerprint")
        product_approved = bool(registered_product) or _approval_current(out_dir, "product", product_fp)
        results["product_board"]["status"] = "confirmed" if product_approved else "pending"
        if stage == "product" or (stage == "next" and not product_approved):
            return finish("product_board", needs_confirmation=True)
        if stage in ("cast", "usage", "storyboard") and not product_approved:
            raise br_client.BRError("GENERATION_BLOCKED: 请先展示并确认产品板，再生成后续素材。")
        if product_approved:
            product_board_path = results["product_board"].get("abspath") or results["product_board"].get("path")
            product_board_urls = _collect_image_urls(
                [product_board_path] if product_board_path else [], api_key,
                fail_on_invalid=True, label="已确认产品板")
            plan_ref_urls = _merge_reference_urls(
                portrait_urls, product_board_urls + product_urls, scene_urls)

    cp = cast_prompt(plan)
    cast_approved = not bool(cp)
    if cp:
        cast_out = os.path.join(out_dir, "cast_board.jpg")
        force_cast = "cast" in force_boards
        # 人物板：用数字人肖像参考图做 img2img（确保生成的六视图与真实上传图一致）
        cast_fp = _source_refs_fingerprint(asset_refs.get("digital_human_portraits") or [])
        existing_cast = results.get("cast_board") or {}
        registered_cast = None if force_cast else _registered_board(client, "cast", cast_fp)
        confirmed_cast = _approval_record(out_dir, "cast")
        if registered_cast:
            print("[storyboard] reusing confirmed cast board from asset registry", flush=True)
            results["cast_board"] = registered_cast
            cast_out = registered_cast["path"]
        cast_source_changed = (existing_cast.get("source_fingerprint") and
                                existing_cast.get("source_fingerprint") != cast_fp)
        if not registered_cast and cast_source_changed and os.path.isfile(cast_out):
            os.remove(cast_out)
        cast_backup = (_backup_existing_board(cast_out, out_dir, "cast")
                       if force_cast else None)
        if force_cast:
            _mark_board_regeneration_pending(
                results, "cast_board", kind="cast", path=cast_out,
                source_fingerprint=cast_fp, backup_path=cast_backup)
            save_progress()
        if not registered_cast and (force_cast or not os.path.isfile(cast_out) or os.path.getsize(cast_out) == 0):
            print("[gpt-image-2] rendering cast board…", flush=True)
            results["cast_board"] = download_first_image(
                api_key, cp, cast_out, model=model, ratio="16:9",
                image_urls=portrait_urls or None,
                on_progress=progress_callback("cast_board", "cast", cast_out),
                resume_task_id=None if force_cast else previous_task("cast_board", "cast"),
                force=force_cast)
            results["cast_board"]["source_fingerprint"] = cast_fp
            results["cast_board"]["board_type"] = "digital_human_6_view"
            results["cast_board"]["description"] = "数字人六视图身份板：锁定脸部、发型、服装、配饰和身体比例。"
            if cast_backup:
                results["cast_board"]["forced_regeneration"] = {
                    "reason": "missing_remote_url",
                    "previous_backup": cast_backup,
                    "forced_at": datetime.now().isoformat(timespec="seconds"),
                }
        elif not registered_cast:
            results["cast_board"] = {"url": existing_cast.get("url") or confirmed_cast.get("url", ""),
                                     "abspath": os.path.abspath(cast_out), "skipped": True,
                                     "path": cast_out,
                                     "source_fingerprint": existing_cast.get("source_fingerprint") or cast_fp}
            for field in ("result_url", "task_id", "request_id"):
                value = existing_cast.get(field) or confirmed_cast.get(field)
                if value:
                    results["cast_board"][field] = value
        results.pop("in_progress", None)
        cast_approved = bool(registered_cast) or _approval_current(out_dir, "cast", cast_fp)
        results["cast_board"]["status"] = "confirmed" if cast_approved else "pending"
        save_progress()
        if stage == "cast" or (stage == "next" and not cast_approved):
            return finish("cast_board", needs_confirmation=True)
        if stage in ("usage", "storyboard") and not cast_approved:
            raise br_client.BRError("GENERATION_BLOCKED: 请先展示并确认人物板，再生成分段故事板。")
        if cast_approved:
            cast_board_urls = _collect_image_urls(
                [cast_out], api_key, fail_on_invalid=True, label="已确认人物板")
            plan_ref_urls = _merge_reference_urls(
                cast_board_urls + portrait_urls,
                product_board_urls + product_urls, scene_urls)

    if needs_product_usage_image(plan):
        force_usage = "usage" in force_boards
        has_human = bool(plan.get("characters") or any(
                shot.get("characters") or shot.get("character_prompt") or
                shot.get("character_action") or shot.get("digital_human") or
                shot.get("actor") for shot in plan.get("shots") or []))
        if not product_approved or (has_human and not cast_approved):
            raise br_client.BRError("GENERATION_BLOCKED: 产品使用图必须基于已确认的产品板和人物板生成。")
        usage_out = os.path.join(out_dir, "product_usage_board.jpg")
        product_board_path = results["product_board"].get("path")
        cast_board_path = (results.get("cast_board") or {}).get("path")
        usage_prompt_shot = _usage_action_shot(plan)
        usage_geometry_contract = product_usage_geometry_contract(plan, usage_prompt_shot)
        usage_relation_contract = product_usage_physical_relation(plan, usage_prompt_shot)
        usage_sources = [path for path in (cast_board_path, product_board_path) if path]
        usage_sources.extend([
            PRODUCT_USAGE_POLICY_VERSION,
            usage_geometry_contract or "usage-geometry-contract:none",
            json.dumps(usage_relation_contract, ensure_ascii=False,
                       sort_keys=True, default=str),
        ])
        usage_fp = _source_refs_fingerprint(usage_sources)
        existing_usage = results.get("product_usage_image") or {}
        confirmed_usage = _approval_record(out_dir, "usage")
        if (not force_usage and existing_usage.get("source_fingerprint") and
                existing_usage.get("source_fingerprint") != usage_fp and os.path.isfile(usage_out)):
            os.remove(usage_out)
        usage_pose_refs = _collect_image_urls(
            (asset_refs.get("usage_reference_images") or []), api_key,
            fail_on_invalid=True, label="已确认佩戴姿势参考图")
        cast_usage_refs = (_collect_image_urls(
            [cast_board_path] if cast_board_path else portrait_urls, api_key,
            fail_on_invalid=True, label="已确认人物板") if has_human else [])
        product_identity_paths = _confirmed_product_identity_paths(
            plan, results, client=client, limit=2)
        product_usage_refs = _collect_image_urls(
            product_identity_paths,
            api_key, fail_on_invalid=True, label="已确认产品身份图")
        # Keep all three semantic anchors, with product identity first. The
        # product board exists specifically to lock shape before usage imagery;
        # putting cast first lets the model prioritize the person and drift the
        # product geometry in human-product interaction panels.
        usage_refs = _usage_reference_urls(
            product_usage_refs, cast_usage_refs, usage_pose_refs, limit=3)
        usage_backup = (_backup_existing_board(usage_out, out_dir, "usage")
                        if force_usage else None)
        usage_prompt_text = product_usage_prompt(
            plan, usage_prompt_shot, include_human=has_human)
        if force_usage:
            _mark_board_regeneration_pending(
                results, "product_usage_image", kind="usage", path=usage_out,
                source_fingerprint=usage_fp, backup_path=usage_backup)
            results["product_usage_image"]["submission_prompt_zh"] = usage_prompt_text
            if usage_geometry_contract:
                results["product_usage_image"]["geometry_contract"] = usage_geometry_contract
            if usage_relation_contract:
                results["product_usage_image"]["physical_relation_contract"] = usage_relation_contract
            results["product_usage_image"]["usage_action_shot_id"] = usage_prompt_shot.get("id")
            results["product_usage_image"]["usage_outcome_context"] = product_usage_outcome_context(
                plan, usage_prompt_shot)
            save_progress()
        if force_usage or not os.path.isfile(usage_out) or os.path.getsize(usage_out) == 0:
            print("[gpt-image-2] rendering product-in-use detail image…", flush=True)
            results["product_usage_image"] = download_first_image(
                api_key, usage_prompt_text, usage_out,
                model=model, ratio="16:9",
                image_urls=usage_refs,
                on_progress=progress_callback("product_usage_image", "usage", usage_out),
                resume_task_id=None if force_usage else previous_task("product_usage_image", "usage"),
                sync_img2img=True, force=force_usage)
            results["product_usage_image"]["source_fingerprint"] = usage_fp
            results["product_usage_image"]["board_type"] = "usage_3x3"
            results["product_usage_image"]["usage_policy_version"] = PRODUCT_USAGE_POLICY_VERSION
            if usage_geometry_contract:
                results["product_usage_image"]["geometry_contract"] = usage_geometry_contract
            if usage_relation_contract:
                results["product_usage_image"]["physical_relation_contract"] = usage_relation_contract
            results["product_usage_image"]["description"] = (
                "产品使用九宫格：根据产品板与数字人板（如有）展示真实操作、手部接触和使用结果。")
            results["product_usage_image"]["identity_reference_paths"] = [
                os.path.abspath(p) for p in product_identity_paths]
            results["product_usage_image"]["generation_reference_paths"] = [
                os.path.abspath(p) for p in
                product_identity_paths + ([cast_board_path] if cast_board_path else []) +
                (asset_refs.get("usage_reference_images") or [])]
            if usage_backup:
                results["product_usage_image"]["forced_regeneration"] = {
                    "reason": "missing_remote_url",
                    "previous_backup": usage_backup,
                    "forced_at": datetime.now().isoformat(timespec="seconds"),
                }
        else:
            results["product_usage_image"] = {
                "url": existing_usage.get("url") or confirmed_usage.get("url", ""),
                "path": usage_out,
                "abspath": os.path.abspath(usage_out), "skipped": True,
                "source_fingerprint": existing_usage.get("source_fingerprint") or usage_fp,
                "board_type": existing_usage.get("board_type") or "usage_3x3",
                "usage_policy_version": (existing_usage.get("usage_policy_version") or
                                         confirmed_usage.get("usage_policy_version") or
                                         PRODUCT_USAGE_POLICY_VERSION),
                "geometry_contract": (existing_usage.get("geometry_contract") or
                                      confirmed_usage.get("geometry_contract") or
                                      usage_geometry_contract),
                "physical_relation_contract": (
                    existing_usage.get("physical_relation_contract") or
                    confirmed_usage.get("physical_relation_contract") or
                    usage_relation_contract),
                "description": existing_usage.get("description") or
                "产品使用九宫格：根据产品板与数字人板（如有）展示真实操作、手部接触和使用结果。",
                "identity_reference_paths": existing_usage.get("identity_reference_paths") or
                confirmed_usage.get("identity_reference_paths") or
                [os.path.abspath(p) for p in product_identity_paths],
                "generation_reference_paths": existing_usage.get("generation_reference_paths"),
            }
            for field in ("result_url", "task_id", "request_id"):
                value = existing_usage.get(field) or confirmed_usage.get(field)
                if value:
                    results["product_usage_image"][field] = value
        results["product_usage_image"]["submission_prompt_zh"] = usage_prompt_text
        if usage_geometry_contract:
            results["product_usage_image"]["geometry_contract"] = usage_geometry_contract
        if usage_relation_contract:
            results["product_usage_image"]["physical_relation_contract"] = usage_relation_contract
        results["product_usage_image"]["usage_action_shot_id"] = usage_prompt_shot.get("id")
        results["product_usage_image"]["usage_outcome_context"] = product_usage_outcome_context(
            plan, usage_prompt_shot)
        results.pop("in_progress", None)
        usage_approved = _approval_current(out_dir, "usage", usage_fp)
        results["product_usage_image"]["status"] = "confirmed" if usage_approved else "pending"
        save_progress()
        if stage == "usage" or (stage == "next" and not usage_approved):
            return finish("product_usage_image", needs_confirmation=True)
        if stage == "storyboard" and not usage_approved:
            raise br_client.BRError("GENERATION_BLOCKED: 请先展示并确认产品使用图，再生成分段故事板。")
        usage_urls = _collect_image_urls([usage_out], api_key, fail_on_invalid=True,
                                         label="已确认产品使用图")
        plan_ref_urls = _merge_reference_urls(
            usage_urls + cast_board_urls + portrait_urls,
            product_board_urls + product_urls, scene_urls)
        plan["asset_refs"] = dict(
            plan.get("asset_refs") or {}, product_usage_images=[usage_out],
            cast_boards=[cast_out] if cast_board_urls else [],
            product_boards=[product_board_path] if product_board_urls else [])

    if stage in ("product", "cast", "usage"):
        return finish("product_usage_image" if stage == "usage" else "%s_board" % stage,
                      needs_confirmation=True)

    # The contact sheet is a review artifact. Its N panels map 1:1 to the
    # authored shots, and each later video request expands exactly one panel.
    reference_registry = build_reference_registry(plan)
    _validate_reference_registry(plan, reference_registry)
    if len(reference_registry) > 4:
        raise br_client.BRError(
            "REFERENCE_REGISTRY_TOO_LARGE: 当前联系表包含 %d 张不同的确认参考图，"
            "超过 gpt-image-2 单次 img2img 的 4 张上限。请拆分项目或先合并为已确认的人物/产品/使用板；"
            "不得静默丢弃某一镜头绑定素材。" % len(reference_registry))
    results["reference_registry"] = reference_registry
    previous_shots = {}
    for item in results.get("shots") or []:
        sid = str((item.get("shot") or {}).get("id") or item.get("id") or "").strip()
        if sid:
            previous_shots[sid] = item
    confirmed_shots = {}
    if only_shot_ids and not previous_shots:
        approval = _read_result(_approval_path(out_dir, "storyboard"))
        for item in (approval or {}).get("shots") or []:
            sid = str(item.get("id") or "")
            if sid:
                confirmed_shots[sid] = item
    rendered_shots = []
    for index, shot in enumerate(plan.get("shots") or [], 1):
        sid = str(shot.get("id") or index)
        shot_out = os.path.join(out_dir, "shot_%02d_%s.jpg" % (index, safe_name(sid)))
        existing = previous_shots.get(sid)
        shot_registry = shot_reference_registry(reference_registry, shot)
        shot_reference_fp = reference_fingerprint(shot_registry)
        if only_shot_ids and sid not in only_shot_ids:
            carry = existing or confirmed_shots.get(sid)
            carry_path = ((carry or {}).get("abspath") or (carry or {}).get("path") or
                          (shot_out if os.path.isfile(shot_out) else None))
            if not carry_path or not os.path.isfile(carry_path):
                raise br_client.BRError(
                    "ONLY_SHOT_CARRY_FORWARD_MISSING: %s 缺少可沿用的已确认故事板图。" % sid)
            carried = dict(carry or {})
            carried.update({"path": carry_path, "abspath": os.path.abspath(carry_path),
                            "id": sid,
                            "sha256": _file_sha256(carry_path),
                            "plan_fingerprint": current_plan_fingerprint,
                            "shot_fingerprint": shot_fingerprint(shot),
                            "reference_fingerprint": shot_reference_fp,
                            "reference_registry": shot_registry,
                            "shot": shot,
                            "carried_forward": True,
                            "carry_forward_reason": "only_shot_regeneration"})
            rendered_shots.append(carried)
            continue
        if _existing_shot_matches_plan(
                existing, shot, current_plan_fingerprint, shot_out,
                expected_reference_fingerprint=shot_reference_fp):
            existing = dict(existing)
            existing_path = existing.get("abspath") or existing.get("path") or shot_out
            existing.update({"id": sid,
                             "path": existing.get("path") or shot_out,
                             "abspath": os.path.abspath(existing_path),
                             "shot": shot})
            rendered_shots.append(existing)
            continue
        print("[gpt-image-2] rendering storyboard panel for shot %s…" % sid, flush=True)
        shot_ref_urls = _collect_image_urls(
            [item["url"] for item in shot_registry], api_key,
            fail_on_invalid=True, label="故事板参考图:%s" % sid) if shot_registry else []
        shot_plan = dict(plan, shots=[shot])
        image = download_first_image(
            api_key, contact_sheet_prompt(shot_plan, bw=bw, reference_registry=shot_registry),
            shot_out, model=model, ratio="16:9",
            image_urls=shot_ref_urls or plan_ref_urls or None,
            on_progress=progress_callback("storyboard", sid, shot_out),
            resume_task_id=None if os.path.isfile(shot_out) else previous_task("storyboard", sid),
            force=os.path.isfile(shot_out))
        image.update({"path": shot_out, "abspath": os.path.abspath(shot_out),
                      "id": sid,
                      "sha256": _file_sha256(shot_out),
                      "plan_fingerprint": current_plan_fingerprint,
                      "shot_fingerprint": shot_fingerprint(shot),
                      "reference_fingerprint": shot_reference_fp,
                      "reference_registry": shot_registry,
                      "shot": shot})
        rendered_shots.append(image)
        results["shots"] = rendered_shots
        results.pop("in_progress", None)
        save_progress()
    results["shots"] = rendered_shots

    return finish("storyboard", needs_confirmation=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate storyboard/cast-board images from a finalized script plan using gpt-image-2")
    p.add_argument("--plan", help="storyboard_plan.json")
    p.add_argument("--out-dir", default="output/storyboard", help="base output directory; by default each run creates a timestamped subdirectory here")
    p.add_argument("--run-id", default=None, help="optional stable session id, e.g. acme-v1 or 20260723-acme-approved-script")
    p.add_argument("--flat", action="store_true", help="write directly into --out-dir; legacy/debug mode, may overwrite previous previews")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--color", action="store_true",
                   help="生成彩色故事板；默认生成黑白故事板（可被 plan/shot 的 color_mode 覆盖）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--prompt-review", help="已确认的中文故事板提示词审核文件")
    p.add_argument("--only-shot", action="append", default=None,
                   help="只重生指定 shot id；仅可配合 --stage storyboard，用于修复单个过期分镜，避免重生全部故事板。")
    p.add_argument("--force-board", action="append", choices=["cast", "usage"], default=None,
                   help="显式重生已存在的人物板/产品使用图，用于旧图缺少 BasicRouter retrieve URL 的修复；会备份旧图并清除旧确认。")
    p.add_argument("--stage", choices=["next", "product", "cast", "usage", "storyboard", "all"], default="next",
                    help="默认 next：一次只推进一个待确认阶段；all 仅限调试")
    p.add_argument("--debug-allow-all", action="store_true",
                   help="仅调试：允许 --stage all；生产流程禁止使用")
    p.add_argument("--confirm-board", choices=["product", "cast", "usage"],
                    help="确认已展示的产品板/人物板/产品使用图，必须同时传 --result-json")
    p.add_argument("--geometry-reviewed", action="store_true",
                   help="配合 --confirm-board usage 使用：已人工复核产品使用图的物理接触面、方向、外露面和使用结果。")
    p.add_argument("--refine-board", choices=["product", "cast", "usage"],
                    help="按客户反馈精修已展示的产品板/人物板/产品使用图，结果仍为 pending")
    p.add_argument("--edit", help="配合 --refine-board 使用：客户要求修改的具体内容")
    p.add_argument("--feedback-ref", nargs="+", default=None,
                   help="配合 --refine-board 使用：客户上传的反馈参考图，可多张")
    p.add_argument("--result-json", help="要确认的 storyboard_result.json")
    p.add_argument("--confirm", action="store_true",
                   help="整体确认全部分段故事板，必须同时传 --result-json")
    args = p.parse_args(argv)
    try:
        if args.confirm:
            if not args.result_json:
                raise br_client.BRError("--confirm 必须配合 --result-json")
            res = confirm_storyboard(args.result_json)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if args.confirm_board:
            if not args.result_json:
                raise br_client.BRError("--confirm-board 必须配合 --result-json")
            res = confirm_board(args.result_json, args.confirm_board,
                                geometry_reviewed=args.geometry_reviewed)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if args.refine_board:
            if not args.result_json:
                raise br_client.BRError("--refine-board 必须配合 --result-json")
            res = refine_board(args.result_json, args.refine_board, args.edit,
                               feedback_refs=args.feedback_ref, model=args.model)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if not args.plan:
            raise br_client.BRError("生成故事板必须提供 --plan")
        if not args.prompt_review and not args.debug_allow_all:
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 正式生图前必须先运行 prompt_review.py polish，"
                "展示中文提示词并执行 confirm。")
        res = render_storyboard(args.plan, args.out_dir, model=args.model, run_id=args.run_id,
                                flat=args.flat, bw=not args.color, stage=args.stage,
                                debug_allow_all=args.debug_allow_all,
                                prompt_review=args.prompt_review,
                                only_shot_ids=args.only_shot,
                                force_boards=args.force_board)
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e),
                              "user_message": ux.friendly_error(e)}, ensure_ascii=False))
        else:
            print("ERROR: %s\n下一步：%s" % (e, ux.friendly_error(e)))
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[done] storyboard index: %s" % res["index_md"])
        print("[done] result json: %s" % res["result_json"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
