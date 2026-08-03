#!/usr/bin/env python3
"""文档/PPT → Remotion 内容动效 scene spec 脚手架（阶段1·文档支线）。

定位：客户给的是「文档/文案/PPT」时，视频主题=内容讲解型。这条支线把文档结构化文本
转成 remotion-com-skills 组件库驱动的**内容动效 scene spec**（HeroTitle 开场 /
SectionTitle 章节 / ProcessFlow 流程 / DataTable 数据表 / EvolutionTree 演进 /
MetricRow 指标 / TypewriterScene 打字机 / HighlightQuote 金句 …），
渲染成 Apple 风格深色科技动效视频，无需真人出镜。

分工：当前本地 Agent 读文档 → 决定每屏用哪个组件、填真实内容（不编造）→ 本脚手架
负责「起骨架 / 校验 prop 形状 / 估时长」，再交 remotion_engine.py render-content 出片。

三个子命令：
  scaffold  --file <doc> [--out spec.json] [--fps 30] [--orientation portrait]
      解析文档 → 生成一份「空内容动效骨架」spec（预置 hero+section 骨架，rows 待 LLM 填）。
  validate  --spec spec.json
      校验 spec 的每个 scene 的 kind/props 是否满足组件必填字段，估算总时长。
  kinds
      打印所有支持的 scene kind 及其必填 props（供 LLM 填 spec 时对齐）。

spec schema（与 remotion_engine/src/content/contentTypes.ts 对齐）：
  {"width","height","fps","brandPrimary","orientation",
   "scenes":[{"kind","durationInFrames","transition","props":{...}}]}
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 每个 scene kind 的必填 props（与 vendored 组件签名对齐）。用于 scaffold 起骨架与 validate 校验。
KIND_REQUIRED = {
    "hero": ["title"],                       # subtitle/tags 可选
    "section": ["title"],                    # sectionNumber/progress 可选
    "list": ["items"],                       # items:[{title,description?,icon?}]
    "features": ["features"],                # features:[{icon,title,description}]
    "metrics": ["metrics"],                  # metrics:[{value,label,prefix?,suffix?}]
    "table": ["columns", "data"],            # columns:[{key,title,align?}], data:[{...}]
    "typewriter": ["text"],                  # title? + text
    "quote": ["quote"],                      # source?/highlightWords?
    "process": ["steps"],                    # steps:[{label,description?,status?}]
    "evolution": ["stages"],                 # stages:[{year,name,description?,color?,breakthrough?}]
    "comparison": ["items"],                 # items:[{label,description,color,details?}]
    "causal": ["nodes", "edges"],            # nodes:[{id,label,color,x,y}], edges:[{from,to,label,type}]
    "product": ["title"],                    # 产品介绍页
}

# 每个 kind 的默认时长（秒）。内容页节奏：标题短、数据/流程页长一点让观众看清。
KIND_DEFAULT_SECONDS = {
    "hero": 3.0, "section": 2.0, "list": 4.0, "features": 4.0, "metrics": 3.5,
    "table": 4.5, "typewriter": 4.0, "quote": 3.0, "process": 4.5,
    "evolution": 5.0, "comparison": 4.5, "causal": 5.0, "product": 4.0,
}

TRANSITIONS = ["none", "fade", "slide", "lightsweep", "zoomblur", "curtain"]


def _seconds_to_frames(seconds, fps):
    return max(1, int(round(float(seconds) * fps)))


def scaffold(doc_path, fps=30, orientation="portrait", brand_primary="#007AFF"):
    """解析文档 → 生成空内容动效骨架 spec。

    真正填内容（每屏用哪个组件、写什么真实文案/数据）由当前本地 Agent 读文档后完成；
    本函数只给一个可跑的起点：hero 开场（用文档首个非空块猜标题）+ 一个 section 骨架 +
    一段 typewriter 占位。绝不编造数据——占位文本明确标注 [待填]。
    """
    if orientation == "portrait":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    title_guess = "[待填标题]"
    blocks = []
    try:
        import doc_extract
        blocks = doc_extract.extract(doc_path)
        for b in blocks:
            t = (b.get("text") or "").strip()
            if t:
                title_guess = t[:20]
                break
    except SystemExit:
        raise
    except Exception:
        pass  # 解析失败也给一个可编辑骨架

    scenes = [
        {"kind": "hero", "durationInFrames": _seconds_to_frames(KIND_DEFAULT_SECONDS["hero"], fps),
         "transition": "fade",
         "props": {"title": title_guess, "subtitle": "[待填副标题]",
                   "tags": ["[标签1]", "[标签2]"]}},
        {"kind": "section", "durationInFrames": _seconds_to_frames(KIND_DEFAULT_SECONDS["section"], fps),
         "transition": "none",
         "props": {"sectionNumber": 1, "title": "[待填章节标题]", "progress": 0.3}},
        {"kind": "typewriter", "durationInFrames": _seconds_to_frames(KIND_DEFAULT_SECONDS["typewriter"], fps),
         "transition": "none",
         "props": {"title": "[核心观点]", "text": "[待填正文，从文档提炼，不编造]", "speed": 2}},
    ]

    return {
        "width": width, "height": height, "fps": fps,
        "brandPrimary": brand_primary, "orientation": orientation,
        "_source_doc": os.path.basename(doc_path),
        "_source_blocks": len(blocks),
        "_note": "本 spec 为骨架，请本地 LLM 读文档后按 content_scaffold.py kinds 填充真实内容；[待填]/[占位]文本必须替换。",
        "scenes": scenes,
    }


def validate(spec):
    """校验 spec：每个 scene 的 kind 合法、必填 props 齐全、时长为正。
    返回 {ok, errors:[...], warnings:[...], total_frames, total_seconds, scene_count}。"""
    errors, warnings = [], []
    fps = spec.get("fps", 30)
    if not isinstance(spec.get("scenes"), list) or not spec["scenes"]:
        errors.append("spec.scenes 为空或缺失")
        return {"ok": False, "errors": errors, "warnings": warnings,
                "total_frames": 0, "total_seconds": 0, "scene_count": 0}

    total_frames = 0
    for i, sc in enumerate(spec["scenes"]):
        tag = "scene[%d]" % i
        kind = sc.get("kind")
        if kind not in KIND_REQUIRED:
            errors.append("%s 未知 kind: %r（合法: %s）" % (tag, kind, ", ".join(KIND_REQUIRED)))
            continue
        dur = sc.get("durationInFrames")
        if not isinstance(dur, int) or dur < 1:
            errors.append("%s durationInFrames 必须是正整数，当前 %r" % (tag, dur))
        else:
            total_frames += dur
        tr = sc.get("transition", "none")
        if tr not in TRANSITIONS:
            warnings.append("%s transition %r 不在支持列表，将按 none 处理" % (tag, tr))
        props = sc.get("props") or {}
        for req in KIND_REQUIRED[kind]:
            if req not in props or props[req] in (None, "", [], {}):
                errors.append("%s(%s) 缺必填 prop: %s" % (tag, kind, req))
        # 占位文本残留检查
        blob = json.dumps(props, ensure_ascii=False)
        if "[待填" in blob or "[占位" in blob or "[标签" in blob:
            warnings.append("%s(%s) 仍含占位文本 [待填]/[占位]，出片前必须替换" % (tag, kind))

    return {
        "ok": not errors, "errors": errors, "warnings": warnings,
        "total_frames": total_frames,
        "total_seconds": round(total_frames / float(fps), 2) if fps else 0,
        "scene_count": len(spec["scenes"]),
    }


def kinds_help():
    lines = ["支持的 scene kind 及必填 props（填 spec 时对齐组件签名）：", ""]
    for k, req in KIND_REQUIRED.items():
        secs = KIND_DEFAULT_SECONDS.get(k, 3.0)
        lines.append("  %-11s 必填: %-22s 默认时长 %.1fs" % (k, ", ".join(req) or "(无)", secs))
    lines.append("")
    lines.append("transition 可选: " + ", ".join(TRANSITIONS))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="文档/PPT → Remotion 内容动效 scene spec 脚手架")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scaffold", help="解析文档 → 生成空内容动效骨架 spec")
    sc.add_argument("--file", required=True, help="源文档路径（pptx/pdf/docx/txt/md/xlsx）")
    sc.add_argument("--out", help="输出 spec JSON 路径（默认 stdout）")
    sc.add_argument("--fps", type=int, default=30)
    sc.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    sc.add_argument("--brand", default="#007AFF", help="品牌主色（默认苹果蓝）")

    va = sub.add_parser("validate", help="校验内容动效 spec 是否可渲染")
    va.add_argument("--spec", required=True)
    va.add_argument("--json", action="store_true", help="以 JSON 输出校验结果")

    sub.add_parser("kinds", help="打印所有支持的 scene kind 及必填 props")

    a = ap.parse_args()
    if a.cmd == "scaffold":
        spec = scaffold(a.file, fps=a.fps, orientation=a.orientation, brand_primary=a.brand)
        text = json.dumps(spec, ensure_ascii=False, indent=2)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text)
            print("已生成骨架 spec: %s（%d 个 scene，请 LLM 填充真实内容）"
                  % (a.out, len(spec["scenes"])))
        else:
            print(text)
    elif a.cmd == "validate":
        with open(a.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
        result = validate(spec)
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("校验结果:", "✅ OK" if result["ok"] else "❌ 有错误")
            print("场景数: %d  总时长: %ss (%d 帧)"
                  % (result["scene_count"], result["total_seconds"], result["total_frames"]))
            for e in result["errors"]:
                print("  [错误]", e)
            for w in result["warnings"]:
                print("  [警告]", w)
        sys.exit(0 if result["ok"] else 1)
    elif a.cmd == "kinds":
        print(kinds_help())


if __name__ == "__main__":
    main()
