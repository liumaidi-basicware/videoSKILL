#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chinese prompt preview, LLM refinement, and explicit approval gate."""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import br_client  # noqa: E402
import key_setup  # noqa: E402


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()[:16]


def plan_fingerprint(plan):
    """Stable identity shared by prompt review and generation entrypoints."""
    # Import lazily to avoid storyboard -> prompt_review import cycles. The
    # review must bind to the same normalized plan used by image generation.
    import storyboard
    canonical = storyboard.expand_product_sku_refs(
        storyboard.canonical_storyboard_plan(plan))
    return storyboard.plan_fingerprint(canonical)


def visual_plan_fingerprint(plan):
    """Visual-only identity: excludes dialogue/voiceover/audio.

    Delegates to storyboard.visual_plan_fingerprint, which canonicalizes
    exactly once. Do NOT pre-canonicalize here — partition_shots is not
    idempotent once dialogue has been split, so a second canonical pass would
    make the fingerprint drift on dialogue-only edits.
    """
    import storyboard
    return storyboard.visual_plan_fingerprint(plan)


def _repair_unescaped_quotes(text):
    """Heuristic repair for LLM JSON where raw ASCII '"' was used as a
    Chinese-style emphasis/quotation mark inside string values instead of
    being escaped as '\\"' (real-world failure mode observed with kimi-k3:
    prompt_zh often quotes the spoken dialogue verbatim using '"...\"').

    Walks the text as a tiny state machine. While inside a JSON string
    value, a '"' is only treated as the real closing quote if the next
    non-whitespace character is a JSON structural token (, : } ] or EOF).
    Otherwise it is almost certainly a literal quote mark meant as content,
    so it gets escaped in place and scanning continues as still-inside-string.
    This never touches already-escaped quotes or characters outside strings.
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                nxt = text[j] if j < n else ""
                if nxt in (",", ":", "}", "]", ""):
                    in_string = False
                    out.append(ch)
                    i += 1
                    continue
                out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("PROMPT_REVIEW_INVALID_JSON: 大模型未返回可解析的提示词 JSON")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Common failure mode: the model embeds unescaped ASCII quotes as
        # emphasis marks inside a Chinese string value (e.g. quoting the
        # spoken line verbatim). Try a targeted repair before giving up —
        # this recovers valid content instead of forcing a full re-poll.
        try:
            return json.loads(_repair_unescaped_quotes(candidate))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "PROMPT_REVIEW_INVALID_JSON: 大模型返回的 JSON 无法解析（含修复重试）："
                "%s" % exc) from exc


def _product_facts(plan):
    facts = dict(plan.get("product_facts") or {})
    for source, target in (
            ("product_name", "product_name"),
            ("product", "product_name"),
            ("model", "model"),
            ("product_type", "product_type")):
        value = plan.get(source)
        if value and not facts.get(target):
            facts[target] = value
    if plan.get("features") and not facts.get("features"):
        facts["features"] = plan["features"]
    return facts


def _continuity(plan):
    return plan.get("continuity_contract") or plan.get("continuity") or {}


def _continuity_for_shot(plan, shot):
    continuity = dict(_continuity(plan))
    if not (shot or {}).get("characters"):
        for key in ("character_identity", "wardrobe", "makeup", "hair"):
            continuity.pop(key, None)
    return continuity


def _shot_character_rule(shot):
    if shot.get("characters"):
        return "人物脸、发型、服装必须引用已确认人物素材或后续人物板，保持同一身份；"
    return "本镜头没有人物出镜，不得凭空添加人物、人脸、头部、身体、坐姿人物、模特、发型或服装；如动作确实需要互动，只允许裁切手部/手指；"


def _base_storyboard_prompt(plan, shot):
    facts = _product_facts(plan)
    continuity = _continuity_for_shot(plan, shot)
    character_rule = _shot_character_rule(shot)
    product_rule = (
        "产品必须引用已确认产品素材；如果后续已有确认产品板或产品使用图，也必须与其一致。"
        "不得把产品改成耳机、充电宝、充电盒、甜点或其它物品；"
    )
    return """你是商业广告分镜导演。请根据以下已确认剧本和素材事实，输出一段中文、可直接交给 gpt-image-2 的电影级 16:9 横版故事板提示词。故事板格数由已确认的 shots[] 分镜数量决定，不得为了凑固定 12 格而拆分或合并镜头。

硬性事实：产品=%s；型号=%s；颜色=%s；规格=%s；场景连续性=%s；台词=%s；画面=%s；动作=%s；镜头=%s。
要求：写清当前镜头的景别、机位、主体位置、动作起止、手部接触、光线、产品结构和与前后镜头的连续关系；%s%s相邻镜头要有明显景别或机位变化；背景不能出现生成字幕、口号、标签、Logo、水印；只保留产品包装/设备原生文字；故事板默认黑白铅笔/炭笔预演风。
只返回 JSON：{"prompt_zh":"...","continuity_notes":["..."],"negative_prompt_zh":"..."}""" % (
        facts.get("product_name") or facts.get("product_type") or "已确认产品",
        facts.get("model") or "以已确认产品资料为准",
        facts.get("product_color") or "以已确认产品板为准",
        json.dumps(facts, ensure_ascii=False), json.dumps(continuity, ensure_ascii=False),
        shot.get("dialogue") or shot.get("voiceover") or "无", shot.get("visual") or "无",
        shot.get("character_action") or shot.get("action") or "无", shot.get("camera") or "无",
        character_rule, product_rule)


def _base_video_prompt(plan, shot):
    facts = _product_facts(plan)
    continuity = _continuity_for_shot(plan, shot)
    character_rule = _shot_character_rule(shot)
    return """你是 Kling 视频导演和口播节奏设计师。请把已确认的第 %s 段中文剧本打磨成可直接提交的视频提示词。

产品事实：%s。台词：%s。动作：%s。镜头：%s。连续性合同：%s。
要求：明确 0-1 秒起始状态、动作发展、手部接触点、人物微表情、镜头运动、景别变化、产品外观锁、背景灯光锁、台词口型和声音语气；%s如果是延长段，必须明确从上一段最后状态无缝继续；不得重演上一段，不添加角色，不改服装/产品/场景，不把故事板网格作为视频画面，不生成字幕、文字、口号或水印。控制在 2200 个中文字符以内。只返回 JSON：{"prompt_zh":"...","negative_prompt_zh":"...","continuity_in":"...","continuity_out":"...","audio_contract":"..."}""" % (
        shot.get("id", "unknown"), json.dumps(facts, ensure_ascii=False),
        shot.get("dialogue") or shot.get("voiceover") or "无",
        shot.get("character_action") or shot.get("action") or shot.get("visual") or "无",
        shot.get("camera") or shot.get("camera_movement") or "无",
        json.dumps(continuity, ensure_ascii=False), character_rule)


def build_prompt_requests(plan, stage):
    shots = plan.get("shots") or []
    return [{"shot_id": shot.get("id") or str(i + 1), "stage": stage,
             "shot": shot,
             "source_prompt_zh": (_base_storyboard_prompt(plan, shot) if stage == "storyboard"
                                   else _base_video_prompt(plan, shot))}
            for i, shot in enumerate(shots)]


# Text models suitable for prompt polishing (LLM-based text generation).
# Image/video models like gpt-image-2 must NOT be passed here.
_TEXT_MODEL_HINTS = ("qwen", "gpt-4", "gpt-3", "claude", "kimi", "deepseek",
                     "gemini", "llama", "mistral", "yi", "chatglm", "moonshot")
_IMAGE_MODEL_HINTS = ("image", "dall", "flux", "stable", "midjourney",
                      "seedream", "imagen", "banana")


def _validate_text_model(model):
    """Ensure the polish model is a text/LLM model, not an image/video model."""
    lower = str(model or "").lower()
    for hint in _IMAGE_MODEL_HINTS:
        if hint in lower:
            raise ValueError(
                "PROMPT_REVIEW_MODEL_INVALID: '%s' 是图像/视频模型，prompt_review "
                "需要文字模型（如 qwen3.6-plus、gpt-4o、kimi-k3）" % model)
    return True


# Retries for a single shot's polish call when the model returns JSON that
# even _extract_json's repair pass cannot parse. Observed real failure mode
# (kimi-k3): the model quotes spoken dialogue verbatim inside a JSON string
# using raw ASCII '"' instead of '\"', e.g. 台词"出门..."响起 — the repair
# heuristic recovers most cases, but on the rare miss we re-poll instead of
# aborting the whole multi-shot batch over one shot.
_POLISH_JSON_RETRIES = 2


def _violates_no_human_scope(shot, prompt_text):
    """Detect obvious prompt drift for product-only shots.

    A product-only visual hook can tolerate product motion, but adding hands or
    a person changes the client-approved storyboard. Shots that explicitly ask
    for hand operation remain allowed.
    """
    if shot.get("characters"):
        return False
    source = "%s %s %s" % (
        shot.get("visual") or "", shot.get("character_action") or "",
        shot.get("action") or "")
    if any(term in source for term in ("手", "手部", "手指", "拇指", "食指")):
        return False
    if any(term in source for term in ("无人出镜", "无人物", "产品以", "只展示产品")):
        positive_text = "。".join(
            part for part in str(prompt_text or "").replace("\n", "。").split("。")
            if not any(blocker in part for blocker in
                       ("严禁", "禁止", "不得", "不出现", "无人物", "无人",
                        "无任何", "仅保留", "negative"))
        )
        return any(term in positive_text for term in
                   ("手", "手部", "手指", "拇指", "食指", "人物", "人脸", "发型", "服装"))
    return False


def validate_polished_prompt(plan, item, result):
    shot = item.get("shot") or {}
    prompt_text = result.get("prompt_zh") or ""
    if _violates_no_human_scope(shot, prompt_text):
        raise ValueError(
            "PROMPT_REVIEW_SCOPE_DRIFT: 镜头 %s 原计划为无人纯产品镜头，但润色提示词加入了人物/手部元素。"
            "请重新 polish 或手动修正后再确认。" % item.get("shot_id"))


def _asset_composition_skill_request(plan, asset_item):
    """Build a tightly scoped prompt for model-assisted asset composition.

    The text model may decide how to frame the already-approved physical
    relation, but it may not rewrite the relation itself.
    """
    facts = _product_facts(plan)
    relation = asset_item.get("physical_relation_contract") or {}
    geometry = asset_item.get("geometry_contract") or ""
    outcome_context = asset_item.get("usage_outcome_context") or []
    source_prompt = asset_item.get("submission_prompt_zh") or ""
    return """你是“产品使用图构图提示词 skill”，只负责把已确认的产品使用物理合同转成 3x3 九宫格构图 brief，不负责改产品事实、改接触面、改目标物、改人物身份或改剧情。

【硬性输入，不可改写】
产品事实: %s
几何合同: %s
物理关系合同: %s
使用结果/卖点上下文: %s

【当前基础提示词】
%s

请输出 JSON，字段固定如下：
{
  "composition_strategy": "一句话说明这张图的构图策略",
  "primary_subject_scope": "哪些主体必须成为画面主语，哪些主体只能辅助",
  "camera_scope": "允许的景别/角度范围",
  "range_limits": "明确哪些画面范围不允许出现",
  "panel_plan": [
    {"shot_size":"", "composition":"", "must_show":"", "proof_goal":"", "forbidden":""}
  ],
  "outcome_panels": ["说明哪些格子承担卖点结果证明"],
  "must_include": ["..."],
  "must_exclude": ["..."]
}

要求：
1. panel_plan 必须恰好 9 格。
2. 如果有接触/连接/安装/佩戴/支撑合同，至少 7 格必须同时包含 active product 与 receiver object，并证明 contact surface 关系。
3. 对表面连接类合同，尤其第4/5/7/8/9格，必须逐格原样写入这些英文 token：product contact surface、receiver contact surface、bottom/base magnetic surface、receiver back plane、flush、protrudes outward。不能只写 attached product、outward surface、wider context。
4. 严禁“物品立在/站在/放在/坐在/搁在目标物上方、边缘或屏幕上”的构图；严禁只写 attached product、outward surface、wider context 这类无法证明接触面的泛化词。
5. 如果提供了“使用结果/卖点上下文”，至少 2 格必须承担 outcome proof：在仍保持接触面正确的前提下，证明产品带来的实际使用结果。凡是 outcome 仍依赖连接/安装/佩戴关系，outcome panel 的 composition 必须写明使用 side/rear 或 rear three-quarter angle，能看见 receiver back plane 和接触线；不能使用 front screen view、straight-on display view、generic wider context 这类会遮挡接触面的角度。比如支架结果要表达为“从侧后方/后侧三分之二角度看到：产品底部磁吸面仍然 flush 贴合手机背面，产品机身从手机背面 protrudes outward；手机与产品是一体吸附组件，产品附着在手机背面并使手机横屏/横放形成观看角度”。不得写 phone rests against speaker、speaker supports phone、phone propped up by speaker、smartphone propped up by attached speaker、phone standing via speaker、speaker as kickstand、propped up by the product 这类含糊关系；不得把结果格画成手机压在产品上、产品垫在手机下、产品托住手机边缘、产品作为桌面底座。正确表达应始终是“attached assembly / phone back attachment remains visible / product is attached to the back of the phone and enables landscape viewing angle”。
6. 不要输出泛用生活方式图、纯人物手持展示、按钮美图、手机正面 app 操作图，除非合同明确要求。
7. 不要新增产品型号、材质、颜色、logo、按钮、接口或接触面。
8. 只返回 JSON，不要解释。""" % (
        json.dumps(facts, ensure_ascii=False, sort_keys=True),
        geometry or "none",
        json.dumps(relation, ensure_ascii=False, sort_keys=True),
        json.dumps(outcome_context, ensure_ascii=False),
        source_prompt[:6000],
    )


def _validate_asset_composition_brief(asset_item, brief):
    if not isinstance(brief, dict):
        raise ValueError("ASSET_COMPOSITION_INVALID_JSON")
    panel_plan = brief.get("panel_plan")
    if not isinstance(panel_plan, list) or len(panel_plan) != 9:
        raise ValueError("ASSET_COMPOSITION_PANEL_COUNT_INVALID")
    relation = asset_item.get("physical_relation_contract") or {}
    if relation:
        required_terms = [
            relation.get("active_object"),
            relation.get("receiver_object"),
            relation.get("product_contact_surface"),
            relation.get("receiver_contact_surface"),
        ]
        joined = json.dumps(brief, ensure_ascii=False).lower()
        missing = [term for term in required_terms if term and str(term).lower() not in joined]
        if missing:
            raise ValueError(
                "ASSET_COMPOSITION_SCOPE_DRIFT: missing contract terms %s" %
                ", ".join(map(str, missing)))
        relation_type = str(relation.get("relation_type") or "").lower()
        attachment_like = any(term in relation_type for term in (
            "attach", "mount", "dock", "wear", "clip", "clamp", "stand"))
        attachment_like = attachment_like or bool(asset_item.get("geometry_contract"))
        if attachment_like:
            product_surface = str(relation.get("product_contact_surface") or "").lower()
            receiver_surface = str(relation.get("receiver_contact_surface") or "").lower()
            final_state = str(relation.get("final_state") or "").lower()
            orientation_terms = ("protrud", "outward", "外凸", "突出", "朝外", "垂直")
            ambiguous_terms = ("站在", "立在", "放在", "搁在", "坐在",
                               "压在", "垫在", "托住边缘", "桌面底座",
                               "standing on", "resting on", "sitting on", "on top of",
                               "under the phone", "beneath the phone", "as a base",
                               "phone rests against speaker", "speaker supports phone",
                               "rests against the speaker", "supports the phone",
                               "phone propped up by speaker", "phone standing via speaker",
                               "phone standing by speaker", "speaker as kickstand",
                               "speaker as a kickstand", "propped up by attached",
                               "propped up by the product", "propped up by product",
                               "propped up by speaker", "standing via speaker")
            hidden_contact_angles = (
                "front screen", "front-screen", "straight-on display", "display view",
                "phone screen as main", "正面屏幕", "正面视角", "屏幕正面",
                "generic wider context")
            for panel_index in (4, 5, 7, 8, 9):
                panel = panel_plan[panel_index - 1]
                positive_text = json.dumps({
                    key: panel.get(key)
                    for key in ("shot_size", "composition", "must_show", "proof_goal")
                    if isinstance(panel, dict)
                }, ensure_ascii=False).lower()
                if any(term in positive_text for term in ambiguous_terms):
                    raise ValueError(
                        "ASSET_COMPOSITION_ATTACHMENT_AMBIGUOUS: panel %d describes standing/resting-on geometry" %
                        panel_index)
                if panel_index in (8, 9):
                    if any(term in positive_text for term in hidden_contact_angles):
                        raise ValueError(
                            "ASSET_COMPOSITION_OUTCOME_ANGLE_AMBIGUOUS: panel %d hides the attachment contact plane" %
                            panel_index)
                    if not any(term in positive_text for term in (
                            "side", "rear", "后侧", "侧后", "背面", "three-quarter", "3/4")):
                        raise ValueError(
                            "ASSET_COMPOSITION_OUTCOME_ANGLE_MISSING: panel %d must use side/rear contact-proof angle" %
                            panel_index)
                if product_surface and product_surface not in positive_text:
                    raise ValueError(
                        "ASSET_COMPOSITION_ATTACHMENT_PANEL_SCOPE_DRIFT: panel %d missing product contact surface" %
                        panel_index)
                if receiver_surface and receiver_surface not in positive_text:
                    raise ValueError(
                        "ASSET_COMPOSITION_ATTACHMENT_PANEL_SCOPE_DRIFT: panel %d missing receiver contact surface" %
                        panel_index)
                if final_state and not any(term in positive_text for term in orientation_terms):
                    raise ValueError(
                        "ASSET_COMPOSITION_ATTACHMENT_PANEL_AMBIGUOUS: panel %d missing outward/protruding orientation" %
                        panel_index)
    outcome_context = asset_item.get("usage_outcome_context") or []
    if outcome_context:
        outcome_text = json.dumps(outcome_context, ensure_ascii=False).lower()
        brief_text = json.dumps(brief, ensure_ascii=False).lower()
        tokens = []
        for term in ("横放", "桌面", "支撑", "观看", "landscape", "table", "desktop", "stand", "viewing"):
            if term in outcome_text:
                tokens.append(term)
        if tokens and not any(term in brief_text for term in tokens):
            raise ValueError(
                "ASSET_COMPOSITION_OUTCOME_MISSING: composition brief does not cover usage outcome")
        outcome_panels = brief.get("outcome_panels") or []
        if len(outcome_panels) < 2:
            raise ValueError(
                "ASSET_COMPOSITION_OUTCOME_PANEL_COUNT_INVALID")
    return True


def add_asset_composition_briefs(review, plan, api_key=None, model="qwen3.6-plus"):
    """Use a text model as a constrained composition-prompt skill."""
    key_setup.ensure_session_id()
    _validate_text_model(model)
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise ValueError("PROMPT_REVIEW_KEY_REQUIRED")
    review = dict(review)
    asset_prompts = []
    system = (
        "你是受限提示词 skill。你只输出 JSON 构图 brief；不得改变输入中的产品事实、"
        "物理关系、接触面、目标物、人物身份、台词或禁文字规则。"
    )
    for item in review.get("asset_prompts") or []:
        item = dict(item)
        if item.get("asset_id") == "product_usage_image" and item.get("physical_relation_contract"):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": _asset_composition_skill_request(plan, item)},
            ]
            last_error = None
            brief = None
            for attempt in range(_POLISH_JSON_RETRIES + 1):
                response = br_client.chat(api_key, messages, model=model, timeout=600)
                try:
                    candidate = _extract_json(response)
                    _validate_asset_composition_brief(item, candidate)
                    brief = candidate
                    break
                except ValueError as exc:
                    last_error = exc
                    if attempt < _POLISH_JSON_RETRIES:
                        messages = messages + [
                            {"role": "assistant", "content": str(response)},
                            {"role": "user", "content": (
                                "上一次输出不符合 schema 或遗漏合同字段：%s。"
                                "请只返回合法 JSON，保留合同里的 active object、receiver object、"
                                "product contact surface 和 receiver contact surface。"
                                % exc)},
                        ]
            if brief is None:
                raise ValueError(
                    "ASSET_COMPOSITION_SKILL_FAILED: %s" % last_error)
            item["composition_brief"] = brief
            item["composition_model"] = model
            item["composition_prompt_fingerprint"] = _digest({
                "asset_id": item.get("asset_id"),
                "policy_version": item.get("policy_version"),
                "physical_relation_contract": item.get("physical_relation_contract"),
                "geometry_contract": item.get("geometry_contract"),
                "usage_outcome_context": item.get("usage_outcome_context"),
                "composition_brief": brief,
            })
        asset_prompts.append(item)
    review["asset_prompts"] = asset_prompts
    review["asset_composition_model"] = model
    review["review_fingerprint"] = _digest(review)
    return review


def _director_skill_request(plan, item, stage):
    """Ask a text model for a constrained director brief.

    This is intentionally not a free rewrite. The model may improve staging,
    scene design, rhythm and continuity, but it cannot change the confirmed
    product facts, characters, dialogue, references or no-text policy.
    """
    facts = _product_facts(plan)
    continuity = _continuity_for_shot(plan, item.get("shot") or item)
    references = item.get("submission_references") or item.get("reference_tags") or []
    prompt = item.get("submission_prompt_zh") or item.get("prompt_zh") or ""
    if stage == "storyboard":
        schema = """{
  "narrative_function": "本镜头在整支片中的叙事任务",
  "scene_design": "场景、空间关系、主体层次",
  "shot_size": "景别",
  "camera_movement": "镜头运动",
  "composition": "构图策略",
  "lighting": "光线和质感",
  "action_beats": ["3-6 个动作节拍"],
  "transition_in": "承接上一镜",
  "transition_out": "交给下一镜",
  "product_value_proof": "如何用画面证明产品/服务卖点",
  "emotional_intent": "观众应感受到什么",
  "continuity_hooks": ["人物/产品/场景/光线/声音连续性钩子"],
  "reference_scope": "必须遵守哪些 @tag/imageUrls 参考",
  "must_preserve": ["不可改变的事实"],
  "must_exclude": ["禁止出现的漂移/文字/无关元素"]
}"""
        task = "把已确认剧本转成故事板导演 brief，用来增强 gpt-image-2 故事板提示词。"
    else:
        schema = """{
  "narrative_function": "本段视频在整支片中的叙事任务",
  "start_state": "0 秒起始画面和动作状态",
  "timeline_beats": ["按秒或动作顺序列出 3-6 个视频节拍"],
  "end_state": "结尾停留状态，方便下一段衔接",
  "dialogue_delivery": "台词语气、停顿、口型同步重点",
  "camera_motion": "镜头运动和景别变化",
  "action_continuity": "人物/产品/道具动作如何连续",
  "audio_continuity": {"voice":"声音连续性", "bgm":"BGM 连续性", "sfx":"音效连续性", "method":"哪些靠模型提示词，哪些应后期统一混音"},
  "edit_continuity": "与前后段剪辑如何衔接",
  "model_strategy": {"seedance":"使用原生故事板时如何执行", "kling":"fallback 单格展开图时如何执行"},
  "reference_priority": "imageUrls/@tag 的优先级和用途",
  "must_preserve": ["不可改变的事实"],
  "must_exclude": ["禁止出现的漂移/文字/无关元素"]
}"""
        task = "把已确认剧本和视频提交包转成视频导演 brief，用来增强 Seedance/Kling 提示词。"
    return """你是通用商业短视频导演 brief skill。%s

【硬性输入，不可改写】
产品/服务事实: %s
连续性合同: %s
本段/本镜头 item: %s
提交图片或 reference tags: %s

【当前完整提交提示词摘要】
%s

请只输出 JSON，字段固定如下：
%s

约束：
1. 只能补强叙事、场景、构图、镜头、动作、声音和剪辑连续性，不得改产品、人物、台词、剧情主旨、参考图含义。
2. 必须显式说明本段如何服务整支片主旨，以及如何与前后段连续。
3. 有台词时必须包含口播/旁白语气和口型同步要点；有 BGM/SFX 时必须说明连续性方法。
4. 背景画面不能生成字幕、文字、口号、标签、Logo、水印；文字动画/参数标签只能标记为后期层，不让视频/图片模型直接画。
5. Seedance 可以使用原生故事板/多图参考；Kling fallback 只能按单格展开图 + 素材图逐段执行，不能把多格故事板当作视频画面。
6. 只返回 JSON，不要解释。""" % (
        task,
        json.dumps(facts, ensure_ascii=False, sort_keys=True),
        json.dumps(continuity, ensure_ascii=False, sort_keys=True),
        json.dumps({k: v for k, v in item.items()
                    if k not in ("submission_prompt_zh", "model_submission_prompts",
                                 "fallback_submission_prompts")},
                   ensure_ascii=False, sort_keys=True)[:4000],
        json.dumps(references, ensure_ascii=False, sort_keys=True)[:3000],
        prompt[:4000],
        schema,
    )


def _validate_director_brief(item, brief, stage):
    if not isinstance(brief, dict):
        raise ValueError("DIRECTOR_BRIEF_INVALID_JSON")
    required = (
        ("narrative_function", "scene_design", "shot_size", "camera_movement",
         "composition", "lighting", "action_beats", "transition_in",
         "transition_out", "product_value_proof", "continuity_hooks",
         "reference_scope", "must_preserve", "must_exclude")
        if stage == "storyboard" else
        ("narrative_function", "start_state", "timeline_beats", "end_state",
         "dialogue_delivery", "camera_motion", "action_continuity",
         "audio_continuity", "edit_continuity", "model_strategy",
         "reference_priority", "must_preserve", "must_exclude")
    )
    missing = [key for key in required if brief.get(key) in (None, "", [], {})]
    if missing:
        raise ValueError("DIRECTOR_BRIEF_MISSING_FIELDS: %s" % ",".join(missing))
    beat_key = "action_beats" if stage == "storyboard" else "timeline_beats"
    if not isinstance(brief.get(beat_key), list) or len(brief.get(beat_key)) < 3:
        raise ValueError("DIRECTOR_BRIEF_BEATS_TOO_THIN")
    if stage == "video":
        audio = brief.get("audio_continuity")
        strategy = brief.get("model_strategy")
        if not isinstance(audio, dict) or not isinstance(strategy, dict):
            raise ValueError("DIRECTOR_BRIEF_VIDEO_CONTRACT_INVALID")
        if not (strategy.get("seedance") and strategy.get("kling")):
            raise ValueError("DIRECTOR_BRIEF_MODEL_STRATEGY_INCOMPLETE")
    text = json.dumps(brief, ensure_ascii=False)
    positive_brief = dict(brief)
    for key in ("must_exclude", "forbidden", "negative_prompt_zh"):
        positive_brief.pop(key, None)
    positive_text = json.dumps(positive_brief, ensure_ascii=False)
    if any(term in positive_text for term in ("改成另一个产品", "替换产品", "新增角色", "添加字幕", "画字幕")):
        raise ValueError("DIRECTOR_BRIEF_SCOPE_DRIFT")
    source_text = json.dumps({
        "source": item.get("source_prompt_zh"),
        "submission": item.get("submission_prompt_zh"),
        "prompt": item.get("prompt_zh"),
    }, ensure_ascii=False).lower()
    lowered = text.lower()
    if ("手机背面" in source_text or "smartphone back" in source_text or
            "receiver back plane" in source_text):
        if not any(term in lowered for term in (
                "手机背面", "smartphone back", "receiver back plane", "phone back")):
            raise ValueError("DIRECTOR_BRIEF_KEY_OBJECT_DRIFT: missing phone-back relation")
    if ("底部" in source_text or "bottom/base" in source_text or
            "product contact surface" in source_text):
        if not any(term in lowered for term in (
                "底部", "bottom/base", "product contact surface", "bottom magnetic")):
            raise ValueError("DIRECTOR_BRIEF_KEY_SURFACE_DRIFT: missing product contact surface")
    return True


def _augment_director_key_relations(item, brief):
    """Carry CIL-level object/surface locks into model-generated briefs.

    A director model is good at rhythm and staging, but product-use contact
    surfaces are deterministic constraints. Preserve them as explicit
    must-preserve locks before validation.
    """
    source_text = json.dumps({
        "source": item.get("source_prompt_zh"),
        "submission": item.get("submission_prompt_zh"),
        "prompt": item.get("prompt_zh"),
    }, ensure_ascii=False).lower()
    brief = json.loads(json.dumps(brief, ensure_ascii=False))
    locks = []
    if ("手机背面" in source_text or "smartphone back" in source_text or
            "receiver back plane" in source_text):
        locks.append("关键物理关系：产品必须连接/贴合在手机背面 receiver back plane / phone back，不得泛化为桌面、手机屏幕或其它配件关系。")
    if ("底部" in source_text or "bottom/base" in source_text or
            "product contact surface" in source_text):
        locks.append("关键接触面：必须保留 product contact surface = bottom/base magnetic surface，产品底部/底座磁吸面是唯一允许接触手机背面的产品面。")
    if not locks:
        return brief
    existing = brief.get("must_preserve")
    if not isinstance(existing, list):
        existing = [existing] if existing else []
    for lock in locks:
        if lock not in existing:
            existing.append(lock)
    brief["must_preserve"] = existing
    scope = brief.get("reference_scope")
    scope_text = json.dumps(scope, ensure_ascii=False) if isinstance(scope, (list, dict)) else str(scope or "")
    cil_note = "CIL locks: " + " ".join(locks)
    if cil_note not in scope_text:
        brief["reference_scope"] = (scope_text + "\n" + cil_note).strip()
    return brief


def _director_brief_block(stage, brief):
    title = ("MODEL-GENERATED STORYBOARD DIRECTOR BRIEF"
             if stage == "storyboard" else
             "MODEL-GENERATED VIDEO DIRECTOR BRIEF")
    if stage == "storyboard":
        lines = [
            "[%s / 已审核导演设计]" % title,
            "Narrative function: %s" % brief.get("narrative_function"),
            "Scene design: %s" % brief.get("scene_design"),
            "Shot/composition: %s; %s; %s" % (
                brief.get("shot_size"), brief.get("camera_movement"), brief.get("composition")),
            "Lighting: %s" % brief.get("lighting"),
            "Action beats: %s" % " / ".join(map(str, brief.get("action_beats") or [])),
            "Transition: in=%s; out=%s" % (brief.get("transition_in"), brief.get("transition_out")),
            "Value proof: %s" % brief.get("product_value_proof"),
            "Continuity hooks: %s" % " / ".join(map(str, brief.get("continuity_hooks") or [])),
            "Reference scope: %s" % brief.get("reference_scope"),
            "Must preserve: %s" % " / ".join(map(str, brief.get("must_preserve") or [])),
            "Must exclude: %s" % " / ".join(map(str, brief.get("must_exclude") or [])),
        ]
    else:
        audio = brief.get("audio_continuity") or {}
        strategy = brief.get("model_strategy") or {}
        lines = [
            "[%s / 已审核视频导演设计]" % title,
            "Narrative function: %s" % brief.get("narrative_function"),
            "Start state: %s" % brief.get("start_state"),
            "Timeline beats: %s" % " / ".join(map(str, brief.get("timeline_beats") or [])),
            "End state: %s" % brief.get("end_state"),
            "Dialogue delivery: %s" % brief.get("dialogue_delivery"),
            "Camera motion: %s" % brief.get("camera_motion"),
            "Action continuity: %s" % brief.get("action_continuity"),
            "Audio continuity: voice=%s; bgm=%s; sfx=%s; method=%s" % (
                audio.get("voice"), audio.get("bgm"), audio.get("sfx"), audio.get("method")),
            "Edit continuity: %s" % brief.get("edit_continuity"),
            "Model strategy: seedance=%s; kling=%s" % (
                strategy.get("seedance"), strategy.get("kling")),
            "Reference priority: %s" % brief.get("reference_priority"),
            "Must preserve: %s" % " / ".join(map(str, brief.get("must_preserve") or [])),
            "Must exclude: %s" % " / ".join(map(str, brief.get("must_exclude") or [])),
        ]
    return "\n".join(line for line in lines if line and not line.endswith(": None"))


def _append_director_brief(item, brief, stage):
    item = dict(item)
    block = "\n\n" + _director_brief_block(stage, brief)
    for key in ("prompt_zh", "submission_prompt_zh", "source_prompt_zh"):
        if item.get(key):
            item[key] = str(item[key]).rstrip() + block
    if stage == "video":
        model_prompts = {}
        for key, value in (item.get("model_submission_prompts") or {}).items():
            model_prompts[key] = str(value).rstrip() + block
        if model_prompts:
            item["model_submission_prompts"] = model_prompts
        fallbacks = []
        for fallback in item.get("fallback_submission_prompts") or []:
            fallback = dict(fallback)
            if fallback.get("submission_prompt_zh"):
                fallback["submission_prompt_zh"] = str(fallback["submission_prompt_zh"]).rstrip() + block
            if fallback.get("prompt_zh"):
                fallback["prompt_zh"] = str(fallback["prompt_zh"]).rstrip() + block
            fallbacks.append(fallback)
        if fallbacks:
            item["fallback_submission_prompts"] = fallbacks
    item["director_brief"] = brief
    item["director_brief_fingerprint"] = _digest(brief)
    return item


def add_director_briefs(review, plan, api_key=None, model="qwen3.6-plus"):
    """Use a text model as a constrained storyboard/video director skill."""
    key_setup.ensure_session_id()
    _validate_text_model(model)
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise ValueError("PROMPT_REVIEW_KEY_REQUIRED")
    stage = review.get("stage")
    if stage not in ("storyboard", "video"):
        raise ValueError("DIRECTOR_BRIEF_STAGE_UNSUPPORTED: %s" % stage)
    system = (
        "你是受限商业短视频导演 skill。你只输出 JSON brief；不得改变输入中的产品事实、"
        "人物身份、台词、剧情、参考图含义或禁文字规则。"
    )
    prompts = []
    for item in review.get("prompts") or []:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _director_skill_request(plan, item, stage)},
        ]
        last_error = None
        brief = None
        for attempt in range(_POLISH_JSON_RETRIES + 1):
            response = br_client.chat(api_key, messages, model=model, timeout=600)
            try:
                candidate = _extract_json(response)
                candidate = _augment_director_key_relations(item, candidate)
                _validate_director_brief(item, candidate, stage)
                brief = candidate
                break
            except ValueError as exc:
                last_error = exc
                if attempt < _POLISH_JSON_RETRIES:
                    messages = messages + [
                        {"role": "assistant", "content": str(response)},
                        {"role": "user", "content": (
                            "上一次输出不符合导演 brief schema 或改变了确认内容：%s。"
                            "请只返回合法 JSON，补齐所有必填字段，不要解释。" % exc)},
                    ]
        if brief is None:
            raise ValueError("DIRECTOR_BRIEF_SKILL_FAILED: shot=%s: %s" % (
                item.get("shot_id") or "unknown", last_error))
        item = _append_director_brief(item, brief, stage)
        item["director_model"] = model
        prompts.append(item)
    review = dict(review)
    review["prompts"] = prompts
    review["director_model"] = model
    review["review_fingerprint"] = _digest(review)
    return review


def polish(plan, stage, api_key=None, model="qwen3.6-plus"):
    key_setup.ensure_session_id()
    _validate_text_model(model)
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise ValueError("PROMPT_REVIEW_KEY_REQUIRED")
    import storyboard
    plan = storyboard.expand_product_sku_refs(storyboard.canonical_storyboard_plan(plan))
    system = ("你只负责提示词工程，不改变客户已确认的产品事实、颜色、规格、人物身份、台词和剧情。"
              "所有生成模型画面禁止字幕、口号、标签、Logo和水印。必须严格返回 JSON。"
              "严格遵守 JSON 语法：字符串值内部如果需要引用台词原文或强调某个词，"
              "只能使用中文引号「」或『』，绝对不能使用英文直引号 \" ；"
              "如果必须使用英文引号，必须写成转义形式 \\\" ，否则 JSON 会解析失败。")
    polished = []
    for item in build_prompt_requests(plan, stage):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": item["source_prompt_zh"]}]
        last_error = None
        result = None
        for attempt in range(_POLISH_JSON_RETRIES + 1):
            response = br_client.chat(api_key, messages, model=model, timeout=600)
            try:
                candidate = _extract_json(response)
                validate_polished_prompt(plan, item, candidate)
                result = candidate
                break
            except ValueError as exc:
                last_error = exc
                if attempt < _POLISH_JSON_RETRIES:
                    messages = messages + [
                        {"role": "assistant", "content": str(response)},
                        {"role": "user", "content": (
                            "上一次返回不符合要求：%s 。请重新输出同样内容的 JSON，"
                            "但字符串值内部绝不能出现未转义的英文直引号 \" ；"
                            "需要引用台词或强调时改用中文引号「」，"
                            "或将英文引号转义为 \\\" 。只返回 JSON，不要添加解释。"
                            % exc)},
                    ]
        if result is None:
            raise ValueError(
                "PROMPT_REVIEW_INVALID_JSON: 镜头 %s 重试 %d 次后仍无法解析大模型返回的 JSON：%s" %
                (item["shot_id"], _POLISH_JSON_RETRIES + 1, last_error))
        result.update({"shot_id": item["shot_id"], "stage": stage,
                       "source_prompt_zh": item["source_prompt_zh"]})
        polished.append(result)
    review = {"status": "pending", "stage": stage, "model": model,
            "plan_fingerprint": plan_fingerprint(plan),
            "visual_plan_fingerprint": visual_plan_fingerprint(plan),
            "prompts": polished,
            "created_at": datetime.now().isoformat(timespec="seconds")}
    # Storyboard rendering validates both shot prompts and the non-shot assets
    # it may need to create (product board, cast board, usage image). Keep both
    # contracts in the same review artifact so the documented polish -> confirm
    # workflow cannot produce a review that is confirmed but unusable.
    if stage == "storyboard":
        review["asset_prompts"] = storyboard.asset_prompt_review_items(plan)
    return review


def save_pending(review, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    review = dict(review)
    review["review_fingerprint"] = _digest(review)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    return review


def capture_video_segments(plan, segments, model="seedance-2.0"):
    """Capture deterministic model-ready prompts without rewriting content."""
    import video_engine
    prompts = []
    for segment in segments:
        target_model = segment.get("model") or model
        base_prompt = video_engine._compile_seedance_text(segment, target_model)
        preview_segment = dict(segment, approved_prompt_zh=base_prompt)
        storyboard_ref = bool(
            segment.get("storyboard_ref") or segment.get("storyboard_ref_mode") or
            segment.get("use_storyboard_reference"))
        submission_prompt = video_engine._submission_text(
            preview_segment, target_model, storyboard_ref=storyboard_ref)
        model_submission_prompts = {str(target_model): submission_prompt}
        if video_engine.seedance_prompt.is_seedance_model(target_model):
            model_submission_prompts["seedance"] = submission_prompt
        fallback_submission_prompts = []
        if video_engine.seedance_prompt.is_seedance_model(target_model):
            fallback_model = "kling-v3-omni-video"
            fallback_base = video_engine._compile_seedance_text(segment, fallback_model)
            fallback_segment = dict(segment, approved_prompt_zh=fallback_base)
            fallback_prompt = video_engine._submission_text(
                fallback_segment, fallback_model, storyboard_ref=storyboard_ref)
            model_submission_prompts[fallback_model] = fallback_prompt
            model_submission_prompts["kling"] = fallback_prompt
            fallback_submission_prompts.append({
                "model": fallback_model,
                "prompt_zh": fallback_base,
                "submission_prompt_zh": fallback_prompt,
                "fallback_reason": "seedance_unavailable_or_privacy",
            })
        prompts.append({
            "shot_id": segment.get("id"),
            "stage": "video",
            "prompt_zh": base_prompt,
            "submission_prompt_zh": submission_prompt,
            "submission_references": _submission_references(segment),
            "storyboard_ref_mode": segment.get("storyboard_ref_mode"),
            "storyboard_panel_index": segment.get("storyboard_panel_index"),
            "model_submission_prompts": model_submission_prompts,
            "fallback_submission_prompts": fallback_submission_prompts,
            "negative_prompt_zh": segment.get("negative_prompt") or video_engine.DEFAULT_NEGATIVE,
            "source_prompt_zh": segment.get("text") or "",
            "model": target_model,
        })
    return {
        "status": "pending",
        "stage": "video",
        "model": "deterministic-segment-compiler",
        "plan_fingerprint": plan_fingerprint(plan),
        "visual_plan_fingerprint": visual_plan_fingerprint(plan),
        "prompts": prompts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _submission_references(segment):
    """Return the image references that will be carried as imageUrls.

    The provider receives image inputs separately from the text prompt. Keeping
    this list in the prompt-review artifact makes the confirmation gate cover
    the whole intended submission packet, not just the textual instructions.
    """
    refs = []
    for idx, ref in enumerate(segment.get("references") or [], 1):
        if not isinstance(ref, dict) or not ref.get("url"):
            continue
        refs.append({
            "index": ref.get("index") or idx,
            "id": ref.get("id") or "ref_%02d" % idx,
            "tag": ref.get("tag") or "",
            "type": ref.get("type") or "",
            "label": ref.get("label") or "",
            "role": ref.get("role") or "",
            "intent": ref.get("intent") or "",
            "url": ref.get("url"),
        })
    known_urls = {item["url"] for item in refs}
    for url in segment.get("urls") or []:
        if url in known_urls:
            continue
        refs.append({
            "index": len(refs) + 1,
            "id": "url_%02d" % (len(refs) + 1),
            "tag": "",
            "type": "image_url",
            "label": "未标注图片输入",
            "role": "随视频提交的 imageUrls 输入",
            "intent": "未在 references 中登记，需人工确认语义。",
            "url": url,
        })
    return refs


def _storyboard_bw(plan):
    color_mode = str(plan.get("color_mode") or "bw").lower()
    return color_mode in ("bw", "black_white", "grayscale", "mono", "黑白")


def capture_storyboard_prompts(plan, model="gpt-image-2"):
    """Capture deterministic gpt-image-ready storyboard prompts.

    This does not call a text model. It records the exact contact-sheet prompt
    that storyboard.py will submit once the review is confirmed.
    """
    import storyboard
    canonical = storyboard.expand_product_sku_refs(
        storyboard.canonical_storyboard_plan(plan))
    registry = storyboard.build_reference_registry(canonical)
    storyboard._validate_reference_registry(canonical, registry)
    bw = _storyboard_bw(canonical)
    prompts = []
    for index, shot in enumerate(canonical.get("shots") or [], 1):
        shot_registry = storyboard.shot_reference_registry(registry, shot)
        shot_plan = dict(canonical, shots=[shot])
        submission_prompt = storyboard.contact_sheet_prompt(
            shot_plan, bw=bw, reference_registry=shot_registry)
        prompts.append({
            "shot_id": shot.get("id") or str(index),
            "stage": "storyboard",
            "prompt_zh": "非付费捕获：使用下方完整提交提示词生成该镜头故事板。",
            "submission_prompt_zh": submission_prompt,
            "negative_prompt_zh": "画面文字、字幕、Logo、水印、错误产品形态、错误磁吸关系",
            "source_prompt_zh": submission_prompt,
            "model": model,
            "reference_tags": [item.get("tag") for item in shot_registry],
        })
    asset_prompts = storyboard.asset_prompt_review_items(canonical)
    return {
        "status": "pending",
        "stage": "storyboard",
        "model": "deterministic-storyboard-compiler",
        "target_model": model,
        "plan_fingerprint": plan_fingerprint(canonical),
        "visual_plan_fingerprint": visual_plan_fingerprint(canonical),
        "asset_prompts": asset_prompts,
        "prompts": prompts,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _validate_review_before_confirm(review):
    stage = review.get("stage")
    for item in review.get("asset_prompts") or []:
        if isinstance(item, dict) and item.get("composition_brief"):
            _validate_asset_composition_brief(item, item.get("composition_brief"))
    for item in review.get("prompts") or []:
        if isinstance(item, dict) and item.get("director_brief"):
            _validate_director_brief(item, item.get("director_brief"), stage)
    return True


def confirm(path):
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    if review.get("status") != "pending" or not review.get("prompts"):
        raise ValueError("PROMPT_REVIEW_CONFIRM_BLOCKED")
    _validate_review_before_confirm(review)
    review["status"] = "confirmed"
    review["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    review["review_fingerprint"] = _digest(review)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    return review


def invalidate(path, reason=None):
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    if review.get("status") != "pending":
        raise ValueError("PROMPT_REVIEW_INVALIDATE_BLOCKED")
    review["status"] = "invalidated"
    review["invalidated_at"] = datetime.now().isoformat(timespec="seconds")
    review["invalidation_reason"] = reason or "superseded_or_failed_preconfirm_validation"
    review["review_fingerprint"] = _digest(review)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    return review


def preview(path, out=None):
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    prompts = review.get("prompts") or []
    status = review.get("status", "unknown")
    lines = [
        "# 提示词审核预览",
        "",
        "- 状态: `%s`" % status,
        "- 阶段: `%s`" % review.get("stage", "unknown"),
        "- 模型: `%s`" % review.get("model", "unknown"),
        "- 镜头数: `%d`" % len(prompts),
        "- 审核指纹: `%s`" % review.get("review_fingerprint", ""),
        "",
    ]
    if status == "pending":
        lines.extend([
            "确认命令:",
            "",
            "```bash",
            "python3 scripts/prompt_review.py confirm --review %s" % path,
            "```",
            "",
        ])
    elif status == "invalidated":
        lines.extend([
            "此审核稿已作废，不可确认。",
            "",
            "- 作废原因: `%s`" % (review.get("invalidation_reason") or ""),
            "",
        ])
    elif status == "confirmed":
        lines.extend([
            "此审核稿已确认。",
            "",
            "- 确认时间: `%s`" % (review.get("confirmed_at") or ""),
            "",
        ])
    for index, item in enumerate(prompts, 1):
        prompt = str(item.get("submission_prompt_zh") or item.get("prompt_zh") or "").strip()
        negative = str(item.get("negative_prompt_zh") or "").strip()
        notes = item.get("continuity_notes") or []
        lines.extend([
            "## %d. %s" % (index, item.get("shot_id") or "unknown"),
            "",
        ])
        if item.get("director_brief"):
            lines.extend([
                "模型导演 brief / Director Skill:",
                "",
                "```json",
                json.dumps(item.get("director_brief"), ensure_ascii=False, indent=2),
                "```",
                "",
            ])
        references = item.get("submission_references") or []
        if references:
            lines.extend([
                "提交图片 / imageUrls:",
                "",
                "- storyboard_ref_mode: `%s`, panel_index: `%s`" %
                (item.get("storyboard_ref_mode") or "",
                 item.get("storyboard_panel_index") or ""),
            ])
            for ref in references:
                lines.append(
                    "- #%s `%s` `%s` %s | role=%s | intent=%s | url=%s" %
                    (ref.get("index") or "",
                     ref.get("tag") or ref.get("id") or "",
                     ref.get("type") or "",
                     ref.get("label") or "",
                     ref.get("role") or "",
                     ref.get("intent") or "",
                     ref.get("url") or "")
                )
            lines.append("")
        lines.extend([
            "完整提交提示词（主模型 %s）:" % (item.get("model") or "unknown"),
            "",
            prompt,
            "",
        ])
        for fallback in item.get("fallback_submission_prompts") or []:
            fallback_prompt = str(fallback.get("submission_prompt_zh") or "").strip()
            if fallback_prompt:
                lines.extend([
                    "Fallback 完整提交提示词（%s）:" % (fallback.get("model") or "unknown"),
                    "",
                    fallback_prompt,
                    "",
                ])
        if notes:
            lines.extend(["连续性要点:", ""])
            for note in notes[:4]:
                lines.append("- %s" % note)
            lines.append("")
        if negative:
            lines.extend([
                "负向约束摘要:",
                "",
                negative[:300] + ("..." if len(negative) > 300 else ""),
                "",
            ])
    asset_prompts = review.get("asset_prompts") or []
    if asset_prompts:
        lines.extend(["## 资产级生图提示词 / Asset Prompts", ""])
        for item in asset_prompts:
            prompt = str(item.get("submission_prompt_zh") or "").strip()
            lines.extend([
                "### %s" % (item.get("asset_id") or item.get("kind") or "asset"),
                "",
                "- policy: `%s`" % (item.get("policy_version") or ""),
                "- prompt_fingerprint: `%s`" % (item.get("prompt_fingerprint") or ""),
                "",
                prompt,
                "",
            ])
            if item.get("composition_brief"):
                lines.extend([
                    "模型构图 brief / Composition Skill:",
                    "",
                    "```json",
                    json.dumps(item.get("composition_brief"), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ])
            if item.get("negative_prompt_zh"):
                lines.extend(["负向约束摘要:", "", str(item.get("negative_prompt_zh")), ""])
    text = "\n".join(lines).rstrip() + "\n"
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


def _review_saved_summary(review, review_path, preview_path=None, preview_text=None):
    payload = {
        "ok": True,
        "review": os.path.abspath(review_path),
        "status": review.get("status"),
        "stage": review.get("stage"),
        "model": review.get("model"),
        "prompt_count": len(review.get("prompts") or []),
    }
    if preview_path:
        payload["preview"] = os.path.abspath(preview_path)
        if preview_text is not None:
            payload["preview_bytes"] = len(preview_text.encode("utf-8"))
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("polish")
    p.add_argument("--plan", required=True)
    p.add_argument("--stage", choices=("storyboard", "video"), required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="qwen3.6-plus")
    cap = sub.add_parser("capture-video")
    cap.add_argument("--plan", required=True)
    cap.add_argument("--segments", required=True)
    cap.add_argument("--out", required=True)
    cap.add_argument("--preview-out")
    cap.add_argument("--model", default="seedance-2.0")
    cap.add_argument("--director-model", default=None,
                     help="可选：用文字模型生成受限视频导演 brief，并注入提交提示词")
    cs = sub.add_parser("capture-storyboard")
    cs.add_argument("--plan", required=True)
    cs.add_argument("--out", required=True)
    cs.add_argument("--preview-out")
    cs.add_argument("--model", default="gpt-image-2")
    cs.add_argument("--composition-model", default=None,
                    help="可选：用文字模型为资产级产品使用图生成受限构图 brief")
    cs.add_argument("--director-model", default=None,
                    help="可选：用文字模型生成受限故事板导演 brief，并注入提交提示词")
    c = sub.add_parser("confirm")
    c.add_argument("--review", "--file", dest="review", required=True)
    inv = sub.add_parser("invalidate")
    inv.add_argument("--review", "--file", dest="review", required=True)
    inv.add_argument("--reason", default=None)
    pv = sub.add_parser("preview")
    pv.add_argument("--review", "--file", dest="review", required=True)
    pv.add_argument("--out")
    args = parser.parse_args(argv)
    if args.cmd == "polish":
        with open(args.plan, encoding="utf-8") as handle:
            plan = json.load(handle)
        print(json.dumps(save_pending(polish(plan, args.stage, model=args.model), args.out),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "capture-video":
        with open(args.plan, encoding="utf-8") as handle:
            plan = json.load(handle)
        with open(args.segments, encoding="utf-8") as handle:
            payload = json.load(handle)
        segments = payload.get("segments") if isinstance(payload, dict) else payload
        review = capture_video_segments(plan, segments or [], model=args.model)
        if args.director_model:
            review = add_director_briefs(review, plan, model=args.director_model)
        review = save_pending(review, args.out)
        preview_text = None
        if args.preview_out:
            preview_text = preview(args.out, out=args.preview_out)
            print(json.dumps(
                _review_saved_summary(review, args.out, args.preview_out, preview_text),
                ensure_ascii=False))
        else:
            print(json.dumps(review, ensure_ascii=False, indent=2))
    elif args.cmd == "capture-storyboard":
        with open(args.plan, encoding="utf-8") as handle:
            plan = json.load(handle)
        review = capture_storyboard_prompts(plan, model=args.model)
        if args.composition_model:
            review = add_asset_composition_briefs(
                review, plan, model=args.composition_model)
        if args.director_model:
            review = add_director_briefs(review, plan, model=args.director_model)
        review = save_pending(review, args.out)
        preview_text = None
        if args.preview_out:
            preview_text = preview(args.out, out=args.preview_out)
            print(json.dumps(
                _review_saved_summary(review, args.out, args.preview_out, preview_text),
                ensure_ascii=False))
        else:
            print(json.dumps(review, ensure_ascii=False, indent=2))
    elif args.cmd == "confirm":
        confirmed = confirm(args.review)
        print(json.dumps(
            _review_saved_summary(confirmed, args.review),
            ensure_ascii=False))
    elif args.cmd == "invalidate":
        invalidated = invalidate(args.review, reason=args.reason)
        print(json.dumps(
            _review_saved_summary(invalidated, args.review),
            ensure_ascii=False))
    else:
        text = preview(args.review, out=args.out)
        if args.out:
            print(json.dumps({
                "ok": True,
                "preview": os.path.abspath(args.out),
                "bytes": len(text.encode("utf-8")),
            }, ensure_ascii=False))
        else:
            print(text)


if __name__ == "__main__":
    main()
