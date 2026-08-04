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
   output/storyboard/<run-id>/shot_01_s1.jpg ...          # one 16:9 4x3, 12-panel cinematic storyboard per segment
  output/storyboard/<run-id>/storyboard_index.md         # markdown gallery with absolute paths
  output/storyboard/<run-id>/storyboard_embedded.md      # markdown gallery with embedded image data
  output/storyboard/<run-id>/storyboard_preview.html     # self-contained preview page
  output/storyboard/<run-id>/storyboard_result.json      # machine-readable result
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from project_utils import require_contained_path  # noqa: E402
import br_client as _br  # noqa: E402
import br_client  # noqa: E402
import artifact_contract  # noqa: E402
import key_setup  # noqa: E402
import ux  # noqa: E402
from image_utils import image_type  # noqa: E402
from video_segmentation import partition_shots, SEEDANCE_MAX_SECONDS  # noqa: E402
from board_confirm import _source_refs_fingerprint  # noqa: E402 — v4 shim


DEFAULT_MODEL = "gpt-image-2"
IMAGE_MAX_WAIT = 900
IMAGE_RETRIES = 2
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
    """Return the canonical 12-panel plan without silently accepting 9 panels."""
    raw = shot.get("panel_plan") or shot.get("twelve_panel_plan")
    if isinstance(raw, str):
        raw = [raw]
    if not raw:
        return list(DEFAULT_PANEL_PLAN)
    return list(raw)


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

    approved_prompt_zh is excluded: it is injected by prompt_review after the
    plan is authored, and must not change the plan's identity (otherwise every
    prompt review would silently create a new output directory).
    """
    plan = canonical_storyboard_plan(plan)
    # Strip injected fields that are not part of the authored plan identity
    for shot in (plan.get("shots") or []):
        shot.pop("approved_prompt_zh", None)
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
    expected_fp = _digest_plan_for_prompt_review(plan)
    if review.get("plan_fingerprint") != expected_fp:
        raise br_client.BRError(
            "PROMPT_REVIEW_REQUIRED: 提示词审核的指纹与当前计划不匹配\n"
            "  审核文件指纹: %s\n  当前计划指纹: %s\n"
            "  原因：计划在审核确认后被修改过。请重新运行 prompt_review.py polish + confirm。"
            % (review.get("plan_fingerprint"), expected_fp))
    prompts = {str(item.get("shot_id")): item.get("prompt_zh")
               for item in review.get("prompts") or []}
    for shot in plan.get("shots") or []:
        if not prompts.get(str(shot.get("id"))):
            raise br_client.BRError(
                "PROMPT_REVIEW_REQUIRED: 缺少镜头 %s 的确认提示词。"
                "请重新运行 prompt_review.py polish + confirm。" % shot.get("id"))
        shot["approved_prompt_zh"] = prompts[str(shot.get("id"))]


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


def download_first_image(api_key, prompt, out_path, *, model=DEFAULT_MODEL,
                         ratio="9:16", image_urls=None, on_progress=None,
                         resume_task_id=None, sync_img2img=False):
    """生成一张图并下载到 out_path。

    image_urls: 可选参考图列表（本地 data URL 或 https URL）。
    传入时走 img2img，不传则纯文生图。sync_img2img 用于当前网关的
    gpt-image-2 图像输入兼容路径：同步 /ai/createImage 明确接受 imageUrls，
    而异步 /v1/image-generations 在部分网关模型上会错误返回不支持 image input。
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    from image_utils import image_type
    if (os.path.isfile(out_path) and os.path.getsize(out_path) > 0 and
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
            if sync_img2img:
                urls = br_client.create_image(
                    api_key, prompt, model=model, count=1, resolution="2k",
                    ratio=ratio, image_urls=image_urls or [], request_id=request_id,
                    timeout=600)
                if not urls:
                    raise br_client.BRError("image task succeeded but returned no image")
                result_url = urls[0]
                br_client.download(result_url, out_path)
                if image_type(out_path) not in {"png", "jpeg", "webp"}:
                    raise br_client.BRError("image task downloaded an invalid image")
                return {"url": result_url, "path": out_path,
                        "abspath": os.path.abspath(out_path),
                        "request_id": request_id, "sha256": _file_sha256(out_path)}
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
            br_client.download(result_url, out_path)
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
    """
    panel_plan = normalize_panel_plan(shot)
    if not panel_plan:
        return ""
    lines = [
        "\n[PANEL-BY-PANEL DIRECTOR BEATS / 逐格导演执行表]",
        "Render each numbered panel as one readable instant, not a collage or repeated pose.",
        "For every panel preserve screen direction, subject identity, prop geometry and the previous panel's cause-and-effect.",
    ]
    for number, item in enumerate(panel_plan[:12], 1):
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
            lines.append("  Panel %s: %s" % (panel, detail or "one distinct observable beat"))
        else:
            lines.append("  Panel %d: beat=%s" % (number, str(item).strip()))
    if len(panel_plan) < 12:
        lines.append("  Missing panels: infer only the smallest continuity-preserving beats; do not duplicate an existing panel.")
    return "\n".join(lines)


def _cinematic_grammar_block(shot):
    """Add model-agnostic visual grammar and a practical acceptance checklist."""
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
        "The result is acceptable only if all 12 panels are countable and readable, each panel has a distinct "
        "story beat, adjacent panels provide a 30-50 degree angle change or a clear shot-size/composition change, "
        "hands actually contact the intended prop, faces remain the same person, and no panel contradicts the "
        "location, wardrobe, lighting, product geometry or timeline. Prefer simple achievable actions over "
        "simultaneous complex actions."
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
    scene_imgs = shot_asset_refs.get("scene_images") or asset_refs.get("scene_images") or []
    portrait_imgs = shot_asset_refs.get("digital_human_portraits") or asset_refs.get("digital_human_portraits") or []

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
        (shot.get("character_action") or shot.get("action") or product_usage_imgs)
        else ""
    )

    asset_block = ""
    if product_imgs or product_usage_imgs or scene_imgs or portrait_imgs:
        lines_a = [
            "\n\n[CRITICAL — CLIENT UPLOADED REFERENCE ASSETS]",
            "The following images were uploaded by the client and MUST be strictly referenced.",
            "Do NOT invent product shapes, colors, logos, or character faces from imagination.",
            "Every panel in this 4x3, 12-panel storyboard must be consistent with these references.",
        ]
        if product_imgs:
            lines_a.append("PRODUCT reference images (exact shape, color, logo, material must match):")
            lines_a.extend("  - %s" % u for u in product_imgs)
        if product_usage_imgs:
            lines_a.append("CONFIRMED PRODUCT-IN-USE reference (match the person's real operation, hand contact, product orientation and visible functional details):")
            lines_a.extend("  - %s" % u for u in product_usage_imgs)
        if scene_imgs:
            lines_a.append("SCENE / BACKGROUND reference images (layout, props, environment must match):")
            lines_a.extend("  - %s" % u for u in scene_imgs)
        if portrait_imgs:
            lines_a.append("CHARACTER / DIGITAL HUMAN portrait references (face, hair, outfit IDENTITY LOCKED):")
            lines_a.extend("  - %s" % u for u in portrait_imgs)
        asset_block = "\n".join(lines_a)

    # ── 黑白画面约束（用户要求：故事板必须是黑白）────────────────────────────────
    # 支持 plan/shot 级 color_mode 覆盖：'bw'（默认黑白）或 'color'。
    color_mode = ("bw" if strict_bw else
                  (shot.get("color_mode") or plan.get("color_mode") or ("bw" if bw else "color")))
    if str(color_mode).lower() in ("bw", "black_white", "grayscale", "mono", "黑白"):
        bw_block = (
             "STRICT BLACK-AND-WHITE DRAWING / 严格黑白绘画: render the ENTIRE 4x3, 12-panel sheet in monochrome "
            "grayscale only — pure black, white and grays, NO color hue anywhere, like a classic "
            "pencil/charcoal film previsualization storyboard. Use tonal contrast and shading "
            "(not color) to convey depth, material and lighting. This is a black-and-white "
            "storyboard, not a colored render. "
        )
        bw_negative = "任何彩色, 色相, 饱和度, 上色, colored, color tint, saturation, "
    else:
        bw_block = ""
        bw_negative = ""
    # ── 12 格电影预演：每格都必须包含身体动量和明确摄影运动。
    seq_block = (
        "Treat the twelve panels as an ORDERED SHOT SEQUENCE 镜头1→镜头12 in strict event order "
        "(先主后次). Every panel MUST show visible movement and body momentum; avoid static standing poses. "
        "Each panel = subject + location + physical action + camera movement + emotional pressure. "
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
    # Grid layout adapts to aspect ratio: landscape 16:9 → 4x3, portrait 9:16 → 3x4
    aspect = plan.get("aspect_ratio", "16:9")
    if aspect in ("9:16", "3:4"):
        grid_desc = "3x4"
        canvas_desc = "9:16" if aspect == "9:16" else "3:4"
    else:
        grid_desc = "4x3"
        canvas_desc = "16:9"

    return (
        "Create a %s cinematic storyboard TABLE with twelve movie-style panels arranged as a clean %s grid / %s电影风格12格故事板表格。"
        % (canvas_desc, grid_desc, canvas_desc)
        + bw_block
        + "The single image must contain TWELVE panels arranged in a clean %s grid inside one %s canvas, each panel showing a distinct beat of the SAME segment. "
        % (grid_desc, canvas_desc)
        + "The drawing itself is ONLY rough black-and-white pencil lines, minimal detail, quick loose construction, simple anatomy, strong silhouette readability, lightweight dynamic unfinished early choreography previsualization. "
        + "Add director annotation marks as a separate overlay layer: RED arrows for body motion, BLUE arrows for camera motion, GREEN marks for framing/composition notes, ORANGE marks for lighting direction, PURPLE marks for sound/emotional emphasis, and BLACK short shot notes/panel labels. Annotation colors are allowed only for these marks; all characters, environment, smoke, fabric, reflections and drawn imagery remain black/white/gray. "
        + "Use handheld energy, whip pans, orbiting camera, overhead view, side profile silhouette, aggressive close-up, and long-lens compression across the sequence. Keep the environment minimal: open space, smoke, fabric motion, reflected light. Make performers feel trapped between ritual and emotional release. "
        + seq_block
        + "Use film-style previsualization, readable composition, strong scene continuity, and edit-friendly coverage. "
        "Suggested 12-panel progression: establish open space, kinetic entry, ritual gesture, lateral profile, handheld push, overhead orbit, aggressive close-up, long-lens compression, fabric/smoke release, camera wrap, emotional payoff, unresolved final hold. "
        "If a custom 12-panel plan is provided below, follow it panel-by-panel while preserving the same segment continuity. "
        "Use professional camera language: rule-of-thirds or symmetry or foreground framing, clear eyeline/product placement, lens-like depth, directional lighting, and intentional negative space for later overlays. "
        "Keep adjacent panels visually different: 30-50 degree camera/subject angle offsets, or wide/medium/close/detail shot-size variation, or composition center shift. "
        "Keep the SAME background, SAME character identity, SAME wardrobe, SAME product appearance, SAME prop relationships, SAME lighting, and SAME art direction across the 4x3 grid. "
        "If this segment belongs to a multi-part stitched sequence, keep background, character appearance, voice tone, and BGM mood consistent across ALL segments; only vary camera angle, framing, and beat progression. "
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
         f"Custom 12-panel plan: {'; '.join(str(p) if not isinstance(p, dict) else json.dumps(p, ensure_ascii=False, sort_keys=True) for p in panel_plan)}\n"
        f"Audio continuity notes: voice={audio.get('voice','')}; bgm={audio.get('bgm','')}; sfx={audio.get('sfx','')}\n"
        f"Video prompt notes: {shot.get('video_prompt_notes','')}\n"
        f"Overall visual style: {style}\n"
        f"Negative constraints: {bw_negative}字幕, 文字, 水印, logo, kinetic typography, 悬浮文字, 数据标签, 双胞胎, 分身, 同款重复人物, 畸形, 多手指, 面部扭曲, 低质量\n"
        + (("\n".join(continuity_bits)) + "\n" if continuity_bits else "")
          + asset_block
          + product_fact_block
          + usage_block
          + _panel_prompt_block(shot)
          + motion_note
          + (("\n【用户确认的中文导演提示词】\n" + str(shot.get("approved_prompt_zh")))
             if shot.get("approved_prompt_zh") else "")
    )


def _data_uri(path):
    import base64
    ext = os.path.splitext(path)[1].lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def write_index(plan, results, out_dir):
    lines = []
    lines.append(f"# {plan.get('project_title','Storyboard')} — 16:9 黑白铅笔预演 12 格故事板确认稿")
    lines.append("")
    lines.append("> 故事板为 16:9、4x3 共 12 格黑白铅笔线稿预演；红/蓝/绿/橙/紫仅用于导演运动、机位、构图、灯光和声音情绪标注。请客户先确认人物、镜头、构图和产品表达，确认后才进入视频生成。")
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
    lines.append("- 分段故事板：%d 张，均为 16:9、4x3、12 格，待确认" % len(results.get("shots", [])))
    lines.append("")
    lines.append("## 16:9 电影级 12 格分镜故事板 / 16:9 cinematic 12-panel storyboard")
    if results.get("product_board"):
        lines.append("## 产品本体九宫格 / Product-only 3x3 board")
        status = results["product_board"].get("status", "pending")
        lines.append("产品板状态：%s%s" % (
            status, "，必须先确认产品九宫格，再进入后续生成。" if status != "confirmed" else "。"))
        lines.append("![产品九宫格产品板](<%s>)" % results["product_board"]["path"])
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        lines.append("## 产品使用九宫格 / Product-in-use 3x3 board")
        lines.append("状态：%s。请确认九个使用镜面、数字人身份、手部接触和产品操作关系。" % usage.get("status", "pending"))
        lines.append("![产品使用九宫格](<%s>)" % usage["path"])
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        lines.append(f"### {shot.get('id','shot')} · {shot.get('duration','?')}s")
        lines.append(f"- 台词/旁白：{shot.get('dialogue','')}")
        lines.append(f"- 画面：{shot.get('visual','')}")
        lines.append(f"- 镜头：{shot.get('camera','')}")
        lines.append(f"![{shot.get('id','shot')}](<{p}>)")
        lines.append("")
    index_path = os.path.join(out_dir, "storyboard_index.md")
    Path(index_path).write_text("\n".join(lines), encoding="utf-8")

    embedded = []
    embedded.append(f"# {plan.get('project_title','Storyboard')} — 内嵌故事板预览")
    embedded.append("")
    embedded.append("> 如果当前 Agent 不能直接渲染本地文件路径图片，请打开这个版本。它把图片编码进 markdown，便于直接在聊天里预览。")
    embedded.append("")
    if results.get("cast_board"):
        p = results["cast_board"]["abspath"]
        embedded.append("## 人物板 / Cast board")
        embedded.append(f"![人物板]({_data_uri(p)})")
        embedded.append("")
    if results.get("product_board"):
        p = results["product_board"]["path"]
        status = results["product_board"].get("status", "pending")
        embedded.append("## 产品板 / Product consistency board")
        embedded.append("> 状态：%s%s" % (
            status, "；请确认产品九宫格后再进入后续生成。" if status != "confirmed" else "。"))
        embedded.append(f"![产品九宫格产品板]({_data_uri(p)})")
        embedded.append("")
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        embedded.append("## 产品使用图 / Product-in-use reference")
        embedded.append("> 状态：%s。请确认人物实际使用动作、手部接触和产品细节。" % usage.get("status", "pending"))
        embedded.append("![人物使用产品细节图](%s)" % _data_uri(usage["path"]))
        embedded.append("")
    embedded.append("## 分镜故事板 / Shot storyboard")
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        embedded.append(f"### {shot.get('id','shot')} · {shot.get('duration','?')}s")
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
    if results.get("cast_board"):
        p = results["cast_board"]["abspath"]
        html.append("<div class='card'><h2>人物板 / Cast board</h2>")
        html.append(f"<img alt='人物板' src='{_data_uri(p)}'></div>")
    if results.get("product_board"):
        p = results["product_board"]["path"]
        status = results["product_board"].get("status", "pending")
        html.append("<div class='card'><h2>产品板 / Product consistency board</h2>")
        html.append("<p class='meta'>状态：%s%s</p>" % (
            status, "；请确认产品九宫格后再进入后续生成。" if status != "confirmed" else "。"))
        html.append(f"<img alt='产品九宫格产品板' src='{_data_uri(p)}'></div>")
    if results.get("product_usage_image"):
        usage = results["product_usage_image"]
        html.append("<div class='card'><h2>产品使用图 / Product-in-use reference</h2>")
        html.append("<p class='meta'>状态：%s。请确认人物实际使用动作、手部接触和产品细节。</p>" % usage.get("status", "pending"))
        html.append("<img alt='人物使用产品细节图' src='%s'></div>" % _data_uri(usage["path"]))
    for item in results.get("shots", []):
        shot = item.get("shot", {})
        p = item.get("abspath")
        html.append("<div class='card'>")
        html.append(f"<h2>{shot.get('id','shot')} · {shot.get('duration','?')}s</h2>")
        html.append(f"<p class='meta'><b>台词/旁白：</b>{shot.get('dialogue','')}<br><b>画面：</b>{shot.get('visual','')}<br><b>镜头：</b>{shot.get('camera','')}</p>")
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
    # must not mask the real upload recorded in brief.json.
    existing = list(refs.get("product_images") or [])
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
        ("product_name", "product_type", "product_color", "color", "price", "usps", "specs")
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


def product_usage_prompt(plan, shot=None, include_human=True):
    """Build the physical interaction lock for product-in-use reference imagery."""
    shot = shot or {}
    # When called without a specific shot (board-level usage image), fall back
    # to the most action-specific shot in the plan so the model focuses on the
    # core interaction (e.g. magnetic snap-on) instead of generic posing.
    if not (shot.get("character_action") or shot.get("action") or shot.get("visual")):
        for candidate in (plan.get("shots") or []):
            cand_action = (candidate.get("character_action") or
                           candidate.get("action") or candidate.get("visual") or "")
            if "磁吸" in cand_action or "吸附" in cand_action or "magnetic" in cand_action.lower():
                shot = candidate
                break
    facts = shot.get("product_facts") or plan.get("product_facts") or {}
    product_name = facts.get("product_name") or facts.get("product_type") or "the exact uploaded product"
    action = (shot.get("character_action") or shot.get("action") or shot.get("visual") or
              "the person actively and correctly uses the product for its intended purpose")
    subject = (
        "Every panel must show the same confirmed digital human actively using the same confirmed product. "
        if include_human else
        "No digital human is configured. Show the same confirmed product in nine physically credible usage contexts, using only anonymous hands or operator POV when needed; never invent a recognizable person. "
    )
    action_line = (
        "Show the person ACTIVELY AND CORRECTLY USING %s, not merely posing beside it. "
        % product_name
        if include_human else
        "Show the exact product performing or being operated for its intended use; do not add a recognizable person. "
    )
    required_action = "Required action: %s. " % action
    shape_lock = ""
    product_text = (str(product_name) + " " + json.dumps(facts, ensure_ascii=False)).lower()
    if "aeroclip" in product_text or "耳夹" in product_text or "c形" in product_text:
        shape_lock = (
            "Shape lock for this product: it is a C-shaped clip-on ear device that clamps gently on the outer ear, "
            "with the product body visibly wrapping the ear rim. It is NOT an over-ear hook, NOT a behind-neck band, "
            "NOT an in-ear earbud, NOT a stemmed earbud, and NOT a hanging ear-loop headset. "
            "产品形态锁定：C 形耳夹式，夹在耳廓外侧并包住耳缘；不是挂耳式、后挂式、入耳式、耳塞式或耳环式。 "
        )
    return "".join((
        "[PRODUCT-IN-USE NINE-PANEL BOARD]\n",
        "Create one 16:9 landscape 3x3 board with exactly nine distinct panels. ",
        subject,
        "The confirmed digital-human board and confirmed product board are the primary subjects of every panel. "
        "Any supplied wearing-position reference is only a pose and fit guide; do not replace the same Luna "
        "or the same AeroClip S1 with the reference image's person or product. ",
        "Panel order: establish, front interaction, left interaction, right interaction, over-shoulder operation, hand/contact close-up, control detail, action result, wider context. ",
        action_line, required_action,
        shape_lock,
        "Preserve the exact uploaded product geometry, scale, color, orientation and functional parts. "
        "Show anatomically plausible finger placement, all real contact points, grip pressure and occlusion; "
        "the product must remain on the correct side and in the correct operating position. Hands must have exactly five fingers each: "
        "no extra fingers, fused fingers, missing fingers, duplicated hands, floating product, hand-product intersection, "
        "skin passing through the product, or impossible contact. The visible result of the action must match the intended use. "
        "No captions, subtitles, generated text, logo, watermark or extra product."
    ))


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
            return bool(board_path and os.path.isfile(board_path) and
                        _file_sha256(board_path) == board_sha256)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def confirm_board(result_json, kind):
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
    record = {"status": "confirmed", "kind": kind,
               "source_fingerprint": fingerprint,
              "path": os.path.abspath(path),
              "board_sha256": _file_sha256(path),
              "client": results.get("client"),
              "run_id": results.get("run_id"),
              "plan_fingerprint": results.get("plan_fingerprint"),
              "model": results.get("model"),
              "confirmed_at": datetime.now().isoformat(timespec="seconds")}
    tmp = _approval_path(out_dir, kind) + ".tmp"
    Path(tmp).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _approval_path(out_dir, kind))
    board["status"] = "confirmed"
    board["confirmed_source_fingerprint"] = fingerprint
    tmp_result = result_json + ".tmp"
    Path(tmp_result).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_result, result_json)
    return record


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_shot_matches_plan(existing, shot, plan_fingerprint_value, out_path):
    """Reuse a shot only when checkpoint metadata and bytes prove provenance."""
    if not isinstance(existing, dict) or not os.path.isfile(out_path):
        return False
    if existing.get("plan_fingerprint") != plan_fingerprint_value:
        return False
    if (existing.get("shot") or {}) != shot:
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
        try:
            path = require_contained_path(
                out_dir, path, label="storyboard_shot", must_exist=True)
        except ValueError as exc:
            raise br_client.BRError(str(exc)) from exc
        seen.add(shot_id)
        shots.append({"id": shot_id, "path": path, "sha256": _file_sha256(path)})
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
    if any(record.get(key) != value for key, value in expected.items()):
        return False
    try:
        current = _storyboard_approval_record(results, result_json)
    except (OSError, br_client.BRError):
        return False
    return record.get("shots") == current.get("shots")


def render_storyboard(plan_path, out_dir, model=DEFAULT_MODEL, run_id=None, flat=False, bw=True,
                      stage="next", debug_allow_all=False, prompt_review=None):
    if stage == "all":
        raise br_client.BRError(
            "STAGE_ALL_BLOCKED: 生产流程禁止一次生成全部素材；请使用 --stage next 逐阶段推进。")
    if model != DEFAULT_MODEL:
        raise br_client.BRError(
            "STORYBOARD_MODEL_REQUIRED: 人物板、产品板和故事板必须使用 %s。" % DEFAULT_MODEL)
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
    os.makedirs(out_dir, exist_ok=True)
    # Use the plan's aspect ratio, not hardcoded 16:9. A 9:16 vertical project
    # must generate 9:16 storyboards so the composition matches the final video.
    ratio = plan.get("aspect_ratio") or "16:9"
    if run_id and os.path.basename(out_dir) != safe_name(run_id):
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
    out_dir = resolve_run_output_dir(out_dir, plan, run_id=run_id, flat=flat)
    os.makedirs(out_dir, exist_ok=True)

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
    results = {"ok": True, "model": model, "plan_fingerprint": current_plan_fingerprint,
               "client": plan.get("client") or client_slug(plan),
               "run_id": safe_name(run_id) if run_id else os.path.basename(out_dir),
               "out_dir": os.path.abspath(out_dir), "shots": [],
               "plan_source": os.path.abspath(plan_path),
               "plan_title": plan.get("project_title") or "",
               "expected_shot_ids": [str(shot.get("id") or index)
                                     for index, shot in enumerate(plan.get("shots") or [], 1)]}
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
            results["product_board"] = _pb.generate_from_reference_urls(
                api_key, product_urls, out_dir,
                product_type=(plan.get("product_type") or asset_refs.get("product_type") or "the exact product"),
                product_color=(plan.get("product_color") or (brief.get("product_color") if isinstance(brief, dict) else None)),
                style_hint=(plan.get("visual_style") or "commercial product reference photography"),
                model=model)
            results["product_board"]["board_type"] = "product_only_3x3"
            results["product_board"]["description"] = (
                "产品本体九宫格：只展示同一产品的多角度、结构、材质和接口，不包含人物或使用场景。")
            save_progress()
            print("[gpt-image-2] rendering conditional product board…", flush=True)
        except Exception as exc:
            raise br_client.BRError("检测到产品素材，但产品板生成失败：%s" % exc)
        product_fp = results["product_board"].get("source_fingerprint")
        product_approved = _approval_current(out_dir, "product", product_fp)
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
        print("[gpt-image-2] rendering cast board…", flush=True)
        cast_out = os.path.join(out_dir, "cast_board.jpg")
        # 人物板：用数字人肖像参考图做 img2img（确保生成的六视图与真实上传图一致）
        cast_fp = _source_refs_fingerprint(asset_refs.get("digital_human_portraits") or [])
        existing_cast = results.get("cast_board") or {}
        cast_source_changed = (existing_cast.get("source_fingerprint") and
                               existing_cast.get("source_fingerprint") != cast_fp)
        if cast_source_changed and os.path.isfile(cast_out):
            os.remove(cast_out)
        if not os.path.isfile(cast_out) or os.path.getsize(cast_out) == 0:
            results["cast_board"] = download_first_image(
                api_key, cp, cast_out, model=model, ratio="16:9",
                image_urls=portrait_urls or None,
                on_progress=progress_callback("cast_board", "cast", cast_out),
                resume_task_id=previous_task("cast_board", "cast"))
            results["cast_board"]["source_fingerprint"] = cast_fp
            results["cast_board"]["board_type"] = "digital_human_6_view"
            results["cast_board"]["description"] = "数字人六视图身份板：锁定脸部、发型、服装、配饰和身体比例。"
        else:
            results["cast_board"] = {"url": existing_cast.get("url", ""), "path": cast_out,
                                     "abspath": os.path.abspath(cast_out), "skipped": True,
                                     "source_fingerprint": existing_cast.get("source_fingerprint") or cast_fp}
        results.pop("in_progress", None)
        cast_approved = _approval_current(out_dir, "cast", cast_fp)
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
        has_human = bool(plan.get("characters") or any(
                shot.get("characters") or shot.get("character_prompt") or
                shot.get("character_action") or shot.get("digital_human") or
                shot.get("actor") for shot in plan.get("shots") or []))
        if not product_approved or (has_human and not cast_approved):
            raise br_client.BRError("GENERATION_BLOCKED: 产品使用图必须基于已确认的产品板和人物板生成。")
        usage_out = os.path.join(out_dir, "product_usage_board.jpg")
        product_board_path = results["product_board"].get("path")
        cast_board_path = (results.get("cast_board") or {}).get("path")
        usage_sources = [path for path in (cast_board_path, product_board_path) if path]
        usage_fp = _source_refs_fingerprint(usage_sources)
        existing_usage = results.get("product_usage_image") or {}
        if (existing_usage.get("source_fingerprint") and
                existing_usage.get("source_fingerprint") != usage_fp and os.path.isfile(usage_out)):
            os.remove(usage_out)
        usage_pose_refs = _collect_image_urls(
            (asset_refs.get("usage_reference_images") or []), api_key,
            fail_on_invalid=True, label="已确认佩戴姿势参考图")
        cast_usage_refs = (_collect_image_urls(
            [cast_board_path] if cast_board_path else portrait_urls, api_key,
            fail_on_invalid=True, label="已确认人物板") if has_human else [])
        product_usage_refs = _collect_image_urls(
            [product_board_path], api_key, fail_on_invalid=True, label="已确认产品板")
        # Keep all three semantic anchors: human identity, product identity, and
        # wearing-position guide. Do not let extra raw product refs evict the pose guide.
        usage_refs = _merge_reference_urls(
            cast_usage_refs, product_usage_refs, usage_pose_refs, limit=3)
        if not os.path.isfile(usage_out) or os.path.getsize(usage_out) == 0:
            print("[gpt-image-2] rendering product-in-use detail image…", flush=True)
            results["product_usage_image"] = download_first_image(
                api_key, product_usage_prompt(plan, include_human=has_human), usage_out,
                model=model, ratio="16:9",
                image_urls=usage_refs,
                on_progress=progress_callback("product_usage_image", "usage", usage_out),
                resume_task_id=previous_task("product_usage_image", "usage"),
                sync_img2img=True)
            results["product_usage_image"]["source_fingerprint"] = usage_fp
            results["product_usage_image"]["board_type"] = "usage_3x3"
            results["product_usage_image"]["description"] = (
                "产品使用九宫格：根据产品板与数字人板（如有）展示真实操作、手部接触和使用结果。")
        else:
            results["product_usage_image"] = {
                "url": existing_usage.get("url", ""), "path": usage_out,
                "abspath": os.path.abspath(usage_out), "skipped": True,
                "source_fingerprint": existing_usage.get("source_fingerprint") or usage_fp,
                "board_type": existing_usage.get("board_type") or "usage_3x3",
                "description": existing_usage.get("description") or
                "产品使用九宫格：根据产品板与数字人板（如有）展示真实操作、手部接触和使用结果。",
            }
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

    for idx, shot in enumerate(plan.get("shots") or [], 1):
        sid = safe_name(shot.get("id") or idx)
        print(f"[gpt-image-2] rendering storyboard shot {idx}: {sid}…", flush=True)
        out = os.path.join(out_dir, f"shot_{idx:02d}_{sid}.jpg")

        existing = next((item for item in results["shots"]
                         if item.get("shot", {}).get("id") == shot.get("id")), None)
        if _existing_shot_matches_plan(existing, shot, current_plan_fingerprint, out):
            print("[storyboard] reusing provenance-verified shot %s" % sid, flush=True)
            continue
        if os.path.isfile(out):
            # Never accept an orphaned or stale same-named file as a new result.
            os.remove(out)
            results["shots"] = [
                item for item in results["shots"]
                if item.get("shot", {}).get("id") != shot.get("id")
            ]

        # shot 级参考图：优先用 shot.asset_refs，否则用 plan 级合并包
        shot_asset_refs = shot.get("asset_refs") or {}
        shot_portrait = _collect_image_urls(
            shot_asset_refs.get("digital_human_portraits"), api_key,
            fail_on_invalid=True, label="镜头人物/佩戴参考图")
        shot_product = _collect_image_urls(
            shot_asset_refs.get("product_images"), api_key,
            fail_on_invalid=True, label="镜头产品参考图")
        shot_scene = _collect_image_urls(
            shot_asset_refs.get("scene_images"), api_key,
            fail_on_invalid=True, label="镜头场景参考图")

        # shot 级有专属参考图则用 shot 级；否则回落到 plan 级合并包
        if shot_portrait or shot_product or shot_scene:
            shot_ref_urls = []
            seen_s = set()
            # Shot-level refs are additional views, not a replacement for the
            # plan-level product/character anchors. Replacing the shared refs
            # when a shot only declares a scene image was the source of product
            # identity drift in storyboard generation.
            shot_ref_urls = _merge_reference_urls(
                usage_urls + cast_board_urls + shot_portrait + portrait_urls,
                product_board_urls + shot_product + product_urls,
                shot_scene + scene_urls,
            )
        else:
            shot_ref_urls = plan_ref_urls

        # Keep the default monochrome preview, but allow plan/shot color_mode to
        # override it. strict_bw=True used to silently discard that override.
        r = download_first_image(
            api_key, shot_prompt(plan, shot, idx, bw=bw, strict_bw=False), out, model=model, ratio=ratio,
            image_urls=shot_ref_urls or None,
            on_progress=progress_callback("shot", shot.get("id") or idx, out),
            resume_task_id=previous_task("shot", shot.get("id") or idx))
        r["plan_fingerprint"] = current_plan_fingerprint
        r["shot"] = shot
        # Replace a stale checkpoint entry when the image file was missing and
        # the task had to be regenerated; do not accumulate duplicate shots.
        results["shots"] = [
            item for item in results["shots"]
            if item.get("shot", {}).get("id") != shot.get("id")
        ]
        results["shots"].append(r)
        results.pop("in_progress", None)
        save_progress()

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
    p.add_argument("--stage", choices=["next", "product", "cast", "usage", "storyboard", "all"], default="next",
                    help="默认 next：一次只推进一个待确认阶段；all 仅限调试")
    p.add_argument("--debug-allow-all", action="store_true",
                   help="仅调试：允许 --stage all；生产流程禁止使用")
    p.add_argument("--confirm-board", choices=["product", "cast", "usage"],
                    help="确认已展示的产品板/人物板/产品使用图，必须同时传 --result-json")
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
            res = confirm_board(args.result_json, args.confirm_board)
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
                                prompt_review=args.prompt_review)
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
