#!/usr/bin/env python3
"""Validate storyboard plans before spending image-generation credits."""
import argparse
import json
import re
import sys
from aspect_ratio import OUTPUT_RATIOS, STORYBOARD_RATIO, output_ratio

REQUIRED_SHOT_FIELDS = ("shot_size", "camera_movement", "composition", "lighting")
# These describe post-production graphics, not things the image model should draw.
# Keep the matcher broad enough for common Chinese phrasing such as "价格浮现".
TEXT_IN_FRAME = re.compile(
    r"字幕|slogan|kinetic typography|文字条|参数标签|悬浮文字|字幕条|"
    r"浮现(?:价格|金额|数字|文字|标签|标题|口号|slogan)|"
    r"(?:价格|金额|数字|文字|标签|标题|口号|slogan)(?:浮现|出现|显示|弹出|快闪|闪现)(?:效果)?|"
    r"逐字|打字机(?:效果)?|文字快闪|标签快闪|价格标签|数据卡片|"
    r"floating slogan|on-screen text|text overlay|lower third",
    re.I,
)
WEAK_VISUAL = re.compile(r"高级感|好看|震撼|电影感|氛围感|premium|cinematic", re.I)
REFERENCE_TYPES = {"character_identity", "product_identity", "scene_environment",
                   "storyboard_composition", "continuation_frame", "generic_visual",
                   "character_board", "product_board", "product_usage_identity"}
REFERENCE_SCOPES = {"global", "scene", "clip", "beat"}
DIRECTOR_FIELDS = ("narrative_function", "felt_intent", "director_voice", "arc_position")
# 2026-08-05 修订：格数不再固定为12，由剧本分镜数量决定。建议区间 4-12
# （少于4通常说明分镜切得太粗，超过12建议拆成多段视频，见 AGENTS.md 铁律#4）。
MIN_PANEL_COUNT = 4
MAX_PANEL_COUNT = 12
TAG_PATTERN = re.compile(r"^@[a-z][a-z0-9_]*$")


def validate_plan(plan):
    errors, warnings = [], []
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["计划必须是 JSON 对象"], "warnings": []}
    try:
        output_ratio(plan)
    except ValueError:
        errors.append("output_ratio 必须为: %s" % ", ".join(sorted(OUTPUT_RATIOS)))
    if plan.get("storyboard_aspect_ratio", STORYBOARD_RATIO) != STORYBOARD_RATIO:
        errors.append("storyboard_aspect_ratio 必须为 16:9")
    shots = plan.get("shots") or []
    if not shots:
        errors.append("shots 不能为空")
    characters = plan.get("characters") or []
    character_ids = set()
    for index, character in enumerate(characters, 1):
        cid = character.get("id") if isinstance(character, dict) else None
        if not cid:
            errors.append("characters[%d] 缺少 id" % index)
        elif cid in character_ids:
            errors.append("角色 id 重复: %s" % cid)
        else:
            character_ids.add(cid)
        if isinstance(character, dict) and not (character.get("appearance") or character.get("facial_features")):
            warnings.append("角色 %s 缺少可用于人物板的外貌/五官描述" % (cid or index))
    shot_ids = set()
    any_scene = any(isinstance(shot, dict) and shot.get("scene_id") for shot in shots)
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            errors.append("shot %s 必须是对象" % index)
            continue
        sid = shot.get("id", index)
        if any_scene and not str(shot.get("scene_id") or "").strip():
            warnings.append("shot %s 缺少 scene_id，将被视为独立场景边界" % sid)
        if sid in shot_ids:
            errors.append("shot id 重复: %s" % sid)
        shot_ids.add(sid)
        duration_value = shot.get("duration", shot.get("duration_sec"))
        if duration_value is not None:
            try:
                duration = float(duration_value)
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                errors.append("shot %s 的 duration 必须大于 0" % sid)
        else:
            warnings.append("shot %s 缺少 duration，出片前需要补齐镜头时长" % sid)
        panel_plan = shot.get("panel_plan") or shot.get("twelve_panel_plan") or []
        if not panel_plan:
            errors.append("shot %s 缺少 panel_plan（格数由剧本分镜数量决定，不能为空）" % sid)
        elif not (MIN_PANEL_COUNT <= len(panel_plan) <= MAX_PANEL_COUNT):
            warnings.append(
                "shot %s 的 panel_plan 有 %d 项，建议区间 %d-%d 格（少于%d通常说明分镜切得太粗，"
                "超过%d建议拆成多段视频），禁止为了凑数而拆分/合并分镜"
                % (sid, len(panel_plan), MIN_PANEL_COUNT, MAX_PANEL_COUNT,
                   MIN_PANEL_COUNT, MAX_PANEL_COUNT))
        if shot.get("nine_panel_plan"):
            errors.append("shot %s 使用了旧的 nine_panel_plan 字段，请统一改用 panel_plan" % sid)
        for field in REQUIRED_SHOT_FIELDS:
            if not shot.get(field):
                warnings.append("shot %s 缺少 %s" % (sid, field))
        prompt_fields = [shot.get(k, "") for k in ("visual", "scene_prompt", "prop_prompts")]
        if TEXT_IN_FRAME.search(" ".join(str(v) for v in prompt_fields)):
            errors.append("shot %s 的画面提示词疑似要求生成文字，应迁移到 motion_elements" % sid)
        if not (shot.get("character_action") or shot.get("action") or shot.get("visual")):
            warnings.append("shot %s 缺少动作描述" % sid)
        if shot.get("visual") and WEAK_VISUAL.search(str(shot.get("visual"))) and len(str(shot.get("visual"))) < 80:
            warnings.append("shot %s 的 visual 过于抽象，建议补充可见动作、主体位置和结果" % sid)
        if not (shot.get("character_action") or shot.get("action")):
            warnings.append("shot %s 缺少可执行的 physical action，建议写一个明确动作及其结果" % sid)
        refs = shot.get("characters") or []
        if isinstance(refs, str):
            refs = [refs]
        unknown = [ref for ref in refs if ref not in character_ids]
        if unknown:
            errors.append("shot %s 引用了不存在的角色: %s" % (sid, ", ".join(map(str, unknown))))
        if len(panel_plan) >= 2 and len(set(map(str, panel_plan))) < len(panel_plan):
            warnings.append("shot %s 的 panel_plan 存在重复描述，建议每格对应明确的动作或机位变化" % sid)
        known_tags = set()
        for ref_index, ref in enumerate(shot.get("references") or [], 1):
            if not isinstance(ref, dict) or not ref.get("url"):
                errors.append("shot %s references[%d] 必须是含 url 的对象" % (sid, ref_index))
                continue
            if ref.get("type") and ref["type"] not in REFERENCE_TYPES:
                warnings.append("shot %s references[%d] 使用未知 type: %s" % (sid, ref_index, ref["type"]))
            if ref.get("scope") and ref["scope"] not in REFERENCE_SCOPES:
                errors.append("shot %s references[%d] scope 非法: %s" % (sid, ref_index, ref["scope"]))
            tag = ref.get("tag")
            if tag:
                if not TAG_PATTERN.match(str(tag)):
                    errors.append("shot %s references[%d] 的 tag 格式非法: %s（须匹配 ^@[a-z][a-z0-9_]*$）"
                                   % (sid, ref_index, tag))
                else:
                    known_tags.add(str(tag))
            else:
                warnings.append("shot %s references[%d] 缺少 tag，无法在 prompt 中内联引用" % (sid, ref_index))
        ref_tags = shot.get("ref_tags") or []
        if isinstance(ref_tags, str):
            ref_tags = [ref_tags]
        unknown_tags = [t for t in ref_tags if t not in known_tags]
        if unknown_tags:
            errors.append("shot %s 的 ref_tags 引用了不存在的参考图标签: %s（需先在 references[].tag 中定义）"
                           % (sid, ", ".join(map(str, unknown_tags))))

        contract = shot.get("clip_contract")
        if contract is not None:
            if not isinstance(contract, dict):
                errors.append("shot %s clip_contract 必须是对象" % sid)
            else:
                scope = contract.get("scope") or {}
                buckets = [set(scope.get(key) or []) for key in
                           ("already_happened", "this_clip_only", "reserved_for_later")]
                if any(buckets[a] & buckets[b] for a in range(3) for b in range(a + 1, 3)):
                    errors.append("shot %s clip_contract 的剧情作用域存在重叠" % sid)
        sequence = shot.get("sequence_state")
        if sequence is not None and (not isinstance(sequence, dict)
                                     or not isinstance(sequence.get("entry", {}), dict)
                                     or not isinstance(sequence.get("exit", {}), dict)):
            errors.append("shot %s sequence_state.entry/exit 必须是对象" % sid)
        for field in DIRECTOR_FIELDS:
            if not shot.get(field):
                warnings.append("shot %s 缺少导演意图字段 %s" % (sid, field))
    panel_counts = [len(shot.get("panel_plan") or shot.get("twelve_panel_plan") or [])
                    for shot in shots if isinstance(shot, dict)]
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "shot_count": len(shots),
            "storyboard": {"aspect_ratio": STORYBOARD_RATIO, "panels": panel_counts}}


def _text_motion_chunks(value):
    """Return text fragments that belong in the post-production motion layer."""
    if not isinstance(value, str) or not value.strip():
        return []
    # Chinese punctuation and semicolons are reliable boundaries for the short
    # prompt clauses produced by the script co-creation flow.
    return [match.group(0).strip() for match in TEXT_IN_FRAME.finditer(value)]


def normalize_plan_motion_elements(plan):
    """Move accidental text-animation instructions out of image prompt fields.

    This is intentionally a separate, explicit normalization pass rather than
    weakening validation. The storyboard prompt must remain a clean plate while
    the migrated instructions stay available for HyperFrames after video
    generation.
    """
    if not isinstance(plan, dict):
        return plan, []
    normalized = json.loads(json.dumps(plan, ensure_ascii=False))
    moved = []
    prompt_fields = ("visual", "scene_prompt", "prop_prompts", "props", "scene")

    def clean(value, path):
        if isinstance(value, str):
            chunks = _text_motion_chunks(value)
            if not chunks:
                return value
            kept = value
            for chunk in chunks:
                kept = kept.replace(chunk, "")
                moved.append({"path": path, "text": chunk})
            kept = re.sub(r"\s{2,}", " ", kept)
            kept = re.sub(r"\s+([，。；！？,.;!?])", r"\1", kept)
            return kept.strip(" \t\r\n，,；;。")
        if isinstance(value, list):
            return [clean(item, "%s[%d]" % (path, i)) for i, item in enumerate(value)]
        return value

    for index, shot in enumerate(normalized.get("shots") or []):
        if not isinstance(shot, dict):
            continue
        shot_moved = []
        for field in prompt_fields:
            if field in shot:
                before = len(moved)
                shot[field] = clean(shot[field], "shots[%d].%s" % (index, field))
                shot_moved.extend(item["text"] for item in moved[before:])
        existing = shot.get("motion_elements") or []
        if isinstance(existing, str):
            existing = [existing]
        if shot_moved:
            # De-duplicate while preserving authored order and append only the
            # newly migrated clauses.
            shot["motion_elements"] = list(dict.fromkeys(
                [str(item) for item in existing if str(item).strip()] + shot_moved
            ))
        elif existing:
            shot["motion_elements"] = list(existing)
    return normalized, moved


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate storyboard_plan.json")
    parser.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.plan, encoding="utf-8") as handle:
            plan = json.load(handle)
        result = validate_plan(plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "errors": ["无法读取故事板计划: %s" % exc], "warnings": []}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
