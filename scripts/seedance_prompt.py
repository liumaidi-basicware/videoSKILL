#!/usr/bin/env python3
"""Compile structured shot data into a Seedance-native video prompt."""
import re

SEEDANCE_MODELS = {"seedance-2.0", "seedance-2.0-fast", "seedance-2.0-white"}
SEEDANCE_MAX_DURATION = 15
MOTION_TERMS = ("static", "locked", "push", "pull", "pan", "tilt", "orbit", "arc",
                "dolly", "tracking", "handheld", "whip", "zoom", "crane", "overhead",
                "环绕", "推进", "拉远", "横摇", "竖摇", "跟拍", "手持", "快速平移", "固定机位", "俯拍")

# 安全区指令模板（与 motion_design.SAFE_ZONES 对齐）
_SAFE_ZONE_HINTS = {
    "lower_third": "Keep the lower 25% of the frame clear of key action, faces, and product details; this space is reserved for subtitle overlay.",
    "upper_third": "Keep the upper 20% of the frame clear of key action and faces; this space is reserved for title overlay.",
    "center": "Keep the center of the frame clear for a title reveal; position key subjects slightly off-center.",
    "corner": "Keep the upper-right corner clear; a small data card will appear there.",
    "left": "Keep the left 30% of the frame clear; text content will appear on the left side.",
    "right": "Keep the right 30% of the frame clear; text content will appear on the right side.",
}


def is_seedance_model(model):
    return str(model or "").lower() in SEEDANCE_MODELS


def _clean(value):
    return re.sub(r"\s+", " ", str("" if value is None else value)).strip()


def _motion(value):
    text = _clean(value)
    if not text:
        return "固定机位"
    for term in MOTION_TERMS:
        if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(term.lower()), text.lower()):
            return term
    return text


def _reference_clause(ref, index):
    ref_type = ref.get("type") or ref.get("reference_type")
    clauses = {
        "character_identity": "只锁定脸型、五官、发际线、发型、年龄、服装、配饰和身体比例；不要复制参考图背景、姿势、构图或光线",
        "product_identity": "严格保持产品结构、颜色、材质、比例、零件位置和原生标识；不得添加、删除、替换或重新设计产品，不复制背景和构图",
        "scene_environment": "只继承空间布局、布景材质、光线方向和色彩氛围；不要复制图中偶然出现的人物、产品、文字或姿势",
        "storyboard_composition": "只读取镜头构图、机位、景别、主体位置、动作顺序和摄像机运动；绝不继承黑白、素描、网格、箭头、文字、标注或面板边框",
        "continuation_frame": "只作为本段连续性起点，继承人物位置、动作状态、镜头方向和光线；不得当作整段固定构图或重置场景",
    }
    if ref_type in clauses:
        line = "素材%d（%s）：%s。" % (index, ref.get("label", "参考图"), clauses[ref_type])
        scope = ref.get("scope")
        if scope == "beat":
            line += " 此素材仅适用于指定镜头时间窗，不得扩大到其他镜头。"
        elif scope == "scene":
            line += " 此约束适用于本场景。"
        elif scope == "clip":
            line += " 此约束仅适用于本次生成片段。"
        return line
    return "素材%d（%s）：%s，%s。" % (
        index, ref.get("label", "参考图"), ref.get("role", "视觉锚定"),
        ref.get("intent", "保持一致"))


def compile_prompt(segment, refs=None, *, style=None, negative=None, target_model="seedance-2.0"):
    """Build a structured timeline prompt for Seedance or its Kling fallback.

    The prompt intentionally uses timestamped beats, as Seedance responds more
    reliably to a short, concrete timeline than to a long prose paragraph.
    """
    timeline = segment.get("timeline") or []
    if not timeline:
        duration = segment.get("duration") or 5
        timeline = [{"start": 0, "end": duration,
                     "action": segment.get("action") or segment.get("visual") or segment.get("text"),
                     "camera": segment.get("camera_movement") or segment.get("camera"),
                     "sound": segment.get("sound") or segment.get("sfx")}]
    duration = segment.get("duration") or 5
    target = "Seedance 2.0" if is_seedance_model(target_model) else "Kling"
    lines = [
        "%s 视频提示词：精准生成 %s 秒，画幅%s，电影级连续动作；不要静帧拼贴，不要把参考故事板整张图做平移缩放。"
        % (target, _clean(duration), _clean(segment.get("ratio") or "")),
        "成片媒介与风格：真实摄影、写实商业广告视频；故事板只是黑白素描预演，用于构图和动作顺序参考。绝不输出素描、铅笔画、炭笔画、黑白绘画、插画、动漫或故事板格子画面。",
        "导演标注颜色系统仅供读取，不得画入成片：红色箭头=身体运动；蓝色箭头=摄像机运动；绿色标记=取景/构图笔记；橙色标记=灯光方向；紫色标记=声音/情感强调；黑色文本=简短镜头笔记和面板标签。请把这些标注转译为真实动作、机位、构图、灯光、声音和情绪，不要显示任何箭头、彩色线条、标记、黑色文字或面板标签。",
    ]
    # ── 连续性锁定（修复断层 + 服装漂移 + 音画不同步）──
    continuity_lines = _build_continuity_lock(segment)
    if continuity_lines:
        lines.extend(continuity_lines)
    else:
        lines.append(
            "一致性锁定：人物脸部、发型、服装和体态保持完全一致；产品外观、颜色、材质、比例、结构和标识细节保持完全一致；场景光线和空间关系保持连续。")
    for item in timeline:
        action = _clean(item.get("action") or item.get("visual") or item.get("text") or "主体自然运动")
        line = "%s-%s秒：%s。镜头%s。" % (
            _clean(item.get("start", 0)), _clean(item.get("end", segment.get("duration", 5))),
            action, _motion(item.get("camera") or item.get("movement")))
        if item.get("sound") or item.get("sfx"):
            line += "声音/情绪：%s。" % _clean(item.get("sound") or item.get("sfx"))
        details = []
        for key, label in (("shot_size", "景别"), ("angle_offset", "角度"),
                           ("composition", "构图"), ("lighting", "灯光"),
                           ("character_action", "人物动作"),
                           ("micro_expression", "微表情"),
                           ("scene_prompt", "场景"), ("prop_prompts", "道具/产品")):
            value = item.get(key)
            if isinstance(value, list):
                value = "；".join(_clean(v) for v in value)
            if value:
                details.append("%s：%s" % (label, _clean(value)))
        if details:
            line += " " + "；".join(details) + "。"
        lines.append(line)
    refs = refs or segment.get("references") or segment.get("reference_roles") or []
    for i, ref in enumerate(refs, 1):
        if isinstance(ref, dict):
            lines.append(_reference_clause(ref, i))
    contract = segment.get("clip_contract") or {}
    buckets = contract.get("scopes") or contract.get("scope") or {}
    if buckets:
        lines.append("作用域规则：镜头/beat级动作、构图、产品和道具要求只适用于其标注时间窗；不得将单个镜头的姿势、道具或机位扩大到整段视频。")
    scope = contract.get("scope") or {}
    if scope.get("already_happened"):
        lines.append("已经完成、不得重演：%s。" % "；".join(map(_clean, scope["already_happened"])))
    if scope.get("this_clip_only"):
        lines.append("本段只完成：%s。" % "；".join(map(_clean, scope["this_clip_only"])))
    if scope.get("reserved_for_later"):
        lines.append("留待后续、本段禁止提前出现：%s。" % "；".join(map(_clean, scope["reserved_for_later"])))
    if scope.get("endpoint"):
        lines.append("本段结束状态：%s。" % _clean(scope["endpoint"]))
    clip_scope = buckets.get("clip") or {}
    director_parts = []
    for key, label in (("narrative_function", "叙事功能"), ("felt_intent", "观众感受目标"),
                       ("director_voice", "导演语气"), ("arc_position", "叙事弧位置")):
        if clip_scope.get(key):
            director_parts.append("%s：%s" % (label, _clean(clip_scope[key])))
    if director_parts:
        lines.append("导演意图：%s。所有运镜、灯光、表演和声音必须服务同一个意图，避免空泛电影感。" % "；".join(director_parts))
    if clip_scope.get("is_pattern_break"):
        lines.append("本镜是有意的节奏破格点：只在本镜改变节奏或构图，不改变人物、产品和品牌世界的一致性。")
    if segment.get("product_identity_lock"):
        lines.append("产品一致性锁：只允许展示参考产品本身，不添加、替换或重设计产品零件、材质、颜色和比例。")
    if segment.get("character_identity_lock"):
        lines.append("人物一致性锁：只允许使用参考人物本身，不改变脸型、发型、服装、年龄和身体比例。")
    if segment.get("continuity_in"):
        lines.append("本段开头衔接：%s。" % _clean(segment["continuity_in"]))
    if segment.get("continuity_out"):
        lines.append("本段结尾衔接点：%s。下一段必须从该状态自然延续。" % _clean(segment["continuity_out"]))
    if segment.get("extend_video"):
        lines.insert(1, "这是上一段视频的延长：从上传视频最后画面自然继续，不重置人物、场景、镜头方向或光线。")
    if segment.get("dialogue"):
        lines.append("台词/旁白：%s。" % _clean(segment["dialogue"]))
    audio = segment.get("audio_contract") or {}
    if audio:
        lines.append(
            "音频执行契约：台词=%s；语言/口音=%s；声音人设=%s；背景音乐=%s；音效=%s；口型同步=%s。"
            "这些是实际音画生成要求，不是备注；不得省略、替换语言或擅自改变声音人设。" % (
                _clean(audio.get("dialogue") or "无"), _clean(audio.get("language") or "无指定"),
                _clean(audio.get("voice") or "无指定"), _clean(audio.get("bgm") or "无"),
                _clean(audio.get("sfx") or "无"), "必须" if audio.get("lip_sync") else "不要求"))
    render_plan = (segment.get("render_plan") or {}).get("content") or {}
    if render_plan:
        lines.append("已确认渲染方案（必须实际执行）：%s。" % _clean(
            __import__("json").dumps(render_plan, ensure_ascii=False, sort_keys=True)))
    if style:
        lines.append("参考风格描述（仅用于商业质感与色彩，不得改变写实摄影媒介）：%s。" % _clean(style))
    lines.append("最终成片锁定：photorealistic live-action realistic commercial footage，真实彩色摄影质感；"
                 "不得继承故事板的黑白、素描、铅笔或炭笔媒介。")
    if negative:
        lines.append("避免：%s。" % _clean(negative))
    return "\n".join(lines)


def _build_continuity_lock(segment):
    """Build enhanced continuity lock lines based on segment context.

    Addresses three production quality issues:
      1. Segment discontinuity (断层) — tail-frame chaining + visual state lock
      2. Clothing inconsistency (服装漂移) — explicit clothing description lock
      3. Voice/lip-sync drift (音画不同步) — voice pacing + duration constraint
    """
    lines = []

    # ── 服装锁定（修复人物服装不一致）──
    clothing = segment.get("clothing_lock") or segment.get("clothing_description")
    if clothing:
        lines.append(
            "服装严格锁定（最高优先级）：%s。不允许更换、添加、删除或改变任何服装元素；"
            "不允许改变颜色、材质、纹理、图案或配饰。每一帧的服装都必须与参考图完全一致。"
            % _clean(clothing))
    elif segment.get("character_identity_lock"):
        lines.append(
            "服装严格锁定：人物必须穿着与参考图完全相同的服装、配饰和鞋子；"
            "不允许更换颜色、款式、材质或添加/删除任何服装元素。")

    # ── 尾帧串联连续性（修复片段衔接断层）──
    if segment.get("_chain_tail_frame") or segment.get("extend_video"):
        lines.append(
            "段间连续性锁定（关键）：本段从上一段的最后一帧无缝继续。第一帧必须与参考图"
            "（上一段尾帧）在人物位置、姿势、表情、服装、场景、光线、色调、镜头角度上完全一致。"
            "不允许跳变、重置场景、改变人物位置或改变光线方向。")
        if segment.get("continuity_in"):
            lines.append(
                "上一段结束状态：%s。本段必须从这个精确状态自然延续。" % _clean(segment["continuity_in"]))

    # ── 口播音画同步锁定（修复音画不同步 + 长口播嘴形配音不一致）──
    audio = segment.get("audio_contract") or {}
    dialogue = audio.get("dialogue") or segment.get("dialogue") or ""
    duration = segment.get("duration") or 5
    if dialogue and segment.get("oral_broadcast"):
        lines.append(
            "口播音画同步锁定：台词必须在 %s 秒内以自然语速念完；不允许加速或减速。"
            "嘴唇动作必须与发音精确同步，从第一个字到最后一个字。不允许提前闭嘴或延迟开口。"
            "配音音色、音调、语速、情感必须与上一段完全一致，如同同一个人连续说话。"
            % _clean(duration))
        if segment.get("extend_video"):
            lines.append(
                "音频连续性：这是上一段口播的自然延续。声音特质（音色、音调、语速、口音、情感）"
                "必须与上一段完全一致，听众不应察觉到段落切换。第一句话紧接上一段最后一句的语气。")
    elif dialogue:
        lines.append(
            "音画同步：台词必须在 %s 秒内以自然语速念完，嘴唇动作与发音精确同步。"
            % _clean(duration))

    # ── 画面安全区（为后期字幕/动效预留空间）──
    safe_zones = segment.get("video_safe_zones") or []
    motion_design = segment.get("motion_design") or {}
    if not safe_zones and motion_design:
        safe_zones = motion_design.get("video_safe_zones") or []
    if safe_zones:
        hints = [_SAFE_ZONE_HINTS[z] for z in safe_zones if z in _SAFE_ZONE_HINTS]
        if hints:
            lines.append(
                "画面构图安全区（重要）：%s 这些区域不要放置关键动作、人脸或产品细节，"
                "后期会在此区域叠加文字/动效。" % " ".join(hints))

    return lines


def audit_segments(segments):
    issues = []
    previous = None
    for index, segment in enumerate(segments or [], 1):
        if not (segment.get("urls") or segment.get("refs") or segment.get("allow_text2video")):
            issues.append({"index": index, "severity": "error", "code": "MISSING_REFERENCE"})
        if not (segment.get("camera") or segment.get("camera_movement") or segment.get("timeline")):
            issues.append({"index": index, "severity": "warning", "code": "MISSING_MOTION"})
        if len(segment.get("timeline") or []) > 4:
            issues.append({"index": index, "severity": "warning", "code": "TOO_MANY_BEATS"})
        references = segment.get("references")
        if references is not None and len(segment.get("urls") or []) != len(references):
            issues.append({"index": index, "severity": "error", "code": "REFERENCE_COUNT_MISMATCH"})
        if (previous and segment.get("extend_video") and previous.get("scene_id")
                and segment.get("scene_id") and previous.get("scene_id") != segment.get("scene_id")):
            issues.append({"index": index, "severity": "error", "code": "CROSS_SCENE_EXTENSION"})
        for beat in segment.get("timeline") or []:
            if float(beat.get("start", 0)) < 0 or float(beat.get("end", 0)) > float(segment.get("duration") or 0):
                issues.append({"index": index, "severity": "error", "code": "BEAT_OUT_OF_RANGE"})
        previous = segment
    return {"ok": not any(i["severity"] == "error" for i in issues), "issues": issues}
