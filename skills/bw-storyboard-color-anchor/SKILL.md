---
name: bw-storyboard-color-anchor
description: "Confirm shot composition with a black-and-white gpt-image-2 storyboard, then render the actual video with COLOR reference assets driving the palette — not the grayscale storyboard. Use when building marketing/digital-human videos where the client approves a pencil/charcoal-style B&W storyboard (cheaper to iterate, focuses on framing/light/composition) but the final film must be color and on-brand. Covers gpt-image-2 B&W storyboard defaults with anti-ID-drift face lock, the split-stage anchor-priority rule (color cast board / product / scene images FIRST, grayscale shot image demoted to a composition hint), and the batch render→assemble handoff via --results-out."
version: 1.0.0
license: MIT
metadata:
  hermes:
  tags: [storyboard, black-white, gpt-image-2, color-anchor, seedance, id-drift, digital-human, script-splitter, video-engine, marketing-video]
prerequisites:
  commands: [python3]
---

# 黑白故事板 → 彩色素材锚定出片

**故事板黑白只用于构图/分镜确认；出片颜色靠彩色素材锚定，不靠黑白故事板。**

铅笔/炭笔预演风黑白故事板便宜、快、聚焦构图/光影/景别，适合和客户反复确认镜头。但成片必须是彩色且符合品牌色。若拿黑白故事板当首帧参考去出彩色片，模型要自己补色 → 颜色不确定、掉色。正解：**阶段4拆分时把彩色素材（人物板/产品图/场景图）作主参考在前锚定颜色，黑白分镜图降为构图提示垫后。**

## 何时用

- 视频类场景（口播/访谈/实景讲解/TVC/产品演示）已用 `storyboard.py` 出了**黑白**故事板并经客户确认构图。
- 成片要彩色、上品牌色，且要跨镜/跨段保持人物/产品/场景一致。
- 客户反馈「故事板可以黑白，但成片得是彩色的、颜色要对」。

## 闭环流程

### ① 故事板默认黑白 + 人脸一致性强锁

```bash
python3 scripts/storyboard.py --plan output/storyboard_plan.json \
  --out-dir output/storyboard --model gpt-image-2 --json
```

- 默认黑白（`shot_prompt` 内建 `STRICT BLACK-AND-WHITE` + 约束：主体定义句式、16:9 4x3 镜头1→12 时序、双胞胎全局约束、无字幕/Logo/水印）。客户要彩色故事板才加 `--color`，或 plan/shot 设 `color_mode:"color"`。
- 人物板 `cast_board.jpg` 六视图 + **近景人脸↔全身一致性强锁**：正脸大头照是最高权重身份锚（占比大、五官清晰、无表情），与全身照必须逐项对齐是同一张脸（防 seedance ID 漂移）。
- 把 `preview_html`/`index_md` 用绝对路径 Markdown 交客户确认构图/顺序/人物形象。

### ② 拆分：彩色素材优先锚定颜色（核心）

```bash
python3 scripts/script_splitter.py split \
  --plan output/storyboard_plan.json \
  --storyboard-dir output/storyboard \
  --out output/segments.json
```

- 默认按**黑白故事板**处理：`_collect_anchor_urls` 把彩色 `asset_refs`（`digital_human_portraits`/`product_images`/`scene_images`）放**最前**锚定颜色，黑白分镜图垫**后**作构图提示。
- 判定来源：`plan.color_mode`（缺省=黑白）；`shot.color_mode` 可逐镜覆盖。
- **故事板本身是彩色**时加 `--color-storyboard`（或 plan.color_mode=color），恢复旧语义：分镜图作首帧锚在前。
- 前置：`storyboard_plan.json` 的 `asset_refs` 必须填**已确认的彩色素材**（彩色人物板/产品 hero/场景图）。缺彩色素材先回 `/asset-prep` 补齐（`gen-image` 两遍清洗 + confirm）。

### ③ 出片 + 合成交接（别断链）

```bash
# batch 出片必须带 --results-out，否则合成拿不到输入
python3 scripts/video_engine.py --batch output/segments.json \
  --results-out output/batch_results.json \
  --locked-refs output/storyboard/<run>/cast_board.jpg assets/<client>/hero.png

# 多段 → ffmpeg 拼底片
python3 scripts/script_splitter.py assemble \
  --segments output/segments.json \
  --results output/batch_results.json \
  --out output/basecut.mp4
```

- `--locked-refs`：把彩色人物板正脸+全身/产品 hero/场景图强制注入每段最前，跨段固定人物/产品/场景，只变台词/剧本/运镜（跨段一致不跳脸）。
- 跨段要连贯长视频/访谈防跳脸：`--chain`（尾帧串联，SSIM≈0.96；seed 锁一致性已证伪，别用）。

## Pitfalls

- **别拿黑白故事板当彩色片首帧锚**：这是本 skill 存在的根因。默认拆分已把黑白分镜图降级为构图提示；只有 `--color-storyboard` 才让它当首帧锚。
- **`asset_refs` 空 → 无彩色可锚**：拆分会只剩黑白分镜图，出片补色不确定。先补齐彩色素材再拆分。
- **batch 不带 `--results-out`**：`batch_results.json` 不会自动生成，`assemble --results` 断链（历史 bug：文档曾写 `--out-dir` 幽灵 flag）。必带。
- **人物板 close-up 与 full-body 像两个人**：判不合格，重出人物板；正脸特写是身份锚，权重最高。
- **黑白故事板配色相关约束**：黑白 shot_prompt 负向里已含压色相项；切彩色记得 `--color` 同时放行。

## 验证

改动后跑 `tests/test_v16.py`（含黑白锚定优先级 T18–T20）+ `tests/test_v14.py`（`_collect_anchor_urls` 双模式回归）：纯本地、无网络。
