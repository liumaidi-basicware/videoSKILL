#!/usr/bin/env python3
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


def _extract_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("PROMPT_REVIEW_INVALID_JSON: 大模型未返回可解析的提示词 JSON")
    return json.loads(text[start:end + 1])


def _base_storyboard_prompt(plan, shot):
    facts = plan.get("product_facts") or {}
    continuity = plan.get("continuity_contract") or {}
    return """你是商业广告分镜导演。请根据以下已确认剧本和素材事实，输出一段中文、可直接交给 gpt-image-2 的电影级 16:9 横版 4x3 12 格故事板提示词。

硬性事实：产品=%s；颜色=%s；规格=%s；场景连续性=%s；台词=%s；画面=%s；动作=%s；镜头=%s。
要求：逐格写清景别、机位、主体位置、动作起止、手部接触、光线、产品结构和前后格连续关系；人物脸、发型、服装必须引用已确认人物板；产品必须引用已确认产品板和产品使用图；相邻格要有明显景别或机位变化；背景不能出现生成字幕、口号、标签、Logo、水印；只保留产品包装/设备原生文字；故事板默认黑白铅笔/炭笔预演风。
只返回 JSON：{"prompt_zh":"...","continuity_notes":["..."],"negative_prompt_zh":"..."}""" % (
        facts.get("product_name") or facts.get("product_type") or "已确认产品",
        facts.get("product_color") or "以已确认产品板为准",
        json.dumps(facts, ensure_ascii=False), json.dumps(continuity, ensure_ascii=False),
        shot.get("dialogue") or shot.get("voiceover") or "无", shot.get("visual") or "无",
        shot.get("character_action") or shot.get("action") or "无", shot.get("camera") or "无")


def _base_video_prompt(plan, shot):
    facts = plan.get("product_facts") or {}
    continuity = plan.get("continuity_contract") or {}
    return """你是 Kling 视频导演和口播节奏设计师。请把已确认的第 %s 段中文剧本打磨成可直接提交的视频提示词。

产品事实：%s。台词：%s。动作：%s。镜头：%s。连续性合同：%s。
要求：明确 0-1 秒起始状态、动作发展、手部接触点、人物微表情、镜头运动、景别变化、产品外观锁、背景灯光锁、台词口型和声音语气；如果是延长段，必须明确从上一段最后状态无缝继续；不得重演上一段，不添加角色，不改服装/产品/场景，不把故事板网格作为视频画面，不生成字幕、文字、口号或水印。控制在 2200 个中文字符以内。只返回 JSON：{"prompt_zh":"...","negative_prompt_zh":"...","continuity_in":"...","continuity_out":"...","audio_contract":"..."}""" % (
        shot.get("id", "unknown"), json.dumps(facts, ensure_ascii=False),
        shot.get("dialogue") or shot.get("voiceover") or "无",
        shot.get("character_action") or shot.get("action") or shot.get("visual") or "无",
        shot.get("camera") or shot.get("camera_movement") or "无",
        json.dumps(continuity, ensure_ascii=False))


def build_prompt_requests(plan, stage):
    shots = plan.get("shots") or []
    return [{"shot_id": shot.get("id") or str(i + 1), "stage": stage,
             "source_prompt_zh": (_base_storyboard_prompt(plan, shot) if stage == "storyboard"
                                   else _base_video_prompt(plan, shot))}
            for i, shot in enumerate(shots)]


def polish(plan, stage, api_key=None, model="qwen3.6-plus"):
    key_setup.ensure_session_id()
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise ValueError("PROMPT_REVIEW_KEY_REQUIRED")
    import storyboard
    plan = storyboard.expand_product_sku_refs(storyboard.canonical_storyboard_plan(plan))
    system = ("你只负责提示词工程，不改变客户已确认的产品事实、颜色、规格、人物身份、台词和剧情。"
              "所有生成模型画面禁止字幕、口号、标签、Logo和水印。必须严格返回 JSON。")
    polished = []
    for item in build_prompt_requests(plan, stage):
        response = br_client.chat(api_key, item["source_prompt_zh"], system_prompt=system,
                                  model=model, timeout=600)
        result = _extract_json(response)
        result.update({"shot_id": item["shot_id"], "stage": stage,
                       "source_prompt_zh": item["source_prompt_zh"]})
        polished.append(result)
    return {"status": "pending", "stage": stage, "model": model,
            "plan_fingerprint": plan_fingerprint(plan), "prompts": polished,
            "created_at": datetime.now().isoformat(timespec="seconds")}


def save_pending(review, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    review = dict(review)
    review["review_fingerprint"] = _digest(review)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    return review


def confirm(path):
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    if review.get("status") != "pending" or not review.get("prompts"):
        raise ValueError("PROMPT_REVIEW_CONFIRM_BLOCKED")
    review["status"] = "confirmed"
    review["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    review["review_fingerprint"] = _digest(review)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    return review


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("polish")
    p.add_argument("--plan", required=True)
    p.add_argument("--stage", choices=("storyboard", "video"), required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="qwen3.6-plus")
    c = sub.add_parser("confirm")
    c.add_argument("--review", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "polish":
        with open(args.plan, encoding="utf-8") as handle:
            plan = json.load(handle)
        print(json.dumps(save_pending(polish(plan, args.stage, model=args.model), args.out),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(confirm(args.review), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
