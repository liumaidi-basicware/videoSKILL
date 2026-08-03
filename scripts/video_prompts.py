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

STORYBOARD_VIDEO_RULES = """
【12格故事板转视频硬性规则】
Use the uploaded FINAL 16:9 4x3 twelve-panel storyboard contact sheet as the primary visual reference.
The storyboard is a monochrome pencil/charcoal PREVIS for composition only, not the finished visual style.
Follow the shot order from panel 1 to panel 12 strictly. Do NOT animate the entire image at once.
Generate continuous video following the panel sequence. Do NOT add extra characters, logos, or text.
Maintain character/product/scene/lighting consistency across all shots.
Adjacent shots must have 30°-50° angle offset or shot-size change for editability.
"""


# ── Prompt building ────────────────────────────────────────────────────

def _storyboard_video_text(text, storyboard_ref=False):
    """Wrap text with storyboard-to-video rules if storyboard_ref is enabled."""
    if storyboard_ref:
        return text + "\n" + STORYBOARD_VIDEO_RULES.strip()
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
    fields = [
        "Kling commercial video. Follow the confirmed storyboard shot order; do not add text, subtitles, logos or extra characters.",
        "Dialogue: %s" % (audio.get("dialogue") or segment.get("dialogue") or ""),
        "Visual: %s" % (segment.get("visual") or ""),
        "Action: %s" % (segment.get("character_action") or segment.get("action") or ""),
        "Camera: %s" % (segment.get("camera_movement") or segment.get("camera") or ""),
        "Scene: %s" % (segment.get("scene_prompt") or ""),
        "Continuity: %s" % (segment.get("continuity_in") or "same confirmed character, grey product, background and lighting"),
        "Product: preserve exact confirmed geometry, neutral grey color, magnetic face and ports.",
    ]
    compact = "\n".join(item for item in fields if item.split(": ", 1)[-1].strip())
    if len(compact) <= limit:
        return compact
    required = fields[:2] + [fields[6], fields[7]]
    optional = fields[2:6]
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
    text = segment.get("approved_prompt_zh") or _storyboard_video_text(
        _compile_seedance_text(segment, model), storyboard_ref)
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
    prompts = {str(item.get("shot_id")): item.get("prompt_zh")
               for item in review.get("prompts") or []}
    for segment in segments:
        if not prompts.get(str(segment.get("id"))):
            raise ValueError("PROMPT_REVIEW_REQUIRED: 缺少镜头 %s 的确认视频提示词" % segment.get("id"))
        segment["approved_prompt_zh"] = prompts[str(segment.get("id"))]
