#!/usr/bin/env python3
"""阶段4 · 剧本按时长拆分 → 底片段 segments（喂给 video_engine.render_batch）。

定位（修正后的正确顺序）：
  阶段1 素材补充（六视图在脚本之前就绪） → 阶段2 脚本共创 → 阶段3 分镜图生成
  → **阶段4（本脚本）** 把定稿 storyboard_plan.json 按各镜时长拆成多段，
     每段 = 分镜图(storyboard shot image) + 锚定素材(asset_refs) + 该段台词(dialogue)
  → video_engine.render_batch 并行出「底片段」 → 多段则 ffmpeg+HyperFrames 合成最终底片
  → 阶段5 第一轮客户确认+下载 → 阶段6 逆向工程 → 阶段7 Remotion 剪辑成片

与 guide_scaffold.compile-segments 的区别：
  guide_scaffold 从「引导表」编译；本脚本从「定稿 storyboard_plan.json + 已生成的分镜图」
  编译，把每个 shot 的分镜图作为 video_type=4/5 的锚定参考图，保证成片贴合已确认的分镜。

storyboard_plan.json（阶段3 定稿，script-cocreation ⑦ 产出）关键字段：
  { "aspect_ratio":"9:16", "asset_refs":{...},
    "shots":[ {"id":"s1","duration":3,"dialogue":"...","visual":"...",
               "camera":"...","characters":["host"],"asset_refs":{...}} ] }

分镜图约定：阶段3 storyboard.py 输出 output/storyboard/shot_<id>.jpg（或 .png）。
本脚本按 shot id 自动匹配分镜图目录里的 shot_<id>.* 作为该段首帧锚定图。

CLI:
  split --plan output/storyboard_plan.json --storyboard-dir output/storyboard \
        --out output/segments.json [--fps 30] [--min-seconds 3]
  assemble --segments output/segments.json --results output/batch_results.json \
        --out output/basecut.mp4          # 多段底片 → ffmpeg 拼接为最终版底片
"""
import os
import re
import sys
import json
import argparse
import shutil
import subprocess
import hashlib
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from aspect_ratio import output_ratio  # noqa: E402
import run_manifest as rm  # noqa: E402
import take_review  # noqa: E402
from video_segmentation import partition_shots, SEEDANCE_MAX_SECONDS, max_seconds_for_model
from artifact_contract import build_video_handoff
import subtitle_asr  # noqa: E402

# aspect_ratio → (ratio_str, default resolution)。竖屏优先（短视频主场景）。
_RATIO_MAP = {
    "9:16": ("9:16", "1080p"),
    "16:9": ("16:9", "1080p"),
    "1:1": ("1:1", "1080p"),
}


def _load_storyboard_result(storyboard_dir):
    path = os.path.join(storyboard_dir, "storyboard_result.json")
    try:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, ValueError, TypeError):
        return path, None
    return path, result if isinstance(result, dict) else None


def _storyboard_shot_map(result):
    """Build the only supported shot-image map from explicit result shot IDs.
    Keys are normalized via _normalize_shot_id for flexible matching."""
    mapping = {}
    for item in (result or {}).get("shots") or []:
        raw_sid = str((item.get("shot") or {}).get("id") or "").strip()
        sid = _normalize_shot_id(raw_sid) or raw_sid
        path = item.get("abspath") or item.get("path")
        if not sid or not path or sid in mapping:
            raise ValueError("INVALID_STORYBOARD_RESULT: shot id/path 缺失或重复")
        mapping[sid] = os.path.abspath(path)
    return mapping


def _normalize_shot_id(raw_id):
    """Normalize shot ID to canonical form for storyboard image matching.

    Handles common LLM-generated variants:
      "s1" / "s01" / "shot_1" / "shot1" / "镜头1" / "镜头一" / "1" → "s1"
    """
    if not raw_id:
        return ""
    s = str(raw_id).strip().lower()
    # Remove common prefixes
    for prefix in ("shot_", "shot", "scene_", "scene"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Chinese shot number
    cn_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
    if s.startswith("镜头"):
        suffix = s[2:]
        s = cn_map.get(suffix, suffix)
    # Strip leading zeros from numeric part
    if s.startswith("s") and len(s) > 1:
        num = s[1:]
        if num.isdigit():
            return "s" + str(int(num))
        return s
    if s.isdigit():
        return "s" + str(int(s))
    return s


def _find_shot_image(shot_map, shot_id, source_ids=None):
    """Resolve shot image by normalized ID; filenames and substrings are never identities."""
    candidates = [_normalize_shot_id(shot_id)]
    candidates.extend(_normalize_shot_id(v) for v in (source_ids or []))
    for candidate in dict.fromkeys(candidates):
        if candidate in (shot_map or {}):
            return shot_map[candidate]
    return None


def _storyboard_result_fingerprint(storyboard_dir):
    """Read the generated storyboard revision identity, when available."""
    if not storyboard_dir:
        return None
    path = os.path.join(storyboard_dir, "storyboard_result.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("plan_fingerprint")
    except (OSError, ValueError, TypeError):
        return None


def _collect_anchor_urls(shot, plan_refs, shot_image, bw_storyboard=True):
    """收集该段锚定素材 URL 列表。锚定顺序决定权重（越靠前权重越高）。

    bw_storyboard=True（默认，故事板是黑白）：**彩色素材优先**（人物板/产品图/场景图作
      主参考锚定颜色），黑白分镜图降为「构图确认」用，放最后作弱构图提示；避免黑白图当
      首帧锚导致出片补色不确定、掉色。
    bw_storyboard=False（故事板本身是彩色）：沿用旧语义，分镜图优先作首帧锚。
    """
    color_refs = []
    shot_refs = shot.get("asset_refs") or {}
    refs = dict(plan_refs or {})
    refs.update(shot_refs)  # shot 级覆盖 plan 级
    for key in ("digital_human_portraits", "product_images", "scene_images"):
        for u in (refs.get(key) or []):
            if u and u not in color_refs:
                color_refs.append(u)

    if bw_storyboard:
        # 彩色素材锚定颜色在前；黑白分镜图作构图提示垫后
        urls = list(color_refs)
        if shot_image and shot_image not in urls:
            urls.append(shot_image)
    else:
        # 彩色故事板：分镜图作首帧锚在前
        urls = []
        if shot_image:
            urls.append(shot_image)
        for u in color_refs:
            if u not in urls:
                urls.append(u)
    return urls[:4]  # kling 参考图上限 4 张


_REFERENCE_META = {
    "product_usage_images": (
        "product_usage_identity", "已确认产品使用细节图", "锁定人物与产品操作关系",
        "继承人物身份、产品外观、手部接触点和真实操作关系，不继承背景文字"),
    "cast_boards": (
        "character_board", "已确认人物六视图", "锁定人物身份与全身一致性",
        "继承脸部、发型、服装、配饰和身体比例，不继承板式布局"),
    "product_boards": (
        "product_board", "已确认产品九宫格板", "锁定产品多角度外观",
        "继承产品结构、材质、颜色、比例和原生标识，不继承板式布局"),
    "digital_human_portraits": (
        "character_identity", "人物身份参考", "锁定人物身份",
        "只继承脸部、发型、服装、配饰和身体比例，不继承背景、姿势或构图"),
    "product_images": (
        "product_identity", "产品外观参考", "锁定产品身份",
        "只继承结构、材质、颜色、比例和原生标识，不继承背景或构图"),
    "scene_images": (
        "scene_environment", "场景环境参考", "锁定空间与灯光",
        "只继承环境布局、材质和光线，不复制图中偶然出现的人物或文字"),
}


def _required_reference_types(_refs=None, has_human=False, has_product=False):
    """Return semantic reference types that cannot be silently dropped."""
    required = {"storyboard_composition"}
    if has_human:
        required.add("character_board")
    if has_product:
        required.add("product_board")
    if has_human and has_product:
        required.add("product_usage_identity")
    return required


def _collect_typed_references(shot, plan_refs, shot_image, bw_storyboard=True):
    """Return role-bound references while preserving the legacy URL order."""
    shot_refs = shot.get("asset_refs") or {}
    refs = dict(plan_refs or {})
    refs.update(shot_refs)
    by_url = {}
    color = []
    for key in ("product_usage_images", "cast_boards", "product_boards",
                "digital_human_portraits", "product_images", "scene_images"):
        ref_type, label, role, intent = _REFERENCE_META[key]
        for url in refs.get(key) or []:
            if not url or url in by_url:
                continue
            item = {"id": "ref_%02d" % (len(by_url) + 1), "url": url,
                    "type": ref_type, "scope": "scene", "label": label,
                    "role": role, "intent": intent, "source": "asset_refs.%s" % key}
            by_url[url] = item
            color.append(item)
    storyboard = None
    if shot_image:
        storyboard = {"id": "ref_%02d" % (len(by_url) + 1), "url": shot_image,
                      "type": "storyboard_composition", "scope": "beat",
                      "label": "分镜锚定图", "role": "构图与动作顺序参考",
                      "intent": "只继承构图、机位和动作顺序，不继承黑白素描媒介",
                      "source": "storyboard"}
    ordered = color + ([storyboard] if storyboard else []) if bw_storyboard else \
        ([storyboard] if storyboard else []) + color
    kept = ordered[:4]
    for index, item in enumerate(kept, 1):
        item["id"] = "ref_%02d" % index
        item["index"] = index
    dropped = [dict(item, reason="gateway_reference_limit") for item in ordered[4:]]
    return kept, dropped


def _legacy_reference_roles(references):
    return [{"label": ref["label"], "role": ref["role"], "intent": ref["intent"],
             "type": ref["type"], "scope": ref["scope"], "ref_id": ref["id"],
             "ref_index": index}
            for index, ref in enumerate(references or [], 1)]


def _build_clip_contract(plan, shot, references, ratio, style, duration):
    authored = shot.get("clip_contract") or {}
    scope = dict(authored.get("scope") or {})
    for key in ("already_happened", "this_clip_only", "reserved_for_later", "endpoint"):
        if key in shot and key not in scope:
            scope[key] = shot[key]
    beats = shot.get("timeline") or []
    return {
        "version": 1,
        "scope": scope,
        "scopes": {
            "global": {"ratio": ratio, "medium": "photorealistic live-action",
                       "style": style or ""},
            "scene": {"scene_id": shot.get("scene_id"),
                      "reference_ids": [r["id"] for r in references if r.get("scope") in ("global", "scene")]},
            "clip": {"clip_id": str(shot.get("id") or ""), "duration": duration,
                     "reference_ids": [r["id"] for r in references],
                     "continuation_mode": "fresh_scene",
                     "narrative_function": shot.get("narrative_function") or plan.get("narrative_function"),
                     "felt_intent": shot.get("felt_intent") or plan.get("felt_intent"),
                     "director_voice": shot.get("director_voice") or plan.get("director_voice"),
                     "arc_position": shot.get("arc_position") or plan.get("arc_position"),
                     "is_pattern_break": bool(shot.get("is_pattern_break"))},
            "beats": beats,
        },
    }


def _build_audio_contract(plan, shot, dialogue):
    """Normalize authored audio intent without asking downstream QC to infer it."""
    audio = dict(plan.get("audio") or {})
    audio.update(shot.get("audio") or {})
    dialogue = (dialogue or "").strip()
    speech = bool(dialogue)
    bgm, sfx = audio.get("bgm"), audio.get("sfx")
    track = "required" if speech or bgm or sfx else "none"
    return {
        "track": track,
        "speech": speech,
        "dialogue": dialogue,
        "language": audio.get("language") or shot.get("language") or plan.get("language"),
        "voice": audio.get("voice") or audio.get("voice_type") or shot.get("voice_type") or plan.get("voice_type"),
        "bgm": bgm,
        "sfx": sfx,
        "lip_sync": bool(audio.get("lip_sync", speech and bool(shot.get("characters")))),
    }


def _pick_video_type(n_urls, has_human, has_environment=False):
    """按锚定图数量与是否含数字人自动选 videoType。

    videoType 语义（与网关 allowVideoType 对齐）：
      1=文生 2=单图首帧 3=首尾帧 4=单张参考图(仅 kling) 5=多图/多主体(含 seedance)
    选型：
      - 无图 → 1
      - 含数字人 + 多图（人物板 + 场景图/产品图，人景同框/多主体）→ 5
        （多图多主体 seedance-2.0 自身支持，_pick_video_model 会保持 seedance，更快更省）
       - 含产品/场景环境素材 → 5（即使当前只有一张环境图，也预留多主体/人景一致性能力）
       - 含数字人 + 单图（仅一张人物锚定图）→ 4（单张身份锚定，仅 kling，会自动回落 kling）
      - 无数字人 + 多图 → 5；无数字人 + 单图 → 2
    """
    if n_urls == 0:
        return 1
    if has_environment:
        return 5
    if n_urls >= 2:
        return 5   # 多图：人景同框/多主体/多方位——seedance 也支持，不强制 kling
    if has_human:
        return 4   # 单张数字人参考图身份锚定：仅 kling
    return 2       # 单图生视频首帧


# voice_type → 中文口播语速（字/秒）。基于实际 AIGC 视频生成模型的语音合成实测数据。
# 慢速：情感旁白/纪录片；标准：一般口播；快速：促销/快节奏带货。
_VOICE_SPEECH_RATES = {
    "slow": 3.2,       # 慢速旁白、情感类
    "calm": 3.5,       # 沉稳、纪录片
    "narrator": 3.8,   # 旁白解说
    "standard": 4.2,   # 标准口播（默认）
    "professional": 4.5,  # 专业播报
    "energetic": 5.0,  # 活力带货
    "fast": 5.5,       # 快节奏促销
    "promo": 5.8,      # 极速促销
}
_DEFAULT_SPEECH_RATE = 4.2  # 无 voice_type 时的默认语速


def _speech_rate_for_shot(shot, plan_voice_type=None):
    """Determine speech rate (chars/sec) for a shot based on voice_type context."""
    audio = shot.get("audio_contract") or {}
    voice = (audio.get("voice") or shot.get("voice_type")
             or plan_voice_type or "standard").lower()
    for key, rate in _VOICE_SPEECH_RATES.items():
        if key in voice:
            return rate
    return _DEFAULT_SPEECH_RATE


def _shot_duration_seconds(shot, min_seconds, speech_rate=None):
    """取该镜时长（秒）。优先 shot.duration；缺失则按台词字数估（按 voice_type 调速）。"""
    d = shot.get("duration") or shot.get("seconds")
    if isinstance(d, (int, float)) and d > 0:
        return max(min_seconds, int(round(d)))
    dialogue = shot.get("dialogue") or shot.get("voiceover") or ""
    rate = speech_rate or _speech_rate_for_shot(shot)
    est = len(re.sub(r"\s", "", dialogue)) / rate if dialogue else min_seconds
    return max(min_seconds, int(round(est)))


def _validate_dialogue_fit(shot, duration, speech_rate=None):
    """Check if dialogue fits within the allocated duration.

    Returns (fits, expected_seconds, message). When dialogue is too long,
    the model will either rush the speech (causing lip-sync issues) or
    truncate the dialogue (causing missing content).
    """
    dialogue = shot.get("dialogue") or shot.get("voiceover") or ""
    if not dialogue:
        return True, 0, None
    rate = speech_rate or _speech_rate_for_shot(shot)
    chars = len(re.sub(r"\s", "", dialogue))
    expected = chars / rate
    # Allow 10% tolerance — model can slightly adjust pacing
    fits = expected <= duration * 1.10
    if not fits:
        return False, round(expected, 1), (
            "台词时长(%.1fs)超出镜头分配时长(%ds)的10%%容差。"
            "预计需要%.1fs但只分配了%ds，模型会被迫加速念词导致嘴形配音不同步。"
            "建议：①缩减台词至%d字以内 ②增加该镜头时长至%ds ③拆分为两个镜头"
            % (expected, duration, expected, duration,
               int(duration * rate * 0.95), int(expected) + 1))
    return True, round(expected, 1), None


def _check_scene_consistency(shots):
    """Warn when shots sharing a scene_id have divergent scene_prompt descriptions.

    This is a soft check (print warning, not raise) to avoid blocking valid
    creative choices while surfacing likely authoring mistakes.
    """
    from difflib import SequenceMatcher
    scenes = {}
    for shot in shots:
        sid = str(shot.get("scene_id") or "").strip()
        prompt = str(shot.get("scene_prompt") or "").strip()
        if not sid or not prompt:
            continue
        scenes.setdefault(sid, []).append((shot.get("id"), prompt))
    for sid, entries in scenes.items():
        if len(entries) < 2:
            continue
        base_id, base_prompt = entries[0]
        for other_id, other_prompt in entries[1:]:
            ratio = SequenceMatcher(None, base_prompt, other_prompt).ratio()
            if ratio < 0.3:
                print("[scene-check] WARNING: scene_id=%s 的 shots %s 和 %s "
                      "scene_prompt 差异很大（相似度 %.0f%%），请确认是否为同一场景"
                      % (sid, base_id, other_id, ratio * 100), flush=True)


def _extract_clothing_lock(plan, shot):
    """Extract clothing description from character metadata for identity lock.

    Sources (in priority order):
      1. shot.character.clothing / shot.clothing
      2. plan.characters[].clothing (matched by shot.characters[0])
      3. plan.cast_description / plan.character_description
    Returns a clothing description string, or None if not available.
    """
    # Shot-level clothing
    char = shot.get("character") or {}
    if isinstance(char, dict) and char.get("clothing"):
        return str(char["clothing"]).strip()
    if shot.get("clothing"):
        return str(shot["clothing"]).strip()

    # Plan-level characters
    shot_chars = shot.get("characters") or []
    plan_chars = plan.get("characters") or []
    if shot_chars and plan_chars:
        # Match by character name/id
        target = str(shot_chars[0]).strip().lower()
        for pc in plan_chars:
            if not isinstance(pc, dict):
                continue
            pc_name = str(pc.get("name") or pc.get("id") or "").strip().lower()
            if pc_name == target or pc_name in target or target in pc_name:
                clothing = pc.get("clothing") or pc.get("outfit") or pc.get("costume")
                if clothing:
                    return str(clothing).strip()

    # Fallback: plan-level description
    for key in ("cast_description", "character_description", "actor_description"):
        desc = plan.get(key)
        if desc and isinstance(desc, str):
            # Try to extract clothing from description
            import re as _re
            m = _re.search(r"穿[着戴]?([^，。；;]+)", desc)
            if m:
                return m.group(1).strip()
    return None


def _inject_scene_transitions(shots):
    """Add transition hints at scene boundaries so the model generates a
    smooth visual transition instead of a hard cut between scenes."""
    prev_scene = None
    for shot in shots:
        scene_id = str(shot.get("scene_id") or "").strip() or None
        if prev_scene is not None and scene_id != prev_scene:
            # Scene boundary detected — inject transition hint
            hint = "场景过渡：从上一场自然转场到当前场景"
            existing = shot.get("continuity_in") or ""
            if hint not in existing:
                shot["continuity_in"] = (existing + "；" + hint).strip("；") if existing else hint
        prev_scene = scene_id


def split(plan, storyboard_dir=None, fps=30, min_seconds=3, bw_storyboard=None,
          allow_text2video=False, client=None, allow_unconfirmed=False,
          ratio_override=None, draft_allow_unapproved_storyboard=False,
          run_id=None, render_plan_artifact=None):
    """把定稿 storyboard_plan.json 按各镜时长拆成 render_batch segments。

    每段 segment：
      {id, text(台词+表演指导), duration(秒), video_type, urls(分镜图+锚定素材),
       ratio, resolution, out_path, storyboard_ref:True}
    返回 {segments:[...], total_seconds, missing_images:[shot_id...],
         needs_image:[shot_id...], unconfirmed_refs:[{shot_id,url}...],
         ratio, resolution}。

    bw_storyboard：故事板是否黑白（决定锚定优先级）。None=自动读 plan.color_mode
      （默认黑白）；黑白时彩色素材优先锚定颜色，黑白分镜图垫后作构图提示。

    allow_text2video（铁律#11，与 guide_scaffold.compile_segments 的政策保持一致）：
      默认 False——若某镜锚定素材为 0（既无分镜图也无 asset_refs），该镜**不生成
      segment**，其 id 记入返回值 needs_image，交由调用方回填素材后重新 split，
      避免"缺图→静默退化为纯文本生视频"（数字人/产品一致性会失控）。
      仅当明确是纯数字人口播、无需产品图/场景图时，才应传 True 显式放行，
      此时缺图镜头会正常走 video_type=1（文生视频）。

    client + allow_unconfirmed（confirmed/pending 状态机跨模块执行，asset_prep.py
      is_confirmed()）：asset_prep.gen_image() 产出的候选图默认 status='pending'，
      只有客户 confirm_image() 之后才是 status='confirmed'。这个状态机原本只是
      brief.json 里的约定，没有任何代码路径校验——本函数若传入 client，会对每段
      urls 里来自 asset_prep 的本地路径素材逐一跑 is_confirmed() 检查；命中未确认
      (pending) 素材时，默认（allow_unconfirmed=False）直接抛 ValueError 列出问题
      shot/url，交由调用方先让客户走确认闸门，不静默把未过客户确认的候选图当成
      最终锚定素材出片。client=None（未传）时跳过这项检查（无法判断 brief 归属）。
      仅当明确要用未确认素材做快速预览/草稿时，才应传 allow_unconfirmed=True。
    """
    if not client:
        raise ValueError("CLIENT_REQUIRED: split 正式流程必须显式指定 client")
    import storyboard
    plan = storyboard.expand_product_sku_refs(storyboard.canonical_storyboard_plan(plan))
    scene_type = str(
        plan.get("scene_type") or plan.get("sceneType") or plan.get("scenario") or plan.get("workflow") or
        plan.get("skill") or plan.get("command") or ""
    ).strip().lower().replace("_", "-")
    oral_broadcast = scene_type in {
        "oral-broadcast", "oralbroadcast", "broadcast", "口播", "普通口播"
    }
    if oral_broadcast:
        authored_shots = plan.get("shots") or []
        if len(authored_shots) < 2:
            raise ValueError("ORAL_BROADCAST_SHOTS_REQUIRED: 口播延长链至少需要两个独立镜头")
        total_authored = sum(_shot_duration_seconds(shot, min_seconds)
                             for shot in authored_shots)
        if total_authored > SEEDANCE_MAX_SECONDS * len(authored_shots):
            raise ValueError("ORAL_BROADCAST_DURATION_INVALID: 每个口播镜头必须不超过15秒")
    # Video extension is an explicit business policy, not a generic same-scene
    # continuity shortcut. Only the single-speaker oral-broadcast workflow may
    # submit a provider extension request; every other workflow must use fresh
    # segment generation and local assembly.
    if plan.get("client") and str(plan["client"]) != str(client):
        raise ValueError("CLIENT_MISMATCH: plan.client 与 split client 不一致")
    plan["client"] = client
    render_plan_artifact = render_plan_artifact or plan.get("render_plan_artifact")
    if render_plan_artifact:
        path = render_plan_artifact.get("path")
        if not path or not os.path.isfile(path):
            raise ValueError("RENDER_PLAN_ARTIFACT_REQUIRED")
        with open(path, "rb") as handle:
            actual_sha = hashlib.sha256(handle.read()).hexdigest()
        if actual_sha != render_plan_artifact.get("sha256"):
            raise ValueError("STALE_RENDER_PLAN_ARTIFACT")
        with open(path, encoding="utf-8") as handle:
            render_plan = json.load(handle)
        render_plan_identity = {"path": os.path.abspath(path), "sha256": actual_sha,
                                "content": render_plan}
    else:
        render_plan = plan.get("render_plan") or {}
        render_plan_identity = {"path": None, "sha256": _sha256_json(render_plan),
                                "content": render_plan}

    # Storyboard contact sheets are commonly 16:9 even when the final video is
    # vertical. Keep those concerns independent with an explicit override.
    aspect = ratio_override or output_ratio(plan)
    ratio, resolution = _RATIO_MAP.get(aspect, ("9:16", "1080p"))
    plan_refs = plan.get("asset_refs") or {}
    # Seedance accepts at most 15 seconds per generation. Group the approved
    # shots into the same <=15s units that the storyboard stage previews.
    source_shots = plan.get("shots") or []
    scene_aware = any(str(shot.get("scene_id") or "").strip() for shot in source_shots)

    # 场景一致性校验：同 scene_id 的 shots 应有相近的 scene_prompt
    if scene_aware:
        _check_scene_consistency(source_shots)

    shots = partition_shots(source_shots, max_seconds=SEEDANCE_MAX_SECONDS,
                            scene_aware="auto", preserve_shots=oral_broadcast)
    if not shots:
        raise ValueError("storyboard_plan 无 shots，无法拆分")

    # 跨场景过渡指令：scene_id 变化时在第一个 shot 注入过渡提示
    if scene_aware:
        _inject_scene_transitions(shots)

    if bw_storyboard is None:
        cm = str(plan.get("color_mode") or "bw").lower()
        bw_storyboard = cm in ("bw", "black_white", "grayscale", "mono", "黑白")

    # All generated clips after the first continue from the previous clip.
    # The renderer uses this marker to submit Seedance's video-extension call.
    total_plan_seconds = sum(
        float(shot.get("duration") or shot.get("seconds") or min_seconds)
        for shot in (plan.get("shots") or [])
    )
    long_video = total_plan_seconds > SEEDANCE_MAX_SECONDS

    import asset_prep as asset_prep

    storyboard_dir = os.path.abspath(storyboard_dir) if storyboard_dir else None
    plan_fingerprint = storyboard.plan_fingerprint(plan)
    result_path, storyboard_result, shot_map = None, None, {}
    if storyboard_dir:
        if not os.path.isdir(storyboard_dir):
            raise ValueError("STORYBOARD_DIR_NOT_FOUND: %s" % storyboard_dir)
        result_path, storyboard_result = _load_storyboard_result(storyboard_dir)
        if not storyboard_result and not draft_allow_unapproved_storyboard:
            raise ValueError("STORYBOARD_APPROVAL_REQUIRED: storyboard_result.json 缺失或无效")
        if storyboard_result:
            if storyboard_result.get("client") != client:
                raise ValueError("CLIENT_MISMATCH: storyboard 不属于当前 client")
            result_run_id = storyboard_result.get("run_id")
            if run_id is not None and str(run_id) != str(result_run_id):
                raise ValueError("RUN_ID_MISMATCH: storyboard 不属于当前 run")
            run_id = result_run_id
            if storyboard_result.get("plan_fingerprint") != plan_fingerprint:
                raise ValueError("STALE_STORYBOARD: storyboard plan fingerprint 已过期")
            if (not draft_allow_unapproved_storyboard and
                    not storyboard.storyboard_approval_is_current(
                        result_path, client=client, run_id=run_id,
                        out_dir=storyboard_dir,
                        plan_fingerprint_value=plan_fingerprint)):
                raise ValueError("STORYBOARD_APPROVAL_REQUIRED: 故事板未确认或确认已失效")
            shot_map = _storyboard_shot_map(storyboard_result)
            generated_refs = dict(plan_refs)
            # Replace raw source references with the confirmed generated board
            # for each stage. Keeping both duplicates the same semantic anchor
            # and can evict the usage/storyboard refs.
            for result_key, ref_key, approval_kind in (
                    ("product_usage_image", "product_usage_images", "usage"),
                    ("cast_board", "cast_boards", "cast"),
                    ("product_board", "product_boards", "product")):
                item = storyboard_result.get(result_key) or {}
                path = item.get("abspath") or item.get("path")
                source_fp = item.get("source_fingerprint")
                if (path and source_fp and item.get("status") == "confirmed" and
                        storyboard._approval_current(storyboard_dir, approval_kind, source_fp)):
                    if ref_key == "product_usage_images":
                        generated_refs.pop("usage_reference_images", None)
                    if ref_key == "cast_boards":
                        generated_refs.pop("digital_human_portraits", None)
                    if ref_key == "product_boards":
                        generated_refs.pop("product_images", None)
                    generated_refs[ref_key] = [os.path.abspath(path)]
            plan_refs = generated_refs
    run_id = str(run_id or plan.get("run_id") or plan_fingerprint)
    approval_identity = {
        "client": client, "run_id": run_id,
        "plan_fingerprint": plan_fingerprint,
        "result_json": os.path.abspath(result_path) if result_path else None,
        "out_dir": storyboard_dir,
        "status": ("draft" if draft_allow_unapproved_storyboard else
                   "confirmed" if storyboard_result else "not_applicable"),
    }
    storyboard_fingerprint = (storyboard_result or {}).get("plan_fingerprint")
    segments, missing, needs_image, unconfirmed_refs, dropped_references = [], [], [], [], []
    total_seconds = 0
    for i, shot in enumerate(shots):
        sid = str(shot.get("id") or (i + 1))
        source_ids = shot.get("source_shot_ids") or [shot.get("source_shot_id") or sid]
        shot_image = _find_shot_image(shot_map, sid, source_ids)
        if storyboard_dir and not shot_image:
            missing.append(sid)
        # shot 级 color_mode 可覆盖 plan 级
        shot_bw = bw_storyboard
        if shot.get("color_mode"):
            shot_bw = str(shot["color_mode"]).lower() in ("bw", "black_white", "grayscale", "mono", "黑白")
        references, dropped = _collect_typed_references(
            shot, plan_refs, shot_image, bw_storyboard=shot_bw)
        urls = [ref["url"] for ref in references]
        dropped_references.extend(dict(item, shot_id=sid) for item in dropped)
        if not urls and not allow_text2video:
            # 铁律#11：零锚定素材 → 不静默降级为纯文本生视频，记入 needs_image 跳过
            needs_image.append(sid)
            continue

        if asset_prep is not None:
            # Storyboard approval is checked above as one content-bound unit;
            # asset_prep only owns source product/character/scene candidates.
            generated_sources = {
                "asset_refs.product_usage_images", "asset_refs.cast_boards",
                "asset_refs.product_boards",
            }
            asset_urls = [ref["url"] for ref in references
                          if ref.get("source") != "storyboard" and
                          ref.get("source") not in generated_sources]
            shot_unconfirmed = [u for u in asset_urls if not asset_prep.is_confirmed(client, u)]
            if shot_unconfirmed:
                unconfirmed_refs.extend({"shot_id": sid, "url": u} for u in shot_unconfirmed)
                if not allow_unconfirmed:
                    raise ValueError(
                        "UNCONFIRMED_ASSET: shot %s 引用了未经客户确认(status=pending)的"
                        "候选素材图 %r。这类图可能是两遍清洗里客户还没选定/可能被拒绝的"
                        "版本，不能静默当成最终锚定素材出片。请先让客户走 asset_prep.py "
                        "confirm-image 确认，或显式传 allow_unconfirmed=True 明确接受用"
                        "未确认素材（如仅做草稿预览）。" % (sid, shot_unconfirmed[0]))

        # 判定是否含数字人：shot.characters 非空 或 asset_refs 有数字人肖像
        merged_refs = dict(plan_refs)
        merged_refs.update(shot.get("asset_refs") or {})
        has_human = bool(shot.get("characters")) or bool(merged_refs.get("digital_human_portraits"))
        has_product = bool(merged_refs.get("product_images") or
                           merged_refs.get("product_boards") or
                           merged_refs.get("product_usage_images") or
                           shot.get("product_sku") or shot.get("product_refs"))
        has_scene = bool(merged_refs.get("scene_images") or
                         shot.get("scene") or shot.get("scene_prompt"))
        # has_environment 精细化：只有"多主体同框"或"人+环境"才算多主体场景。
        # 单产品静物（无人物无场景）不应触发多主体模式。
        # - 有人物 + 有环境 → 多主体（type 5）
        # - 有产品 + 有场景 → 多主体（type 5）
        # - 仅产品（无场景） → 单主体（type 2）
        # - 仅场景（无产品） → 单主体（type 2）
        env_element_count = sum([has_human, has_product, has_scene])
        has_environment = env_element_count >= 2
        required_types = _required_reference_types(
            has_human=has_human, has_product=has_product)
        dropped_required = [item for item in dropped if item.get("type") in required_types]
        if dropped_required and not draft_allow_unapproved_storyboard:
            raise ValueError(
                "REFERENCE_HANDOFF_INCOMPLETE: shot %s 的已确认参考图被网关上限丢弃：%s。"
                "请选择支持足够参考图数量的模型，或先生成一个包含全部必需身份/操作锚点的"
                "单一确认参考图；不得静默降级。" %
                (sid, ", ".join(item.get("label") or item.get("type")
                                for item in dropped_required)))
        vtype = _pick_video_type(len(urls), has_human, has_environment=has_environment)
        secs = _shot_duration_seconds(shot, min_seconds)
        total_seconds += secs

        # 台词时长 vs 镜头时长校验：防止模型被迫加速念词导致音画不同步
        dlg_fits, dlg_expected, dlg_msg = _validate_dialogue_fit(shot, secs)
        if not dlg_fits:
            print("[dialogue-fit] WARNING: shot %s — %s" % (sid, dlg_msg), flush=True)

        # 台词 + 表演指导 + 镜头语言，拼成音画一体出片 text
        parts = []
        if shot.get("dialogue") or shot.get("voiceover"):
            parts.append("【台词】" + (shot.get("dialogue") or shot.get("voiceover")))
        if shot.get("visual"):
            parts.append("【画面】" + shot["visual"])
        if shot.get("performance") or shot.get("action"):
            parts.append("【表演】" + (shot.get("performance") or shot.get("action")))
        if shot.get("camera"):
            parts.append("【镜头】" + shot["camera"])
        for key, label in (("shot_size", "景别"), ("angle_offset", "角度偏移"),
                           ("composition", "构图"), ("lighting", "灯光"),
                           ("character_action", "人物动作"),
                           ("micro_expression", "微表情"),
                           ("scene_prompt", "场景"), ("prop_prompts", "道具/产品")):
            value = shot.get(key)
            if isinstance(value, list):
                value = "；".join(str(v) for v in value)
            if value:
                parts.append("【%s】%s" % (label, value))
        text = "\n".join(parts) or (shot.get("visual") or "产品展示镜头")

        style = plan.get("visual_style") or (plan.get("render_profile") or {}).get("video_style_prompt", "")
        contract = _build_clip_contract(plan, shot, references, ratio, style, secs)
        # 服装锁定：从人物描述/角色板元数据提取服装细节
        clothing_lock = _extract_clothing_lock(plan, shot)

        dialogue = (shot.get("dialogue") or shot.get("voiceover") or "").strip()
        segment = {
            "id": sid,
            "client": client,
            "run_id": run_id,
            "storyboard_approval": approval_identity,
            "scene_id": shot.get("scene_id"),
            "source_shot_ids": shot.get("source_shot_ids") or [shot.get("source_shot_id") or sid],
            "text": text,
            "dialogue": dialogue,
            "audio_contract": _build_audio_contract(plan, shot, dialogue),
            "render_plan": render_plan_identity,
            "render_plan_fingerprint": _sha256_json(render_plan_identity),
            "duration": secs,
            "video_type": vtype,
            "urls": urls,
            "references": references,
            "required_reference_types": sorted(required_types),
            "dropped_references": dropped,
            "ratio": ratio,
            "resolution": resolution,
            "storyboard_ref": True,
            "storyboard_path": shot_image,
            "storyboard_dir": storyboard_dir,
            "storyboard_plan_fingerprint": plan_fingerprint,
            "storyboard_result_fingerprint": storyboard_fingerprint,
            "out_path": os.path.join("output", client, run_id, "seg_%s.mp4" % sid),
            "model": "kling-v3-omni-video" if oral_broadcast else None,
            "seedance_native": True,
            "extend_from_previous": bool(oral_broadcast and
                                          (shot.get("extend_from_previous") or shot.get("extend"))),
            "clothing_lock": clothing_lock,
            "character_identity_lock": bool(has_human),
            # 动效设计规范（motion_design.py 预规划）：安全区 + 字幕/动效 spec
            "video_safe_zones": shot.get("video_safe_zones") or [],
            "motion_design": shot.get("motion_design"),
            # Keep authored graphics out of the video prompt, but carry them
            # forward for the post-production HyperFrames stage.
            "motion_elements": list(shot.get("motion_elements") or [])
                if not isinstance(shot.get("motion_elements"), str)
                else [shot["motion_elements"]],
            "timeline": (shot.get("timeline") or [{
                "start": 0,
                "end": secs,
                "action": shot.get("visual") or shot.get("scene_prompt") or "主体自然运动",
                "camera": shot.get("camera_movement") or shot.get("camera"),
                "sound": (shot.get("audio") or {}).get("sfx"),
                "shot_size": shot.get("shot_size"),
                "angle_offset": shot.get("angle_offset"),
                "composition": shot.get("composition") or shot.get("layout"),
                "lighting": shot.get("lighting"),
                "character_action": shot.get("character_action") or shot.get("action"),
                "micro_expression": shot.get("micro_expression"),
                "scene_prompt": shot.get("scene_prompt") or shot.get("scene"),
                "prop_prompts": shot.get("prop_prompts") or shot.get("asset_prompts"),
            }]),
            "style": style,
            "reference_roles": _legacy_reference_roles(references),
            "clip_contract": contract,
            "take_review_required": bool(
                scene_aware or shot.get("clip_contract") or
                any(shot.get(field) for field in
                    ("narrative_function", "felt_intent", "director_voice", "arc_position"))),
            "sequence_state": shot.get("sequence_state") or {
                "version": 1, "sequence_id": shot.get("sequence_id") or shot.get("scene_id") or "default",
                "scene_id": shot.get("scene_id"), "entry": shot.get("planned_start_state") or {},
                "exit": shot.get("planned_end_state") or {}, "reset": False},
            "continuity_in": shot.get("continuity_in"),
            "continuity_out": shot.get("continuity_out"),
            "extend_video": bool(oral_broadcast and
                                  (shot.get("extend_video") or shot.get("extend_from_previous") or
                                   shot.get("extend"))),
            "oral_broadcast": oral_broadcast,
            "chain_contract": ({"predecessor_segment_id": str(shots[i - 1].get("id"))}
                               if i > 0 else {}),
        }
        # Rebuild contract beats from the normalized segment timeline.
        segment["clip_contract"]["scopes"]["beats"] = segment["timeline"]
        segments.append(segment)

    if long_video and oral_broadcast:
        for index, segment in enumerate(segments[1:], 1):
            previous = segments[index - 1]
            same_scene = (not scene_aware or segment.get("scene_id") == previous.get("scene_id"))
            if same_scene:
                segment["extend_video"] = True
                segment["extend_from_previous"] = True
                segment["continuity_in"] = "上一段视频的最后画面和动作状态"
                segment["continuity_out"] = "保持当前人物、产品、场景、光线和运动方向，交给下一段延长"
                segment["clip_contract"]["scopes"]["clip"]["continuation_mode"] = "extend_previous"
            else:
                segment["extend_video"] = False
                segment["extend_from_previous"] = False
                segment["continuity_in"] = None
                segment["clip_contract"]["scopes"]["clip"]["continuation_mode"] = "fresh_scene"

    for segment in segments:
        handoff = build_video_handoff(segment)
        segment["video_handoff_fingerprint"] = handoff["fingerprint"]

    return {
        "client": client,
        "run_id": run_id,
        "storyboard_approval": approval_identity,
        "segments": segments,
        "total_seconds": total_seconds,
        "total_frames": total_seconds * fps,
        "missing_images": missing,
        "needs_image": needs_image,
        "unconfirmed_refs": unconfirmed_refs,
        "dropped_references": dropped_references,
        "scene_aware": scene_aware,
        "oral_broadcast": oral_broadcast,
        "generation_strategy": "extend" if oral_broadcast else "segmented",
        "scene_count": len({s.get("scene_id") for s in segments if s.get("scene_id")}),
        "contract_version": 1,
        "schema_version": 2,
        "video_handoff_fingerprints": {
            segment["id"]: segment["video_handoff_fingerprint"] for segment in segments},
        "ratio": ratio,
        "resolution": resolution,
        "shot_count": len(shots),
        "storyboard_dir": storyboard_dir,
        "storyboard_plan_fingerprint": plan_fingerprint,
        "storyboard_result_fingerprint": storyboard_fingerprint,
        "render_plan": render_plan_identity,
        "render_plan_fingerprint": _sha256_json(render_plan_identity),
    }


def assemble(segments_spec, results, out_path, ff=None, allow_ocr_warning=False,
             legacy_unsafe=False):
    """多段底片 → ffmpeg 拼接为最终版底片（阶段4 尾）。

    segments_spec: split() 的输出（或含 segments 列表的 dict）。
    results: video_engine.render_batch 的结果列表（每项 {ok, localPath, ocr_warning, ...}），或已下载的
             本地 mp4 路径列表。按 segments 顺序拼接。
    单段直接复制/返回该段；多段调 compose.concat 拼。

    铁律#9：若某段 results[i].ocr_warning=True（OCR 检出画面文字疑似字幕残留）且
    allow_ocr_warning 未显式置 True，则拒绝拼接，抛 RuntimeError 列出问题段号，
    交由 agent/人工决定重新生成或显式放行——绝不静默把带字幕残留的段拼进成片。
    （若 results 是纯字符串路径列表，说明调用方未走 render_batch 的 OCR 检测，
    此时无法判断，直接放行。）

    返回 {ok, out, segment_count, used_paths}。
    """
    segs = segments_spec.get("segments") if isinstance(segments_spec, dict) else segments_spec
    if not isinstance(segs, list) or not all(isinstance(seg, dict) for seg in segs):
        raise TypeError("INVALID_SEGMENTS: 必须提供结构化 segments 列表")
    if isinstance(results, dict):
        results = results.get("results")
    if not legacy_unsafe and (not isinstance(results, list) or
                              not all(isinstance(item, dict) for item in results)):
        raise TypeError("STRUCTURED_RESULTS_REQUIRED: results 必须是结构化对象列表")
    # 解析每段的本地路径（result.localPath 优先；否则按 out_path）
    paths = []
    ocr_flagged = []
    strict_results = not legacy_unsafe or results is not None
    if strict_results and len(results) != len(segs):
        raise RuntimeError("合成结果数量与分段数量不一致：%d != %d" %
                           (len(results), len(segs)))
    for i, seg in enumerate(segs):
        p = None
        if results and i < len(results):
            r = results[i]
            if isinstance(r, dict):
                if not r.get("ok"):
                    raise RuntimeError("段 %s 出片失败，拒绝合成：%s" %
                                       (seg.get("id"), r.get("error") or "未知错误"))
                p = r.get("localPath") or r.get("out_path")
                if not legacy_unsafe and "segment_id" not in r:
                    raise RuntimeError("RESULT_SEGMENT_ID_REQUIRED: 段 %s" % seg.get("id"))
                if r.get("segment_id") != seg.get("id"):
                    raise RuntimeError("段结果与分段 ID 不一致：%s != %s" %
                                       (r.get("segment_id"), seg.get("id")))
                expected_handoff = seg.get("video_handoff_fingerprint")
                if not legacy_unsafe and not expected_handoff:
                    raise RuntimeError("SEGMENT_HANDOFF_REQUIRED: 段 %s" % seg.get("id"))
                if not legacy_unsafe and "video_handoff_fingerprint" not in r:
                    raise RuntimeError("RESULT_HANDOFF_REQUIRED: 段 %s" % seg.get("id"))
                if expected_handoff and r.get("video_handoff_fingerprint") != expected_handoff:
                    raise RuntimeError("STALE_VIDEO_HANDOFF: 段 %s 的成片不属于当前生成契约" % seg.get("id"))
                if not legacy_unsafe:
                    current_fp = take_review.take_fingerprint(r)
                    if not r.get("take_fingerprint") or current_fp != r.get("take_fingerprint"):
                        raise RuntimeError(
                            "STALE_TAKE_ARTIFACT: 段 %s 的当前文件与验片 take 不一致" % seg.get("id"))
                if (seg.get("take_review_required") and r.get("review_status") != "accepted"):
                    raise RuntimeError("TAKE_REVIEW_REQUIRED: 段 %s 尚未通过客户验片" % seg.get("id"))
                if not legacy_unsafe and "ocr_warning" not in r:
                    raise RuntimeError("RESULT_OCR_STATUS_REQUIRED: 段 %s" % seg.get("id"))
                if not legacy_unsafe and not isinstance(r.get("ocr_warning"), bool):
                    raise RuntimeError("INVALID_RESULT_OCR_STATUS: 段 %s" % seg.get("id"))
                if r.get("ocr_warning"):
                    if not legacy_unsafe and not isinstance(r.get("ocr_texts"), list):
                        raise RuntimeError("RESULT_OCR_TEXTS_REQUIRED: 段 %s" % seg.get("id"))
                    ocr_flagged.append({"index": i, "id": seg.get("id"),
                                        "texts": r.get("ocr_texts") or []})
            elif isinstance(r, str):
                if not legacy_unsafe:
                    raise TypeError("STRUCTURED_RESULTS_REQUIRED: 不接受字符串结果")
                p = r
        if not p and strict_results:
            raise RuntimeError("段 %s 没有本次运行的本地成片结果，拒绝回退到旧文件" %
                               seg.get("id"))
        if not p:
            p = seg.get("out_path")
        if not p or not os.path.exists(p):
            raise FileNotFoundError("段 %s 的底片缺失: %r" % (seg.get("id"), p))
        paths.append(os.path.abspath(p))

    if ocr_flagged and not allow_ocr_warning:
        raise RuntimeError(
            "[OCR_WARNING] %d 段检出疑似字幕残留，拒绝静默拼接（铁律#9）："
            "%s。请重新生成对应段，或确认可接受后传 allow_ocr_warning=True 显式放行。"
            % (len(ocr_flagged), ocr_flagged))

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if len(paths) == 1:
        import shutil
        shutil.copyfile(paths[0], out_path)
        return {"ok": True, "out": out_path, "segment_count": 1, "used_paths": paths}

    import compose
    # 默认启用交叉淡化，减少段间断层感；短片段(<=2段)或用户显式关闭时退化为硬切
    use_xfade = len(paths) > 2
    compose.concat(paths, out_path, transition="xfade" if use_xfade else None)
    return {"ok": True, "out": out_path, "segment_count": len(paths), "used_paths": paths}


def persist_and_gate_assemble_ocr(manifest, manifest_path, segments_spec, results, *, client):
    """Persist result OCR evidence and allow only clean or exact waived takes."""
    rm.identity_gate(manifest, client=client)
    segs = segments_spec.get("segments") if isinstance(segments_spec, dict) else segments_spec
    raw_results = results.get("results") if isinstance(results, dict) else results
    recorded_handoff = (manifest.get("handoffs") or {}).get("video") or {}
    expected = {seg.get("id"): seg.get("video_handoff_fingerprint") for seg in segs or []}
    if recorded_handoff.get("segments") != expected:
        raise ValueError("VIDEO_HANDOFF_MISMATCH")
    if not isinstance(raw_results, list) or len(raw_results) != len(segs or []):
        raise ValueError("STRUCTURED_RESULTS_REQUIRED")
    blocked = []
    for seg, result in zip(segs, raw_results):
        sid = str(seg.get("id"))
        take_fp = result.get("take_fingerprint")
        if result.get("segment_id") != seg.get("id") or not take_fp:
            raise ValueError("ASSEMBLE_TAKE_IDENTITY_REQUIRED: %s" % sid)
        existing = (manifest.get("ocr_checks") or {}).get(sid) or {}
        if result.get("ocr_warning"):
            rm.record_ocr_result(manifest, sid, take_fp, "detected",
                                 result.get("ocr_texts") or [], available=True,
                                 frames_checked=result.get("ocr_frames_checked", 0),
                                 expected=result.get("ocr_expected", 0),
                                 error=result.get("ocr_error"))
        elif (result.get("ocr_status") == "clear" and result.get("ocr_available") and
              int(result.get("ocr_expected") or 0) > 0 and
              int(result.get("ocr_frames_checked") or 0) == int(result.get("ocr_expected") or 0)):
            rm.record_ocr_result(manifest, sid, take_fp, "clear", [], available=True,
                                 frames_checked=result["ocr_frames_checked"],
                                 expected=result["ocr_expected"])
        elif not (existing.get("take_fingerprint") == take_fp and
                  rm.ocr_take_is_clear_or_waived(manifest, sid, take_fp)):
            # Legacy results only expose warning/no-warning and cannot prove
            # OCR availability or frame coverage. Persist fail-closed evidence
            # without overwriting an exact manual/full-coverage clear record.
            rm.record_ocr_result(manifest, sid, take_fp, "unavailable", [],
                                 available=False)
        if not rm.ocr_take_is_clear_or_waived(manifest, sid, take_fp):
            blocked.append(sid)
    rm.save_manifest(manifest, manifest_path)
    if blocked:
        raise ValueError("OCR_EXACT_WAIVER_REQUIRED: %s" % ", ".join(blocked))
    return True


def record_video_stage_pending(manifest, manifest_path, *, client, segments_path,
                               results_path, basecut_path, reviews_path):
    """Bind the formal basecut to every review/render input and await approval."""
    rm.identity_gate(manifest, client=client)
    with open(segments_path, encoding="utf-8") as handle:
        segments_spec = json.load(handle)
    with open(results_path, encoding="utf-8") as handle:
        results_value = json.load(handle)
    results = results_value.get("results") if isinstance(results_value, dict) else results_value
    segments = segments_spec.get("segments") or []
    if (segments_spec.get("client") != client or
            str(segments_spec.get("run_id")) != str(manifest.get("run_id"))):
        raise ValueError("VIDEO_STAGE_RUN_IDENTITY_MISMATCH")
    if len(results or []) != len(segments):
        raise ValueError("VIDEO_STAGE_RESULT_COUNT_MISMATCH")
    for segment, result in zip(segments, results):
        sid = str(segment.get("id"))
        accepted = (manifest.get("accepted_takes") or {}).get(sid) or {}
        if not accepted.get("take_fingerprint"):
            raise ValueError("VIDEO_ACCEPTED_TAKE_REQUIRED: %s" % sid)
        if accepted.get("take_fingerprint") != result.get("take_fingerprint"):
            raise ValueError("VIDEO_ACCEPTED_TAKE_MISMATCH: %s" % sid)
        if take_review.take_fingerprint(result) != accepted.get("take_fingerprint"):
            raise ValueError("VIDEO_ACCEPTED_TAKE_ARTIFACT_MISMATCH: %s" % sid)
        if accepted.get("video_handoff_fingerprint") != segment.get("video_handoff_fingerprint"):
            raise ValueError("VIDEO_ACCEPTED_TAKE_HANDOFF_MISMATCH: %s" % sid)
    artifacts = [segments_path, results_path, basecut_path, reviews_path]
    rm.mark_video_generation_finished(manifest, artifacts)
    manifest["video_artifact"] = {
        "status": "pending_approval",
        "segments": rm.file_record(segments_path),
        "results": rm.file_record(results_path),
        "basecut": rm.file_record(basecut_path),
        "reviews": rm.file_record(reviews_path),
        "handoff_sha256": ((manifest.get("handoffs") or {}).get("video") or {}).get("sha256"),
    }
    rm.save_manifest(manifest, manifest_path)
    return manifest["video_artifact"]


def _fmt_srt_ts(seconds):
    """秒 → SRT 时间码 HH:MM:SS,mmm。"""
    if seconds < 0:
        seconds = 0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return "%02d:%02d:%02d,%03d" % (h, m, sec, ms)


_MAX_CHARS_PER_LINE = 20  # 每行字幕最大字符数（含标点）


def _split_long_sentence(text, max_chars=_MAX_CHARS_PER_LINE):
    """Split a long sentence at natural breakpoints (commas, pauses) to fit
    max_chars_per_line. Falls back to hard split if no natural breakpoint."""
    if len(text) <= max_chars:
        return [text]
    # Try splitting at Chinese comma/pause marks
    parts = re.split(r"(?<=[，、；])", text)
    if len(parts) > 1:
        result, current = [], ""
        for p in parts:
            if current and len(current) + len(p) > max_chars:
                result.append(current)
                current = p
            else:
                current += p
        if current:
            result.append(current)
        if all(len(r) <= max_chars for r in result):
            return result
    # Hard split at max_chars boundary
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _split_dialogue_sentences(text):
    """把一段台词按中英文断句符切成若干短句，供逐句字幕换行（避免整段字幕过长）。
    超过 _MAX_CHARS_PER_LINE 的长句会在逗号/顿号处二次拆分。"""
    if not text:
        return []
    # 在中英句末标点后切分，保留可读短句；无标点则整段一句。
    parts = re.split(r"(?<=[。！？!?；;\n])|(?<=[.])\s+", text)
    raw = [p.strip() for p in parts if p and p.strip()]
    if not raw:
        raw = [text.strip()]
    # 二次拆分超长句
    out = []
    for sentence in raw:
        out.extend(_split_long_sentence(sentence))
    return out


def derive_captions(segments_spec, fps=30, per_sentence=True, results=None):
    """连续性反推：定稿剧本各段台词 + 各段实际时长 → 字幕/动效脚本（阶段4尾→阶段5前）。

    解决的流程断点：底片(basecut.mp4)拼完后，此前没有任何一步把「剧本台词 + 各段时长」
    连续性反推成字幕时间轴和动效脚本，导致 subtitle_overlay 需要的 lines.json 只能人工手写。
    本函数按 segments 顺序累加时间轴（段i 的起点 = 前 i-1 段时长之和），产出：
      - srt：标准 SRT 字幕文本（可外挂/压制）
      - lines：[{text,start,end}]，可直接喂 subtitle_overlay.run/build_scenes
      - motion_plan：每段动效脚本骨架（段落时间窗 + 台词 + 可标关键词/动效意图占位），
        供后续 HyperFrames 编排（kinetic typography / 关键词快闪）填充。

    per_sentence=True：把每段台词按句末标点再切成逐句字幕（时间在段内按字数比例分配），
      字幕更贴合语音节奏、单条不过长；False 则整段台词一条字幕占满该段时间窗。

    **不自动往下走**：返回 needs_confirmation=True。时间轴/断句/动效意图需用户确认后，
      才继续 subtitle_overlay 渲染字幕层 + 合成。台词无声音轨精确对齐（音画一体模型未回传
      逐字时间戳），时间轴是按时长/字数估算的近似值，务必让用户核对再压制。
    """
    segs = segments_spec.get("segments") if isinstance(segments_spec, dict) else segments_spec
    if not segs:
        raise ValueError("derive_captions: 空 segments，无法反推字幕/动效")

    raw_results = results.get("results") if isinstance(results, dict) else results
    if raw_results is not None and (not isinstance(raw_results, list) or
                                    not all(isinstance(r, dict) for r in raw_results)):
        raise TypeError("derive_captions results 必须是结构化对象列表")
    result_by_id = {
        str(result.get("segment_id")): result for result in (raw_results or [])
        if result.get("segment_id")
    }

    srt_blocks = []
    lines = []
    motion_plan = []
    cursor = 0.0
    idx = 1
    for seg in segs:
        sid = seg.get("id")
        rendered = result_by_id.get(str(sid))
        actual = None
        if rendered:
            actual = rendered.get("actual_duration") or rendered.get("duration")
            if not actual and (rendered.get("localPath") or rendered.get("out_path")):
                actual = _probe_duration(rendered.get("localPath") or rendered.get("out_path"))
        secs = float(actual or seg.get("duration") or 0)
        if secs <= 0:
            secs = 3.0
        seg_start, seg_end = cursor, cursor + secs
        dialogue = (seg.get("dialogue") or "").strip()

        # 段级动效脚本骨架：时间窗 + 台词 + 待填的关键词/动效意图（供 HyperFrames 编排）
        motion_plan.append({
            "seg_id": sid,
            "start": round(seg_start, 3),
            "end": round(seg_end, 3),
            "dialogue": dialogue,
            "emphasis_keywords": [],          # 待用户/LLM 标：需快闪强调的关键词
            "motion": "",                      # 待填：kinetic/pop-in/underline/none 等动效意图
            "motion_elements": list(seg.get("motion_elements") or [])
                if not isinstance(seg.get("motion_elements"), str)
                else [seg["motion_elements"]],
            "note": "",
        })

        if not dialogue:
            # 无台词段（纯画面/音乐）：不生成字幕，仅占位时间窗给动效脚本
            cursor = seg_end
            continue

        if per_sentence:
            sentences = _split_dialogue_sentences(dialogue)
            total_chars = sum(len(re.sub(r"\s", "", s)) for s in sentences) or 1
            sub_cursor = seg_start
            for si, sent in enumerate(sentences):
                w = len(re.sub(r"\s", "", sent)) or 1
                dur = secs * (w / total_chars)
                st = sub_cursor
                en = seg_end if si == len(sentences) - 1 else sub_cursor + dur
                lines.append({"text": sent, "start": round(st, 3), "end": round(en, 3),
                              "seg_id": sid})
                srt_blocks.append("%d\n%s --> %s\n%s\n" % (
                    idx, _fmt_srt_ts(st), _fmt_srt_ts(en), sent))
                idx += 1
                sub_cursor = en
        else:
            lines.append({"text": dialogue, "start": round(seg_start, 3),
                          "end": round(seg_end, 3), "seg_id": sid})
            srt_blocks.append("%d\n%s --> %s\n%s\n" % (
                idx, _fmt_srt_ts(seg_start), _fmt_srt_ts(seg_end), dialogue))
            idx += 1

        cursor = seg_end

    return {
        "ok": True,
        "needs_confirmation": True,
        "total_seconds": round(cursor, 3),
        "srt": "\n".join(srt_blocks).strip() + "\n" if srt_blocks else "",
        "lines": lines,
        "motion_plan": motion_plan,
        "note": ("时间轴按各段时长/字数估算（音画一体模型未回传逐字时间戳），"
                 "为近似值。请用户确认时间轴/断句/动效关键词后，再继续字幕渲染与合成。"),
    }


def _sha256_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_json(path, value):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def persist_caption_artifact(manifest, manifest_path, *, client, segments_path,
                             basecut_path, lines_path, srt_path, motion_path,
                             caption_manifest_path):
    """Bind estimated caption files to this run, handoff, and basecut bytes."""
    rm.identity_gate(manifest, client=client)
    if not rm.approval_is_current(manifest, "video"):
        raise ValueError("CAPTION_VIDEO_APPROVAL_REQUIRED")
    with open(segments_path, encoding="utf-8") as handle:
        segments_spec = json.load(handle)
    if segments_spec.get("client") != client or str(segments_spec.get("run_id")) != str(manifest.get("run_id")):
        raise ValueError("CAPTION_RUN_IDENTITY_MISMATCH")
    expected = {str(seg.get("id")): seg.get("video_handoff_fingerprint")
                for seg in segments_spec.get("segments") or []}
    recorded = (manifest.get("handoffs") or {}).get("video") or {}
    if not expected or recorded.get("segments") != expected:
        raise ValueError("CAPTION_VIDEO_HANDOFF_MISMATCH")
    records = {name: rm.file_record(path) for name, path in (
        ("segments", segments_path), ("basecut", basecut_path), ("lines", lines_path),
        ("srt", srt_path), ("motion", motion_path))}
    if any(not record or not record.get("exists") for record in records.values()):
        raise ValueError("CAPTION_ARTIFACT_MISSING")
    identity_payload = {
        "client": client, "run_id": manifest.get("run_id"),
        "video_handoff_sha256": recorded.get("sha256"),
        "files": {name: record.get("sha256") for name, record in records.items()},
    }
    artifact = {
        "schema_version": 1, "artifact_type": "caption_timeline",
        "client": client, "run_id": manifest.get("run_id"),
        "status": "pending_approval", "created_at": datetime.now().isoformat(timespec="seconds"),
        "video_handoff_sha256": recorded.get("sha256"), "files": records,
        "caption_identity": _sha256_json(identity_payload),
    }
    _atomic_json(caption_manifest_path, artifact)
    rm.mark_generation_finished(manifest, "captions", [lines_path, srt_path, motion_path])
    manifest["caption_artifact"] = {
        "path": os.path.abspath(caption_manifest_path),
        "caption_identity": artifact["caption_identity"], "status": "pending_approval",
    }
    rm.save_manifest(manifest, manifest_path)
    return artifact


def caption_artifact_is_current(manifest, caption_artifact, *, client=None,
                                require_approved=True):
    """Re-read every bound file and validate the caption timeline identity."""
    rm.identity_gate(manifest, client=client)
    if (caption_artifact.get("client") != manifest.get("client") or
            str(caption_artifact.get("run_id")) != str(manifest.get("run_id"))):
        raise ValueError("CAPTION_RUN_IDENTITY_MISMATCH")
    for name in ("segments", "basecut", "lines", "srt", "motion"):
        if not rm.file_record_is_current((caption_artifact.get("files") or {}).get(name)):
            raise ValueError("STALE_CAPTION_ARTIFACT: %s hash 已变化" % name)
    handoff = (manifest.get("handoffs") or {}).get("video") or {}
    if caption_artifact.get("video_handoff_sha256") != handoff.get("sha256"):
        raise ValueError("CAPTION_VIDEO_HANDOFF_MISMATCH")
    payload = {
        "client": caption_artifact.get("client"), "run_id": caption_artifact.get("run_id"),
        "video_handoff_sha256": caption_artifact.get("video_handoff_sha256"),
        "files": {name: caption_artifact["files"][name].get("sha256")
                  for name in ("segments", "basecut", "lines", "srt", "motion")},
    }
    if caption_artifact.get("caption_identity") != _sha256_json(payload):
        raise ValueError("CAPTION_IDENTITY_MISMATCH")
    if require_approved:
        recorded = manifest.get("caption_artifact") or {}
        if (caption_artifact.get("status") != "approved" or
                recorded.get("status") != "approved" or
                recorded.get("caption_identity") != caption_artifact.get("caption_identity") or
                not rm.approval_is_current(manifest, "captions")):
            raise ValueError("CAPTION_APPROVAL_REQUIRED")
    return True


def confirm_captions(manifest, manifest_path, caption_manifest_path, *, client):
    """Approve the exact timeline files without making rendered subtitles circular."""
    with open(caption_manifest_path, encoding="utf-8") as handle:
        artifact = json.load(handle)
    caption_artifact_is_current(manifest, artifact, client=client, require_approved=False)
    rm.approve(manifest, "captions", strict=True)
    artifact["status"] = "approved"
    artifact["approved_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_json(caption_manifest_path, artifact)
    manifest["caption_artifact"] = {
        "path": os.path.abspath(caption_manifest_path),
        "caption_identity": artifact["caption_identity"], "status": "approved",
    }
    rm.save_manifest(manifest, manifest_path)
    return artifact


def _probe_duration(video_path):
    """Read the actual rendered duration; returns None when ffprobe is unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not os.path.isfile(video_path):
        return None
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stderr=subprocess.STDOUT, text=True)
        value = float(out.strip())
        return value if value > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def align_captions_to_audio(derived, video_path, api_key=None):
    """Align the estimated timeline to the actual rendered audio.

    Three-tier strategy:
      1. ASR-based alignment (subtitle_asr.align_with_asr) — silence
         detection or cloud ASR for sentence-level timestamps.
      2. Duration scaling — ratio-scale estimates to match actual duration.
      3. Keep original estimates if both fail.
    """
    actual = _probe_duration(video_path)
    if actual is None or not derived.get("total_seconds"):
        derived["timeline_source"] = "estimated"
        return derived

    # Tier 1: ASR-based sentence alignment
    asr_aligned = subtitle_asr.align_with_asr(
        derived.get("lines", []), video_path, api_key=api_key)
    if asr_aligned:
        derived["lines"] = asr_aligned
        derived["total_seconds"] = round(actual, 3)
        derived["timeline_source"] = "asr_aligned"
        derived["needs_confirmation"] = True
        derived["note"] = ("已通过音频分析(ASR/静音检测)对齐逐句时间戳，"
                            "准确率高于字数比例估算；请抽查确认后再渲染字幕。")
        derived["srt"] = subtitle_asr.lines_to_srt(asr_aligned)
        # Re-scale motion_plan blocks proportionally
        old_total = float(derived.get("total_seconds") or 1)
        ratio = actual / old_total if old_total > 0 else 1.0
        if abs(ratio - 1.0) > 0.01:
            for block in derived.get("motion_plan", []):
                block["start"] = round(block["start"] * ratio, 3)
                block["end"] = round(block["end"] * ratio, 3)
        return derived

    # Tier 2: Duration scaling (original fallback)
    old = float(derived["total_seconds"])
    ratio = actual / old
    for line in derived.get("lines", []):
        line["start"] = round(line["start"] * ratio, 3)
        line["end"] = round(line["end"] * ratio, 3)
    for block in derived.get("motion_plan", []):
        block["start"] = round(block["start"] * ratio, 3)
        block["end"] = round(block["end"] * ratio, 3)
    derived["total_seconds"] = round(actual, 3)
    derived["timeline_source"] = "audio_duration_aligned_estimated_sentences"
    derived["needs_confirmation"] = True
    derived["note"] = ("已按成片实际时长校准时间轴，但未取得逐字 ASR 时间戳；"
                        "断句仍为按台词/字数估算，请确认后再渲染字幕。")
    # Rebuild SRT from the adjusted lines.
    derived["srt"] = "".join(
        "%d\n%s --> %s\n%s\n\n" % (i, _fmt_srt_ts(line["start"]),
                                      _fmt_srt_ts(line["end"]), line["text"])
        for i, line in enumerate(derived.get("lines", []), 1)
    )
    return derived


def main(argv=None):
    ap = argparse.ArgumentParser(description="阶段4 · 剧本按时长拆分 + 多段底片合成")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split", help="定稿 storyboard_plan.json → render_batch segments")
    sp.add_argument("--plan", required=True)
    sp.add_argument("--storyboard-dir", help="分镜图目录（阶段3 storyboard.py 输出）")
    sp.add_argument("--out", help="输出 segments JSON（默认 stdout）")
    sp.add_argument("--fps", type=int, default=30)
    sp.add_argument("--min-seconds", type=int, default=3)
    sp.add_argument("--ratio", dest="ratio_override", choices=sorted(_RATIO_MAP),
                    help="覆盖故事板比例作为成片比例，例如 9:16；默认沿用 plan")
    sp.add_argument("--color-storyboard", action="store_true",
                    help="故事板本身是彩色时用它（分镜图作首帧锚优先）；默认按黑白故事板处理"
                         "——彩色素材优先锚定颜色，黑白分镜图垫后作构图提示。也可由 plan.color_mode 决定。")
    sp.add_argument("--allow-text2video", action="store_true",
                    help="铁律#11默认拦截：若某镜零锚定素材（无分镜图也无 asset_refs），"
                         "该镜默认跳过并记入 needs_image，需先回填素材再重新 split。"
                         "确认为纯数字人口播、无需产品图/场景图时，加此参数显式放行"
                         "（此时缺图镜头走 video_type=1 文生视频）。")
    sp.add_argument("--client", required=True,
                    help="传入后会用 asset_prep.is_confirmed() 跨模块校验每段锚定素材是否"
                         "已过客户确认(confirmed)，未确认(pending)默认拦截。不传则跳过该检查。")
    sp.add_argument("--allow-unconfirmed", action="store_true",
                    help="确认/待确认状态机默认拦截：命中 pending(未确认)锚定素材直接报错。"
                          "确需用未确认素材做草稿预览时，加此参数显式放行。")
    sp.add_argument("--run-id")
    sp.add_argument("--manifest", help="正式流程 run manifest；成功写出后原子登记 video handoff")
    sp.add_argument("--draft", action="store_true", help="草稿兼容：不要求 manifest/render-plan 审批")
    sp.add_argument("--draft-allow-unapproved-storyboard", action="store_true",
                    help="仅草稿：允许 storyboard_dir 未确认；正式流程禁止绕过")

    asm = sub.add_parser("assemble", help="多段底片 → ffmpeg 拼接为最终版底片")
    asm.add_argument("--segments", required=True, help="split 输出的 segments JSON")
    asm.add_argument("--results", help="render_batch 结构化结果 JSON")
    asm.add_argument("--out", required=True)
    asm.add_argument("--allow-ocr-warning", action="store_true",
                    help="铁律#9默认拦截：若任一段 results[i].ocr_warning=True（疑似字幕残留）"
                          "则拒绝拼接并报错列出问题段。确认可接受后加此参数显式放行。")
    asm.add_argument("--legacy-unsafe", action="store_true",
                     help="危险兼容模式：允许无 results/字符串路径/旧 out_path 回退")
    asm.add_argument("--client")
    asm.add_argument("--manifest", help="正式流程 run manifest；登记每个 take 的 OCR evidence")
    asm.add_argument("--reviews", help="正式流程验片汇总 JSON；与 segments/results/basecut 一起绑定审批")
    asm.add_argument("--draft", action="store_true", help="草稿兼容：不登记 manifest/OCR identity")

    dc = sub.add_parser("derive-captions",
                        help="底片拼完后连续性反推：剧本台词+各段时长 → SRT/lines.json/动效脚本(待确认)")
    dc.add_argument("--segments", required=True, help="split 输出的 segments JSON")
    dc.add_argument("--fps", type=int, default=30)
    dc.add_argument("--whole-segment", action="store_true",
                    help="整段台词一条字幕（默认按句末标点切逐句字幕，更贴语音节奏）")
    dc.add_argument("--srt-out", help="写出 SRT 字幕文件路径")
    dc.add_argument("--lines-out", help="写出 lines.json（喂 subtitle_overlay.run）路径")
    dc.add_argument("--motion-out", help="写出 motion_plan.json（动效脚本骨架）路径")
    dc.add_argument("--audio-video", help="用实际成片时长校准字幕时间轴（仍需人工确认断句）")
    dc.add_argument("--results", help="可选 render_batch results JSON，优先采用每段实际时长")
    dc.add_argument("--client")
    dc.add_argument("--manifest", help="正式流程 run_manifest.json")
    dc.add_argument("--basecut", help="正式流程绑定的已确认底片")
    dc.add_argument("--caption-manifest-out", help="可持久化 caption timeline identity")
    dc.add_argument("--draft", action="store_true", help="草稿兼容：不登记/审批 caption artifact")

    cc = sub.add_parser("confirm-captions", help="确认当前 caption timeline artifact")
    cc.add_argument("--client", required=True)
    cc.add_argument("--manifest", required=True)
    cc.add_argument("--caption-manifest", required=True)

    a = ap.parse_args(argv)
    if a.cmd == "split":
        if not a.manifest and not a.draft:
            ap.error("split 正式流程必须提供 --manifest，以校验已确认 render plan；旧方式需 --draft")
        if a.manifest and not a.out:
            ap.error("split 使用 --manifest 时必须提供 --out，才能原子登记 handoff 文件")
        with open(a.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
        manifest = None
        render_plan_artifact = None
        if a.manifest:
            with open(a.manifest, encoding="utf-8") as f:
                manifest = json.load(f)
            artifacts = (manifest.get("generation", {}).get("render_plan", {})
                         .get("artifacts") or [])
            if len(artifacts) != 1:
                raise ValueError("RENDER_PLAN_ARTIFACT_REQUIRED")
            render_plan_artifact = artifacts[0]
        bw = False if a.color_storyboard else None  # None=按 plan.color_mode（默认黑白）
        result = split(plan, storyboard_dir=a.storyboard_dir, fps=a.fps,
                       min_seconds=a.min_seconds, bw_storyboard=bw,
                       allow_text2video=a.allow_text2video, client=a.client,
                       allow_unconfirmed=a.allow_unconfirmed,
                       ratio_override=a.ratio_override, run_id=a.run_id,
                       draft_allow_unapproved_storyboard=a.draft_allow_unapproved_storyboard,
                       render_plan_artifact=render_plan_artifact)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if a.out:
            _atomic_json(a.out, result)
            if a.manifest:
                rm.identity_gate(manifest, client=a.client)
                rm.generation_gate(manifest, "video", client=a.client)
                if str(result.get("run_id")) != str(manifest.get("run_id")):
                    raise ValueError("VIDEO_HANDOFF_RUN_IDENTITY_MISMATCH")
                if result.get("needs_image") or not result.get("segments"):
                    raise ValueError("VIDEO_HANDOFF_INCOMPLETE: split 尚有待补素材或无 segments")
                rm.record_video_handoff(manifest, result, a.out)
                rm.save_manifest(manifest, a.manifest)
            print("已拆分: %s（%d 段，总时长 %ds）"
                  % (a.out, len(result["segments"]), result["total_seconds"]))
            if result["missing_images"]:
                print("[警告] 以下 shot 未找到分镜图（将无首帧锚定）: %s"
                      % ", ".join(result["missing_images"]))
            if result["needs_image"]:
                print("[待补素材] 以下 shot 零锚定素材，已跳过未生成 segment: %s\n"
                      "若确为纯数字人口播无需产品图/场景图，加 --allow-text2video 显式放行。"
                      % ", ".join(result["needs_image"]))
            if result.get("unconfirmed_refs"):
                print("[待确认] 以下素材未经客户确认(status=pending)，因 --allow-unconfirmed "
                      "已放行仍纳入出片，请注意可能是客户还没选定/可能被拒绝的候选版本: %s"
                      % result["unconfirmed_refs"])
        else:
            print(text)
    elif a.cmd == "assemble":
        if not a.results and not a.legacy_unsafe:
            ap.error("assemble 正式流程必须提供 --results；旧文件回退需显式 --legacy-unsafe")
        if not a.draft and not a.legacy_unsafe and not (a.client and a.manifest and a.reviews):
            ap.error("assemble 正式流程必须提供 --client/--manifest/--reviews；旧方式需 --draft")
        if a.allow_ocr_warning and not (a.legacy_unsafe or a.draft):
            ap.error("正式 assemble 不接受全局 --allow-ocr-warning；请在 run manifest 为精确"
                     " segment/take/OCR texts 登记 waiver。旧链路仅可与 --legacy-unsafe 同用")
        with open(a.segments, "r", encoding="utf-8") as f:
            spec = json.load(f)
        results = None
        if a.results:
            with open(a.results, "r", encoding="utf-8") as f:
                results = json.load(f)
        exact_ocr_allowed = False
        if not a.draft and not a.legacy_unsafe:
            with open(a.manifest, encoding="utf-8") as f:
                manifest = json.load(f)
            persist_and_gate_assemble_ocr(manifest, a.manifest, spec, results,
                                          client=a.client)
            exact_ocr_allowed = True
        r = assemble(spec, results, a.out,
                     allow_ocr_warning=a.allow_ocr_warning or exact_ocr_allowed,
                     legacy_unsafe=a.legacy_unsafe)
        if not a.draft and not a.legacy_unsafe:
            record_video_stage_pending(
                manifest, a.manifest, client=a.client, segments_path=a.segments,
                results_path=a.results, basecut_path=a.out, reviews_path=a.reviews)
            r["status"] = "pending_approval"
        print(json.dumps(r, ensure_ascii=False))
    elif a.cmd == "derive-captions":
        formal_required = (a.client, a.manifest, a.basecut, a.caption_manifest_out,
                           a.srt_out, a.lines_out, a.motion_out)
        if not a.draft and not all(formal_required):
            ap.error("derive-captions 正式流程必须提供 --client/--manifest/--basecut/"
                     "--caption-manifest-out/--srt-out/--lines-out/--motion-out；旧方式需 --draft")
        with open(a.segments, "r", encoding="utf-8") as f:
            spec = json.load(f)
        caption_results = None
        if a.results:
            with open(a.results, "r", encoding="utf-8") as f:
                caption_results = json.load(f)
        r = derive_captions(spec, fps=a.fps, per_sentence=not a.whole_segment,
                            results=caption_results)
        if a.audio_video:
            r = align_captions_to_audio(r, a.audio_video)
        if a.srt_out:
            with open(a.srt_out, "w", encoding="utf-8") as f:
                f.write(r["srt"])
        if a.lines_out:
            with open(a.lines_out, "w", encoding="utf-8") as f:
                json.dump(r["lines"], f, ensure_ascii=False, indent=2)
        if a.motion_out:
            with open(a.motion_out, "w", encoding="utf-8") as f:
                json.dump(r["motion_plan"], f, ensure_ascii=False, indent=2)
        caption_artifact = None
        if not a.draft:
            with open(a.manifest, encoding="utf-8") as f:
                manifest = json.load(f)
            caption_artifact = persist_caption_artifact(
                manifest, a.manifest, client=a.client, segments_path=a.segments,
                basecut_path=a.basecut, lines_path=a.lines_out, srt_path=a.srt_out,
                motion_path=a.motion_out, caption_manifest_path=a.caption_manifest_out)
        # stdout 给摘要（不打印全量 srt 以免刷屏），文件路径 + 待确认提示
        summary = {
            "ok": r["ok"], "needs_confirmation": r["needs_confirmation"],
            "total_seconds": r["total_seconds"], "caption_lines": len(r["lines"]),
            "motion_segments": len(r["motion_plan"]),
            "srt_out": a.srt_out, "lines_out": a.lines_out, "motion_out": a.motion_out,
            "caption_manifest": a.caption_manifest_out,
            "caption_identity": caption_artifact.get("caption_identity") if caption_artifact else None,
            "note": r["note"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif a.cmd == "confirm-captions":
        with open(a.manifest, encoding="utf-8") as f:
            manifest = json.load(f)
        artifact = confirm_captions(manifest, a.manifest, a.caption_manifest,
                                    client=a.client)
        print(json.dumps({"ok": True, "caption_identity": artifact["caption_identity"],
                          "status": artifact["status"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        code = str(exc).split(":", 1)[0] or "WORKFLOW_BLOCKED"
        if code == "RENDER_PLAN_ARTIFACT_REQUIRED":
            next_step = ("下一步：先在当前 run 的 manifest 中登记并确认 render plan，"
                         "再重新执行 split；可用 `python3 START_HERE_AGENT.py status --manifest <manifest>` 查看阶段。")
        elif code in ("STORYBOARD_DIR_NOT_FOUND", "STORYBOARD_APPROVAL_REQUIRED"):
            next_step = ("下一步：先使用 storyboard.py --stage next 生成并确认当前 run 的故事板，"
                         "再重新执行 split。")
        elif code in ("UNCONFIRMED_ASSET", "MISSING_REFERENCE"):
            next_step = ("下一步：上传并确认缺失素材，或为内部预览显式使用 --draft/--allow-unconfirmed；"
                         "正式客户流程不能跳过确认。")
        else:
            next_step = "下一步：请查看当前 run 状态并完成该阶段要求后重试。"
        print("ERROR:%s\n%s\n详情：%s" % (code, next_step, exc), file=sys.stderr)
        raise SystemExit(2)
