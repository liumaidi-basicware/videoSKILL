#!/usr/bin/env python3
"""Video prompt building and truncation utilities.

Extracted from video_engine.py (v3 split). Contains:
  - DEFAULT_NEGATIVE constant (suppressed artifacts)
  - STORYBOARD_VIDEO_RULES (storyboard-to-video instruction block)
  - _fit_video_prompt_limit (gateway 2500-char limit compliance)
  - _submission_text (compile segment → model-ready prompt)
  - _require_confirmed_prompt_review (gate: prompts must be user-confirmed)

Dependencies: seedance_prompt, artifact_contract
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seedance_prompt
import artifact_contract


# ── Constants ───────────────────────────────────────────────────────────

# Default negative prompt: suppress AI video artifacts (deformities, text, etc.)
DEFAULT_NEGATIVE = ("模糊, 畸形, 多余手指, 手部畸形, 面部扭曲, 文字乱码, 水印, "
                    "字幕, 文字, 悬浮文字, kinetic typography, 逐字动画文字, 数据标签快闪, "
                    "字幕条, 说明文字, slogan文字, 低分辨率, 噪点, 抖动, 画面撕裂, 多余肢体, 变形")

NATIVE_STORYBOARD_VIDEO_RULES = """
【Seedance 原生故事板转视频规则】
Use the uploaded FINAL storyboard/contact sheet as Seedance-native storyboard guidance for this clip.
Follow the specified current segment, panel index, director text, dialogue, timeline, references and continuity contract.
Read the storyboard as a previs guide for composition, shot order, camera movement, action beats and lighting, but never render the grid, panel borders, labels, arrows, notes, text, subtitles or watermark.
Annotation legend for reading only: RED arrows = body / subject movement; BLUE arrows = camera movement; GREEN marks = framing and composition notes; ORANGE marks = lighting direction; PURPLE marks = sound and emotional emphasis; BLACK text = short shot notes and panel labels. Do NOT render any arrows, marks, labels or notes.
The storyboard may be monochrome previs; final output must be photorealistic live-action, full-color commercial footage with real materials, natural lighting and clean camera motion. NEVER render the video as a sketch, pencil drawing, charcoal drawing, storyboard panel, animatic, comic strip or grayscale previs.
Strictly preserve the confirmed product, character when present, props, spatial relationship, wardrobe, voice, BGM mood and lighting. Do not add extra characters or change the plot.
"""

PANEL_REFERENCE_VIDEO_RULES = """
【单镜头展开图转视频规则】
Use the uploaded SINGLE 16:9 reference plate as this clip's composition, motion and lighting guide.
It is one expanded frame prepared from the customer-approved plan, not a multi-panel board and not a grid.
Generate only this clip's described action. Do NOT infer other panels, animate a contact sheet, create split-screen panels, or render any annotation marks.
The reference plate may be monochrome or previs-like; the final video MUST be photorealistic live-action, full-color commercial footage with real materials, natural lighting, believable product scale and clean camera motion. NEVER render the video as a sketch, pencil drawing, charcoal drawing, storyboard panel, animatic, comic strip or grayscale previs.
Strictly preserve the uploaded confirmed product, character when present, props, spatial relationships and scene lighting. Do not add text, subtitles, logos, extra characters, arrows, labels or watermark.
"""

# Backward-compatible export.
STORYBOARD_VIDEO_RULES = NATIVE_STORYBOARD_VIDEO_RULES


# ── Prompt building ────────────────────────────────────────────────────

def _uses_native_storyboard_prompt(segment, model):
    mode = str((segment or {}).get("storyboard_ref_mode") or "").strip().lower()
    if mode in ("expanded_panel", "single_panel", "single_shot_keyframe"):
        return False
    return (not mode or mode in ("native_storyboard", "contact_sheet", "storyboard_contact_sheet")) \
        and seedance_prompt.is_seedance_model(model)


def _is_kling_video_model(model):
    return "kling" in str(model or "").lower()


def _model_prompt_keys(model):
    keys = [str(model)]
    if seedance_prompt.is_seedance_model(model):
        keys.append("seedance")
    if _is_kling_video_model(model):
        keys.append("kling")
    return list(dict.fromkeys(key for key in keys if key))


def _same_prompt_model(approved_model, model):
    return bool(set(_model_prompt_keys(approved_model)) & set(_model_prompt_keys(model)))


def _approved_submission_for_model(segment, model):
    by_model = segment.get("approved_submission_prompts_by_model") or {}
    if isinstance(by_model, dict):
        for key in _model_prompt_keys(model):
            prompt = by_model.get(key)
            if prompt:
                return prompt
    approved_submission = segment.get("approved_submission_prompt_zh")
    approved_model = segment.get("approved_prompt_model")
    if approved_submission and (not approved_model or _same_prompt_model(approved_model, model)):
        return approved_submission
    return None


def _assert_confirmed_submission_for_model(segment, model):
    has_confirmed_submission = bool(
        segment.get("approved_submission_prompt_zh") or
        segment.get("approved_submission_prompts_by_model"))
    if has_confirmed_submission and not _approved_submission_for_model(segment, model):
        raise ValueError(
            "PROMPT_REVIEW_REQUIRED_FOR_MODEL: 镜头 %s 缺少模型 %s 的已确认完整视频提交提示词" %
            (segment.get("id"), model))


def _storyboard_video_text(text, segment=None, model=None, storyboard_ref=False):
    """Wrap text with storyboard-to-video rules if storyboard_ref is enabled."""
    if storyboard_ref:
        rules = (NATIVE_STORYBOARD_VIDEO_RULES if _uses_native_storyboard_prompt(segment or {}, model)
                 else PANEL_REFERENCE_VIDEO_RULES)
        return rules.strip() + "\n\n【本段台词/剧情】\n" + text
    return text


def _compile_seedance_text(segment, model):
    """Compile segment fields into a model-appropriate text prompt.

    Uses seedance_prompt.build() to produce the base text, then appends
    audio contract and render plan if the model supports them.
    """
    text = seedance_prompt.build(segment, model=model)
    return text


def _fit_video_prompt_limit(text, segment, model, limit=2400):
    """Keep provider prompt below the gateway's 2500-character hard limit.

    Preserve business-critical dialogue, action, product identity and
    continuity contract while dropping duplicated explanatory prose first.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    audio = segment.get("audio_contract") or {}
    audio_method = " ".join(item for item in (
        "Voice method: %s." % audio.get("voice_continuity_method") if audio.get("voice_continuity_method") else "",
        "BGM method: %s." % audio.get("bgm_continuity_method") if audio.get("bgm_continuity_method") else "",
        "SFX method: %s." % audio.get("sfx_continuity_method") if audio.get("sfx_continuity_method") else "",
        "Media reference method: %s." % audio.get("media_reference_method") if audio.get("media_reference_method") else "",
    ) if item)
    director_brief = segment.get("director_brief") or {}
    director_summary = ""
    if isinstance(director_brief, dict) and director_brief:
        director_summary = "; ".join(item for item in (
            "narrative=%s" % director_brief.get("narrative_function") if director_brief.get("narrative_function") else "",
            "beats=%s" % " / ".join(map(str, director_brief.get("timeline_beats") or [])) if director_brief.get("timeline_beats") else "",
            "start=%s" % director_brief.get("start_state") if director_brief.get("start_state") else "",
            "end=%s" % director_brief.get("end_state") if director_brief.get("end_state") else "",
            "camera=%s" % director_brief.get("camera_motion") if director_brief.get("camera_motion") else "",
            "dialogue=%s" % director_brief.get("dialogue_delivery") if director_brief.get("dialogue_delivery") else "",
            "edit=%s" % director_brief.get("edit_continuity") if director_brief.get("edit_continuity") else "",
            "refs=%s" % director_brief.get("reference_priority") if director_brief.get("reference_priority") else "",
        ) if item)
    fields = [
        ("Commercial video. Use the Seedance-native storyboard/contact sheet for the specified current segment; annotation colors/arrows/marks are reading-only. Do NOT render any arrows, marks, labels, notes, grid, storyboard border, text, subtitles, logos or watermark. NEVER render the video as a sketch, pencil drawing, charcoal drawing, storyboard panel, animatic or grayscale previs; final output is photorealistic live-action."
         if _uses_native_storyboard_prompt(segment, model)
         else "Commercial video. Use only the current shot's expanded SINGLE 16:9 reference plate; annotation colors/arrows/marks are reading-only. Do NOT render any arrows, marks, labels, notes, grid, storyboard border, split screens, text, subtitles, logos or watermark. NEVER render the video as a sketch, pencil drawing, charcoal drawing, storyboard panel, animatic or grayscale previs; final output is photorealistic live-action."),
        "Dialogue: %s" % (audio.get("dialogue") or segment.get("dialogue") or ""),
        "Director text: %s" % (segment.get("text") or ""),
        "Director brief: %s" % director_summary,
        "Visual: %s" % (segment.get("visual") or ""),
        "Action: %s" % (segment.get("character_action") or segment.get("action") or ""),
        "Camera: %s" % (segment.get("camera_movement") or segment.get("camera") or ""),
        "Scene: %s" % (segment.get("scene_prompt") or ""),
        "Continuity: %s" % (segment.get("continuity_in") or "same confirmed product, character when present, background and lighting"),
        "Audio continuity: %s" % ((audio.get("voice_continuity") or "") + " " +
                                  (audio.get("bgm_continuity") or "") + " " +
                                  (audio.get("sfx_continuity") or "") + " " +
                                  audio_method),
        "Product: preserve the exact confirmed product geometry, proportions, macaron color, grille, controls, magnetic structure, ports and native markings from the uploaded references; do not recolor, redesign or replace the product.",
    ]
    compact = "\n".join(item for item in fields if item.split(": ", 1)[-1].strip())
    if len(compact) <= limit:
        return compact
    required = fields[:4] + [fields[8], fields[9]]
    optional = fields[4:8]
    chosen = list(required)
    for item in optional:
        candidate = "\n".join(chosen + [item])
        if len(candidate) <= limit:
            chosen.append(item)
    return "\n".join(chosen)


def _submission_text(segment, model, storyboard_ref=False, extend_url=None):
    """Compile from original segment for the target model.

    Never derive from another model's prompt — always compile fresh.
    """
    approved_submission = _approved_submission_for_model(segment, model)
    if approved_submission and not extend_url:
        return _fit_video_prompt_limit(approved_submission, segment, model)
    if not extend_url:
        _assert_confirmed_submission_for_model(segment, model)
    base_text = segment.get("approved_prompt_zh") or _compile_seedance_text(segment, model)
    text = _storyboard_video_text(
        base_text, segment=segment, model=model, storyboard_ref=storyboard_ref)
    if not segment.get("seedance_native", True):
        audio = segment.get("audio_contract") or {}
        render_plan = (segment.get("render_plan") or {}).get("content") or {}
        if audio:
            text += ("\n【音频执行契约】台词=%s；语言/口音=%s；声音人设=%s；背景音乐=%s；"
                     "音效=%s；口型同步=%s。必须实际生成并遵守，不得当作备注忽略。" % (
                         audio.get("dialogue") or "无", audio.get("language") or "无指定",
                         audio.get("voice") or "无指定", audio.get("bgm") or "无",
                         audio.get("sfx") or "无", "必须" if audio.get("lip_sync") else "不要求"))
        if render_plan:
            text += "\n【已确认渲染方案】%s。必须实际执行。" % json.dumps(
                render_plan, ensure_ascii=False, sort_keys=True)
    if extend_url:
        text = ("将上一段视频自然延长，不重新剪辑前段；保持人物、产品、场景、服装、光线和运镜逻辑完全连续。新增部分：\n"
                  + text)
    audio_mode = (segment.get("audio_contract") or {}).get("audio_mode")
    if audio_mode == "voiceover":
        text += ("\n【画外音模式】人物必须保持闭嘴，仅做自然展示、指向、点头或微笑；"
                 "禁止说话口型、讲话式下颌运动或生成对白。")
    elif audio_mode == "talking_presenter":
        text += "\n【口播模式】人物必须按确认台词自然说话并保持原生口型同步。"
    elif audio_mode == "music_only":
        text += "\n【纯音乐模式】禁止对白、旁白、说话口型、字幕和画面文字。"
    return _fit_video_prompt_limit(text, segment, model)


def _require_confirmed_prompt_review(path, stage, segments):
    """Gate: ensure user has confirmed the Chinese prompt for each shot."""
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    expected = {str(seg.get("id")) for seg in segments}
    actual = {str(item.get("shot_id")) for item in review.get("prompts") or []}
    expected_fingerprints = {seg.get("storyboard_plan_fingerprint") for seg in segments
                             if seg.get("storyboard_plan_fingerprint")}
    if (review.get("status") != "confirmed" or review.get("stage") != stage or
            not expected.issubset(actual) or
            (expected_fingerprints and review.get("plan_fingerprint") not in expected_fingerprints)):
        raise ValueError(
            "PROMPT_REVIEW_REQUIRED: %s 阶段的中文提示词必须先由用户确认；缺少=%s" %
            (stage, ",".join(sorted(expected - actual))))
    prompts = {str(item.get("shot_id")): item
               for item in review.get("prompts") or []}
    for segment in segments:
        item = prompts.get(str(segment.get("id"))) or {}
        base_prompt = item.get("prompt_zh")
        submission_prompt = item.get("submission_prompt_zh")
        if not base_prompt and not submission_prompt:
            raise ValueError("PROMPT_REVIEW_REQUIRED: 缺少镜头 %s 的确认视频提示词" % segment.get("id"))
        if base_prompt:
            segment["approved_prompt_zh"] = base_prompt
        model_prompts = {}
        raw_model_prompts = item.get("model_submission_prompts") or {}
        if isinstance(raw_model_prompts, dict):
            model_prompts.update({str(k): v for k, v in raw_model_prompts.items() if v})
        for fallback in item.get("fallback_submission_prompts") or []:
            if isinstance(fallback, dict) and fallback.get("model") and fallback.get("submission_prompt_zh"):
                model_prompts[str(fallback["model"])] = fallback["submission_prompt_zh"]
        if submission_prompt:
            segment["approved_submission_prompt_zh"] = submission_prompt
            if item.get("model"):
                segment["approved_prompt_model"] = item.get("model")
                model_prompts.setdefault(str(item.get("model")), submission_prompt)
        if model_prompts:
            segment["approved_submission_prompts_by_model"] = model_prompts
        if item.get("director_brief"):
            segment["director_brief"] = item["director_brief"]
