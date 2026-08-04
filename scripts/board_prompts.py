#!/usr/bin/env python3
"""Storyboard and cast-board prompt builders.

Extracted from storyboard.py (v3 split). Contains:
  - shot_prompt (per-segment 12-panel storyboard prompt for gpt-image-2)
  - cast_prompt (six-view character reference board prompt)
  - product_usage_prompt (human-product interaction detail board)
  - _subject_definition_block (character identity anchor block)
  - _panel_prompt_block (12-panel timeline block)
  - CONTINUITY_LOCK constant

Dependencies: board_plan (for normalize_panel_plan)
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from board_plan import normalize_panel_plan

# 安全区提示（与 motion_design.SAFE_ZONES 对齐）
_SAFE_ZONE_STORYBOARD_HINTS = {
    "lower_third": "构图时下方留 25% 空白区，不放置关键主体、人脸或产品细节（后期字幕叠加区）",
    "upper_third": "构图时上方留 20% 空白区，不放置关键主体或人脸（后期标题叠加区）",
    "center": "构图时中央留白，主体偏左或偏右（后期标题/关键词居中叠加）",
    "corner": "构图时右上角留空，不放置关键元素（后期数据卡片叠加区）",
    "left": "构图时左侧留 30% 空白（后期文字内容叠加区）",
    "right": "构图时右侧留 30% 空白（后期文字内容叠加区）",
}

CONTINUITY_LOCK = (
    "CONTINUITY LOCK: identical character identity (face, hairstyle, build, "
    "wardrobe, accessories) across all panels and segments. Same product "
    "appearance, color, geometry and orientation. Same background, lighting "
    "direction, and color temperature. Same voice tone and language."
)

BW_GLOBAL_CONSTRAINT = (
    "Pure monochrome (black, white, and grey values only). No color, no hue. "
    "Use pencil/charcoal tonal rendering to express depth, material, and lighting. "
    "No text, subtitles, logos, watermarks, or kinetic typography in any panel."
)


def _subject_definition_block(plan, shot, chars):
    """Build the character identity anchor block for a shot prompt.

    This is the highest-weight identity anchor — includes face, hair,
    wardrobe, accessories, and immutable features to prevent ID drift
    across panels (seedance root cause countermeasure).
    """
    lines = []
    for char_id in (shot.get("characters") or []):
        char = next((c for c in chars if c.get("id") == char_id), None)
        if not char:
            continue
        lines.append("Character %s (%s):" % (char_id, char.get("name", "")))
        for field in ("appearance", "facial_features", "hair", "makeup",
                       "body_features", "costume", "accessories", "shoes"):
            v = char.get(field)
            if v:
                lines.append("  %s: %s" % (field.replace("_", " "), v))
        # Immutable identity features (face shape, eye spacing, etc.)
        immutable = char.get("immutable_features") or {}
        if immutable:
            lines.append("  immutable identity: %s" % json.dumps(immutable, ensure_ascii=False))
    return "\n".join(lines) if lines else ""


def _panel_prompt_block(shot):
    """Build the 12-panel timeline block for a storyboard shot."""
    panels = normalize_panel_plan(shot)
    lines = ["Panel timeline (follow strictly, panel 1 to 12):"]
    for i, panel in enumerate(panels, 1):
        lines.append("  Panel %d: %s" % (i, panel))
    return "\n".join(lines)


def shot_prompt(plan, shot, idx, bw=True, strict_bw=False):
    """Build the complete prompt for a 12-panel storyboard image.

    Combines: subject definition + visual style + panel timeline + 
    continuity lock + no-text constraint + audio notes.
    """
    chars = plan.get("characters") or []
    subject_block = _subject_definition_block(plan, shot, chars)
    panel_block = _panel_prompt_block(shot)

    style = plan.get("visual_style", "clean premium commercial")
    aspect = plan.get("aspect_ratio", "16:9")

    parts = [
        "Cinematic 16:9 storyboard contact sheet, 4 rows × 3 columns = 12 panels.",
        "Shot %d (%s): %s" % (idx, shot.get("id", ""), shot.get("visual", "")),
    ]

    if subject_block:
        parts.append(subject_block)

    if shot.get("camera"):
        parts.append("Camera: %s" % shot["camera"])
    if shot.get("shot_size"):
        parts.append("Shot size: %s" % shot["shot_size"])
    if shot.get("camera_movement"):
        parts.append("Camera movement: %s" % shot["camera_movement"])
    if shot.get("character_action"):
        parts.append("Action: %s" % shot["character_action"])
    if shot.get("scene_prompt"):
        parts.append("Scene: %s" % shot["scene_prompt"])
    if shot.get("composition"):
        parts.append("Composition: %s" % shot["composition"])
    if shot.get("lighting"):
        parts.append("Lighting: %s" % shot["lighting"])
    if shot.get("prop_prompts"):
        parts.append("Props: %s" % "; ".join(shot["prop_prompts"]))

    audio = shot.get("audio") or {}
    if audio:
        parts.append("Audio: voice=%s, bgm=%s, sfx=%s" % (
            audio.get("voice", ""), audio.get("bgm", ""), audio.get("sfx", "")))

    if panel_block:
        parts.append(panel_block)

    # 动效安全区：故事板构图预留文字叠加空间
    safe_zones = shot.get("video_safe_zones") or []
    motion_design = shot.get("motion_design") or {}
    if not safe_zones and motion_design:
        safe_zones = motion_design.get("video_safe_zones") or []
    if safe_zones:
        hints = [_SAFE_ZONE_STORYBOARD_HINTS[z] for z in safe_zones
                 if z in _SAFE_ZONE_STORYBOARD_HINTS]
        if hints:
            parts.append("构图安全区（重要）：%s。" % "；".join(hints))

    parts.append(CONTINUITY_LOCK)

    if bw:
        parts.append(BW_GLOBAL_CONSTRAINT)
    elif strict_bw:
        parts.append(BW_GLOBAL_CONSTRAINT + " STRICT: absolutely no color whatsoever.")

    parts.append(
        "No text, subtitles, logos, watermarks, or generated characters in any panel. "
        "Do not animate the contact sheet. This is a static reference image."
    )

    return "\n".join(parts)


def cast_prompt(plan):
    """Build the six-view character reference board prompt.

    Each character gets 6 views: full-body front, full-body back, full-body side,
    face front, face back/top, face side. All views must be the same identity.
    """
    chars = plan.get("characters") or []
    parts = ["Character reference sheet with six required views per character:"]

    for char in chars:
        parts.append("\nCharacter: %s (%s)" % (char.get("id", ""), char.get("name", "")))
        parts.append("Role: %s" % char.get("role", ""))
        for field in ("appearance", "facial_features", "hair", "makeup",
                       "body_features", "costume", "accessories", "shoes"):
            v = char.get(field)
            if v:
                parts.append("  %s: %s" % (field.replace("_", " "), v))

        parts.append("Six views (same identity, same outfit, same hairstyle):")
        parts.append("  1. Full-body front view")
        parts.append("  2. Full-body back view")
        parts.append("  3. Full-body side view")
        parts.append("  4. Face front close-up (neutral expression, high detail)")
        parts.append("  5. Face back/top view (showing hairstyle from behind)")
        parts.append("  6. Face side profile (3/4 view)")

        # Identity lock: close-up and full-body must be same person
        parts.append("  IDENTITY LOCK: the face in close-up views MUST be the same "
                     "person as the full-body views (same face shape, eye spacing, "
                     "jawline, hairline, skin tone). If they look like different "
                     "people, the board is不合格.")

    parts.append("\nNo text, subtitles, labels, or watermarks.")
    parts.append("Clean neutral background for all views.")

    return "\n".join(parts)


def product_usage_prompt(plan, shot=None, include_human=True):
    """Build the human-product interaction detail board prompt.

    Shows the character actually using the product with visible hand contact,
    grip, and operational detail. Used as high-priority reference for
    subsequent storyboards.
    """
    parts = ["Human-product interaction detail board."]
    parts.append(
        "Show a person's hands realistically holding and using the product. "
        "Preserve the exact uploaded product geometry, scale, color, orientation "
        "and functional parts. Show anatomically plausible finger placement, "
        "all real contact points, grip pressure and occlusion; the product must "
        "remain on the correct side and in the correct operating position."
    )
    parts.append(
        "Hands must have exactly five fingers each: no extra fingers, fused "
        "fingers, missing fingers, duplicated hands, floating product, "
        "hand-product intersection, skin passing through the product, or "
        "impossible contact. The visible result of the action must match the "
        "intended use."
    )
    parts.append("No captions, subtitles, generated text, logo, watermark or extra product.")

    if include_human:
        chars = plan.get("characters") or []
        if chars:
            char = chars[0]
            parts.append("Person: %s — same identity as confirmed cast board." % char.get("name", ""))

    return "\n".join(parts)
