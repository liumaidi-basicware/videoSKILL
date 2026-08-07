#!/usr/bin/env python3
"""导演级动效设计规划器 — 在故事板阶段就规划好字幕/动效/安全区。

核心理念（资深导演视角）：
  字幕动效不是后期补救，而是前期预演的一部分。导演在写剧本时就应该决定：
  「这里画面上要叠什么字、什么时候出现、怎么动、放在画面哪个位置」。
  这个决策必须同时影响：
    ① 视频生成 prompt（告诉模型在安全区不要放关键动作/主体）
    ② 故事板生成 prompt（在构图中预留文字叠加空间）
    ③ 后期字幕渲染（直接使用预规划的文本/时机/样式，而非逆向猜测）

工作流：
  1. design_from_plan(storyboard_plan) → motion_design.json
     读取定稿 storyboard_plan.json，用 LLM 生成逐镜动效设计规范。
  2. inject_safe_zones(prompt_text, shot_design) → prompt_text
     把安全区指令注入视频生成 prompt。
  3. storyboard_safe_zone_prompt(shot_design) → str
     生成故事板构图的安全区提示。

CLI:
  design --plan output/storyboard_plan.json --out output/motion_design.json
  inject --plan output/storyboard_plan.json --design output/motion_design.json
"""
import os
import re
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import br_client  # noqa: E402
import key_setup  # noqa: E402

# ── 安全区定义 ──────────────────────────────────────────────────────
# 每个安全区对应画面的一块区域，视频生成时模型应避免在此区域放置关键动作/主体。
# 后期字幕/动效会精确叠加在这些区域。

SAFE_ZONES = {
    "lower_third": {
        "description": "画面下方 25% 区域",
        "prompt_hint": "Keep the lower 25% of the frame clear of key action, faces, and product details; this space is reserved for subtitle overlay.",
        "storyboard_hint": "构图时下方留 25% 空白区，不放置关键主体、人脸或产品细节",
        "video_region": {"bottom_pct": 25},
    },
    "upper_third": {
        "description": "画面上方 20% 区域",
        "prompt_hint": "Keep the upper 20% of the frame clear of key action and faces; this space is reserved for title overlay.",
        "storyboard_hint": "构图时上方留 20% 空白区，不放置关键主体或人脸",
        "video_region": {"top_pct": 20},
    },
    "center": {
        "description": "画面中央区域",
        "prompt_hint": "Keep the center of the frame clear for a title reveal; position key subjects slightly off-center.",
        "storyboard_hint": "构图时中央留白，主体偏左或偏右",
        "video_region": {"center": True},
    },
    "corner": {
        "description": "画面右上角区域",
        "prompt_hint": "Keep the upper-right corner clear; a small data card will appear there.",
        "storyboard_hint": "构图时右上角留空，不放置关键元素",
        "video_region": {"corner": "upper_right"},
    },
    "left": {
        "description": "画面左侧 30% 区域",
        "prompt_hint": "Keep the left 30% of the frame clear; text content will appear on the left side.",
        "storyboard_hint": "构图时左侧留 30% 空白",
        "video_region": {"left_pct": 30},
    },
    "right": {
        "description": "画面右侧 30% 区域",
        "prompt_hint": "Keep the right 30% of the frame clear; text content will appear on the right side.",
        "storyboard_hint": "构图时右侧留 30% 空白",
        "video_region": {"right_pct": 30},
    },
}

# ── 动效类型与安全区映射 ─────────────────────────────────────────────
# 每种动效类型默认使用哪个安全区。
STYLE_DEFAULT_ZONE = {
    "title_reveal": "center",
    "bullet_list": "left",
    "metric_pop": "corner",
    "data_card": "corner",
    "lower_third": "lower_third",
    "keyword_flash": "center",
}

# ── LLM 动效设计提示词 ───────────────────────────────────────────────

DESIGN_PROMPT = """你是一位资深广告导演和动态图形设计师。请根据以下分镜脚本，为每个镜头规划「字幕与动效设计方案」。

【核心原则】
1. 字幕和动效必须与画面内容融为一体，不是后期贴上去的
2. 每个镜头的构图必须预留文字叠加的安全区
3. 字幕文本要精炼有力，不是台词的简单重复
4. 动效要服务叙事节奏，不是越多越好

【输入】分镜脚本（JSON）：
{plan_json}

【输出格式】严格输出 JSON（不要其他文字）：
{{
  "version": 1,
  "global_style": {{
    "font_family": "品牌字体或默认",
    "primary_color": "#RRGGBB",
    "subtitle_position": "lower_third",
    "motion_density": "low|medium|high"
  }},
  "shots": [
    {{
      "shot_id": "s1",
      "subtitle": {{
        "text": "精炼字幕文本（≤20字，无标点）",
        "position": "lower_third",
        "style": "subtitle",
        "safe_zone": "lower_third",
        "typography": {{
          "role": "spoken|hook|proof|cta",
          "size_px": 52,
          "max_width_px": 1220,
          "max_lines": 2,
          "emphasis": ["需要强调的关键词"],
          "preset": "fade_up|pop|slide_left|slide_right|none"
        }}
      }},
      "motion_overlay": {{
        "style": "title_reveal|bullet_list|metric_pop|data_card|lower_third|keyword_flash|none",
        "title": "动效标题（如有）",
        "bullets": ["要点1", "要点2"],
        "metric": {{"value": "数值", "label": "标签"}},
        "position": "center|top|bottom|lower_third|left|right|corner",
        "safe_zone": "center|upper_third|lower_third|corner|left|right",
        "timing": "进场0.5s后|全程|结尾强调",
        "preset": "pop|slide_left|slide_right|fade_up|none",
        "size_px": 42,
        "width_px": 560,
        "reason": "为什么这里需要这个动效（一句话）"
      }},
      "video_safe_zones": ["lower_third"],
      "director_note": "导演备注：这个镜头的动效如何服务叙事"
    }}
  ]
}}

【硬性要求】
- subtitle.text 不超过 20 字，去掉语气词和标点
- 每个镜头最多 1 个字幕 + 1 个动效
- 主字幕不是每镜头固定同一个字号：hook/cta 可放大，长台词必须缩小或分两行；字号必须在 38–72px（基于1920x1080）
- 主字幕 max_width_px 必须在 680–1320px，禁止横贯全屏；卖点卡 width_px 必须在 320–620px
- 相邻镜头不要连续使用同一个 preset；字幕与卖点卡应使用不同 track_index，避免互相遮挡
- preset、字号、宽度和强调词必须能直接用于后期渲染，不能只写导演描述
- 不需要动效的镜头 style 填 "none"
- 口播镜头必须有 subtitle，非口播镜头可以没有
- 数据类镜头（有 metric）优先用 data_card
- 安全区必须与动效位置匹配"""


def _validate_design(design, plan_shots):
    """Validate the LLM-generated motion design against the storyboard plan."""
    if not isinstance(design, dict):
        raise ValueError("MOTION_DESIGN_INVALID: 设计必须是对象")
    shots = design.get("shots") or []
    plan_ids = {str(s.get("id")) for s in plan_shots}
    design_ids = {str(s.get("shot_id")) for s in shots}
    missing = plan_ids - design_ids
    if missing:
        raise ValueError("MOTION_DESIGN_INCOMPLETE: 缺少镜头的动效设计: %s" % ", ".join(sorted(missing)))
    for shot in shots:
        sid = shot.get("shot_id")
        sub = shot.get("subtitle") or {}
        overlay = shot.get("motion_overlay") or {}
        typography = sub.get("typography") or {}
        if typography:
            size = typography.get("size_px", 52)
            width = typography.get("max_width_px", 1100)
            if not isinstance(size, (int, float)) or not 38 <= size <= 72:
                raise ValueError("MOTION_DESIGN_INVALID: subtitle.typography.size_px 必须在38-72px")
            if not isinstance(width, (int, float)) or not 680 <= width <= 1320:
                raise ValueError("MOTION_DESIGN_INVALID: subtitle.typography.max_width_px 必须在680-1320px")
            typography["size_px"] = int(size)
            typography["max_width_px"] = int(width)
            typography["max_lines"] = max(1, min(2, int(typography.get("max_lines", 2))))
            if typography.get("preset") not in (None, "fade_up", "pop", "slide_left", "slide_right", "none"):
                raise ValueError("MOTION_DESIGN_INVALID: subtitle.typography.preset 不受支持")
        if overlay.get("size_px") is not None:
            size = overlay.get("size_px")
            if not isinstance(size, (int, float)) or not 30 <= size <= 56:
                raise ValueError("MOTION_DESIGN_INVALID: motion_overlay.size_px 必须在30-56px")
            overlay["size_px"] = int(size)
        if overlay.get("width_px") is not None:
            width = overlay.get("width_px")
            if not isinstance(width, (int, float)) or not 320 <= width <= 620:
                raise ValueError("MOTION_DESIGN_INVALID: motion_overlay.width_px 必须在320-620px")
            overlay["width_px"] = int(width)
        if overlay.get("preset") not in (None, "pop", "slide_left", "slide_right", "fade_up", "none"):
            raise ValueError("MOTION_DESIGN_INVALID: motion_overlay.preset 不受支持")
        # Validate subtitle
        if sub.get("text") and len(sub["text"]) > 30:
            sub["text"] = sub["text"][:30]
        # Validate safe zones
        for zone in shot.get("video_safe_zones") or []:
            if zone not in SAFE_ZONES:
                shot["video_safe_zones"] = [z for z in shot["video_safe_zones"] if z in SAFE_ZONES]
                break
        # Validate overlay style
        style = overlay.get("style", "none")
        if style not in ("title_reveal", "bullet_list", "metric_pop", "data_card",
                         "lower_third", "keyword_flash", "none"):
            overlay["style"] = "none"
    return design


def design_from_plan(plan, api_key=None, model=None, require_llm=False):
    """从定稿 storyboard_plan.json 生成 motion_design.json。

    使用 BasicRouter LLM 分析每镜内容，生成统一的动效设计规范。
    返回 design dict（含 global_style + shots[]）。
    """
    shots = plan.get("shots") or []
    if not shots:
        raise ValueError("MOTION_DESIGN_NO_SHOTS: storyboard_plan 无 shots")

    # Build a compact plan summary for the LLM
    plan_summary = {
        "title": plan.get("title") or plan.get("product_name") or "",
        "aspect_ratio": plan.get("aspect_ratio", "9:16"),
        "shots": [{
            "id": s.get("id"),
            "duration": s.get("duration") or s.get("seconds"),
            "dialogue": (s.get("dialogue") or s.get("voiceover") or "")[:80],
            "visual": (s.get("visual") or s.get("scene_prompt") or "")[:80],
            "camera": s.get("camera_movement") or s.get("camera") or "",
            "characters": s.get("characters") or [],
            "has_product": bool(s.get("product_sku") or s.get("product_refs")),
        } for s in shots],
    }

    # Try LLM-based design
    llm_error = None
    if api_key:
        try:
            design, used_model = _llm_design(plan_summary, api_key, model=model)
            validated = _validate_design(design, shots)
            validated["design_engine"] = {"mode": "llm", "model": used_model}
            return validated
        except Exception as exc:
            llm_error = str(exc)
            if require_llm:
                raise ValueError("MOTION_DESIGN_LLM_FAILED: %s" % exc) from exc
    elif require_llm:
        raise ValueError("MOTION_DESIGN_LLM_REQUIRED: 缺少 BasicRouter key")

    # Fallback: rule-based design (no LLM needed)
    fallback = _rule_based_design(plan_summary, shots)
    fallback["design_engine"] = {
        "mode": "rule_based",
        "reason": llm_error or "api_key_unavailable",
    }
    return fallback


def _llm_design(plan_summary, api_key, model=None):
    """Call BasicRouter LLM to generate motion design."""
    prompt = DESIGN_PROMPT.replace("{plan_json}", json.dumps(plan_summary, ensure_ascii=False, indent=2))
    models = br_client.list_models(category="text")
    if not models:
        raise ValueError("在线文本模型目录为空")
    # Pick a capable text model
    text_model = model
    if not text_model:
        for m in (models if isinstance(models, list) else []):
            if m.get("online") is False or m.get("status") is False:
                continue
            name = str(m.get("modelId") or m.get("modelName") or m.get("id") or "")
            if any(k in name.lower() for k in ("kimi", "gpt", "claude", "qwen", "deepseek")):
                text_model = name
                break
    if not text_model:
        raise ValueError("没有可用的字幕设计文本模型")
    messages = [
        {"role": "system", "content": "你只输出符合要求的 JSON 字幕与动效设计方案。"},
        {"role": "user", "content": prompt},
    ]
    result = br_client.chat(api_key, messages, model=text_model, timeout=180)
    if not result:
        raise ValueError("字幕设计模型返回为空")
    # Extract JSON from response
    text = str(result)
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text), text_model


def _rule_based_design(plan_summary, shots):
    """Rule-based motion design fallback (no LLM needed).

    Generates a reasonable default design based on shot content analysis.
    """
    design_shots = []
    for shot in shots:
        sid = shot.get("id")
        dialogue = shot.get("dialogue") or ""
        visual = shot.get("visual") or ""
        has_product = bool(shot.get("has_product") or shot.get("product_sku")
                           or shot.get("product_refs"))
        duration = shot.get("duration") or 5

        # Subtitle: extract from dialogue
        subtitle = None
        if dialogue:
            # Clean dialogue for subtitle
            text = re.sub(r"[。！？!?；;，,、\s]+", " ", dialogue).strip()
            if len(text) > 20:
                text = text[:20]
            subtitle = {
                "text": text,
                "position": "lower_third",
                "style": "subtitle",
                "safe_zone": "lower_third",
            }

        # Motion overlay: rule-based selection
        overlay = {"style": "none", "safe_zone": None, "reason": ""}
        if has_product and duration >= 4:
            # Product shot → data card if there's a metric, otherwise title
            overlay = {
                "style": "title_reveal",
                "title": plan_summary.get("title") or "产品亮点",
                "position": "center",
                "safe_zone": "center",
                "timing": "进场0.5s后",
                "reason": "产品镜头需要品牌标题强化记忆",
            }
        elif len(dialogue) > 30 and duration >= 5:
            # Long dialogue → lower third with key point
            key_point = dialogue[:15].strip()
            overlay = {
                "style": "lower_third",
                "title": key_point,
                "position": "lower_third",
                "safe_zone": "lower_third",
                "timing": "全程",
                "reason": "长口播需要下三分字幕辅助理解",
            }

        # Determine safe zones
        safe_zones = set()
        if subtitle:
            safe_zones.add(subtitle.get("safe_zone", "lower_third"))
        if overlay.get("safe_zone"):
            safe_zones.add(overlay["safe_zone"])

        design_shots.append({
            "shot_id": sid,
            "subtitle": subtitle,
            "motion_overlay": overlay,
            "video_safe_zones": sorted(safe_zones),
            "director_note": "",
        })

    return {
        "version": 1,
        "global_style": {
            "font_family": "default",
            "primary_color": "#E60012",
            "subtitle_position": "lower_third",
            "motion_density": "medium",
        },
        "shots": design_shots,
    }


# ── 安全区注入 ──────────────────────────────────────────────────────

def inject_safe_zones(prompt_text, shot_design):
    """把安全区指令注入视频生成 prompt。

    告诉模型在哪些区域不要放置关键动作/主体，为后期字幕/动效留出空间。
    """
    if not shot_design:
        return prompt_text
    zones = shot_design.get("video_safe_zones") or []
    if not zones:
        return prompt_text

    hints = []
    for zone in zones:
        zone_def = SAFE_ZONES.get(zone)
        if zone_def:
            hints.append(zone_def["prompt_hint"])
    if not hints:
        return prompt_text

    safe_zone_block = "\n【画面构图安全区】%s" % " ".join(hints)
    return prompt_text + safe_zone_block


def storyboard_safe_zone_prompt(shot_design):
    """生成故事板构图的安全区提示。"""
    if not shot_design:
        return ""
    zones = shot_design.get("video_safe_zones") or []
    if not zones:
        return ""
    hints = []
    for zone in zones:
        zone_def = SAFE_ZONES.get(zone)
        if zone_def:
            hints.append(zone_def["storyboard_hint"])
    if not hints:
        return ""
    return "；".join(hints)


def get_shot_design(design, shot_id):
    """从 motion_design 中获取指定镜头的动效设计。"""
    if not design:
        return None
    for shot in (design.get("shots") or []):
        if str(shot.get("shot_id")) == str(shot_id):
            return shot
    return None


def design_to_motion_overlay(shot_design):
    """把 shot 级动效设计转成 final_edit._build_motion_overlay 兼容的格式。"""
    if not shot_design:
        return None
    overlay = shot_design.get("motion_overlay") or {}
    if overlay.get("style") == "none":
        return None
    result = {
        "style": overlay.get("style"),
        "position": overlay.get("position") or STYLE_DEFAULT_ZONE.get(overlay.get("style"), "center"),
        "timing": overlay.get("timing") or "",
    }
    if overlay.get("title"):
        result["title"] = overlay["title"]
    if overlay.get("bullets"):
        result["bullets"] = overlay["bullets"]
    if overlay.get("metric"):
        result["metric"] = overlay["metric"]
    return result


def design_to_subtitle(shot_design, dialogue=""):
    """把 shot 级动效设计转成字幕行。"""
    if not shot_design:
        return None
    sub = shot_design.get("subtitle")
    if not sub or not sub.get("text"):
        # Fallback to dialogue
        if dialogue:
            text = re.sub(r"[。！？!?；;，,、\s]+", " ", dialogue).strip()[:20]
            return {"text": text, "position": "lower_third"} if text else None
        return None
    typography = sub.get("typography") or {}
    result = {"text": sub["text"], "position": sub.get("position", "lower_third")}
    for source, target in (("size_px", "size"), ("max_width_px", "width_px"),
                           ("preset", "preset"), ("max_lines", "max_height_lines")):
        if typography.get(source) is not None:
            result[target] = typography[source]
    if typography.get("emphasis"):
        result["emphasis"] = typography["emphasis"]
    return result


# ── CLI ─────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="导演级动效设计规划器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dp = sub.add_parser("design", help="从 storyboard_plan 生成 motion_design")
    dp.add_argument("--plan", required=True)
    dp.add_argument("--out", required=True)
    dp.add_argument("--model", help="LLM model for design generation")
    dp.add_argument("--require-llm", action="store_true",
                    help="模型不可用或返回无效时阻断，不允许静默退回规则模板")

    ip = sub.add_parser("inject", help="把动效设计注入 storyboard_plan")
    ip.add_argument("--plan", required=True)
    ip.add_argument("--design", required=True)
    ip.add_argument("--out", help="输出路径（默认原地修改）")

    args = ap.parse_args(argv)

    if args.cmd == "design":
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
        api_key = key_setup.load_key()
        design = design_from_plan(plan, api_key=api_key, model=args.model,
                                  require_llm=args.require_llm)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(design, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "out": args.out,
                          "shots": len(design.get("shots", [])),
                          "design_engine": design.get("design_engine")}, ensure_ascii=False))
        return 0

    if args.cmd == "inject":
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
        with open(args.design, encoding="utf-8") as f:
            design = json.load(f)
        # Inject motion design into each shot
        for shot in plan.get("shots") or []:
            sid = str(shot.get("id"))
            shot_design = get_shot_design(design, sid)
            if shot_design:
                shot["motion_design"] = shot_design
                shot["video_safe_zones"] = shot_design.get("video_safe_zones") or []
        out_path = args.out or args.plan
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "out": out_path}, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
