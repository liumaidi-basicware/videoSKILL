# 全流程总览（7 阶段 · 修正版顺序）

## 正式客户闭环（可执行顺序）

1. `START_HERE_AGENT.py init` 创建独立 manifest。
2. brief/script/cast/storyboard/render_plan 逐阶段生成、预览、审批。
3. `script_splitter.py split --client --manifest` 生成 segments 并登记 video handoff。
4. `video_engine.py --batch --client --manifest --ledger --results-out` 生成底片。
5. 每段执行 formal take review、`accept-take` 或 `new-video-attempt`，并登记 OCR clear/精确 waiver。
6. `script_splitter.py assemble --client --manifest --reviews --results` 后执行 `pipeline.py approve --stage video`。
7. `derive-captions`、客户确认 caption artifact、HyperFrames/subtitle overlay 生成 captioned basecut，再执行 `pipeline.py approve --stage captions`。
8. `final_edit.py run --client --manifest --caption-manifest` 生成 final，执行 formal media QC 和客户确认。
9. `pipeline.py delivery create` 后执行 `delivery verify`。

缺 reviews、accepted take、OCR evidence、caption artifact 或 formal QC 时，正式流程必须阻断；`--draft` 只允许内部预览，不是客户交付路径。

本文件是营销视频 agent 的**顶层流程编排**，串起所有 skill 与脚本。各创作场景 skill
（口播/访谈/产品演示/文档转视频等）都在此框架内运行。**顺序是硬约束，不要颠倒。**

> 第 0 步铁律：任何阶段动手前先跑 `python3 scripts/key_setup.py gate`。返回 `BLOCKED`
> 就停下、转达客户粘贴 `sk-` 密钥，`SAVED` 后再继续。详见 `script-cocreation.md` 第 0 步。

---

## 阶段 1 · 素材补充（在脚本创作之前完成）

素材必须**先于脚本**就绪，脚本才有据可依。按客户交付物分两条支线：

- **实拍/数字人/产品/场地类** → 生成**六视图**参考素材（脚本之前）：
  - 人物：`python3 scripts/digital_human.py create ...` → 人物六视图 / cast_board（多角度形象板）
  - 商品：`python3 scripts/product_library.py gen-all-views ...` → front/back/side/detail/scene/pack 六方位图
  - 场地/场景：`python3 scripts/asset_prep.py gen-image ...`（按当前图像模型降级策略选择）
  - 客户确认素材（`confirm-image` / `confirm-view`，pending → confirmed）后才进阶段 2。

- **文档 / 文案 / PPT 类** → 生成 **Remotion 内容动效素材**（`remotion-com-skills` 组件库驱动）：
  ```bash
  # ① 解析文档 → 内容动效骨架 spec（占位待填）
  python3 scripts/content_scaffold.py scaffold --file <doc.pptx|pdf|docx|md> \
      --out output/content_spec.json --orientation portrait
  # ② 本地 LLM（你）读文档，按 content_scaffold.py kinds 填真实内容到 spec（不编造）
  python3 scripts/content_scaffold.py kinds     # 查所有 scene kind 及必填 props
  # ③ 校验 spec
  python3 scripts/content_scaffold.py validate --spec output/content_spec.json
  # ④ 渲染内容动效素材（HeroTitle/SectionTitle/ProcessFlow/DataTable/EvolutionTree...）
  python3 scripts/remotion_engine.py render-content --spec output/content_spec.json \
      --out output/content_assets.mp4
  ```
  文档型视频可以直接以内容动效成片，或把动效素材留给阶段 6/7 与底片结合。

**产出**：已确认的六视图素材 / 内容动效素材，写回 brief。

---

## 阶段 2 · 脚本共创引导（基于已备素材 · 本地 LLM · 零 Credit）

走 `script-cocreation.md` 八阶段漏斗（意图→素材盘点→核心信息→风格→数字人→逐段共创→
渲染方案→定稿）。**脚本没定稿，绝不调专业模型出片。**

**产出**：客户明确定稿的 `output/storyboard_plan.json`（characters + shots，每 shot 含
dialogue/visual/camera/duration/asset_refs）。

---

## 阶段 3 · 分镜图生成（素材 + 定稿剧本）

```bash
python3 scripts/storyboard.py --plan output/storyboard_plan.json \
    --out-dir output/storyboard --model gpt-image-2 --json
```
输入 = **已备六视图素材 + 定稿剧本**；`asset_refs` 严格注入六视图，保证人物/产品/场景一致。
输出 cast_board.jpg + 每镜 shot_<id>.jpg。用绝对路径 Markdown 展示给客户确认构图/顺序。
客户改则先改 `storyboard_plan.json` 重出图，不直接改视频。

**故事板默认黑白（仅用于构图/分镜确认）**：故事板是铅笔/炭笔预演风黑白图（`--color` 或
plan/shot `color_mode:"color"` 可切彩色）。**出片时颜色不靠黑白故事板，而靠彩色素材锚定**
——阶段 4 拆分时（默认黑白故事板）彩色人物板/产品图/场景图作**主参考在前锚定颜色**，
黑白分镜图降为构图提示垫后，避免出片补色不确定/掉色。人物板正脸大头照是最高权重身份
锚（防 ID 漂移）；正脸特写与全身照必须是同一张脸。

---

## 阶段 4 · 底片生成（剧本拆分 → Seedance 原生 prompt → 多段 → 合成）

```bash
# ① 定稿剧本按各镜时长拆成 render_batch segments（每段=分镜图+锚定素材+该段台词）
python3 scripts/script_splitter.py split --plan output/storyboard_plan.json \
    --storyboard-dir output/storyboard/<run-id> --client <client> \
    --manifest output/<client>/<run-id>/run_manifest.json \
    --out output/<client>/<run-id>/segments.json
# ② 出各段底片（音画一体；Seedance 可用时采用时间节点、人物/产品参考素材角色和单一主导运镜；同一连续动作优先用视频延长，明显换动作才用 --chain；默认 seedance→kling→wan 降级；videoType 4/5 参考图锚定段自动回落 kling）
#    ⚠️ 必须带 --results-out，把每段结果落盘，才能喂给下一步 assemble（否则「出片→合成」断链）
python3 scripts/video_engine.py --batch output/<client>/<run-id>/segments.json \
    --client <client> --manifest output/<client>/<run-id>/run_manifest.json \
    --ledger output/<client>/<run-id>/generation_runs.jsonl \
    --results-out output/<client>/<run-id>/batch_results.json
#    跨段固定同一人物/产品/场景时，加 --locked-refs（共享锚定图，只变台词/运镜）：
#    python3 scripts/video_engine.py --batch output/segments.json \
#        --results-out output/batch_results.json \
#        --locked-refs output/storyboard/<run>/cast_board.jpg assets/<client>/hero.png
# ③ 多段 → ffmpeg 拼成最终版底片（单段直接复制）
python3 scripts/script_splitter.py assemble --segments output/segments.json \
    --results output/<client>/<run-id>/batch_results.json \
    --client <client> --manifest output/<client>/<run-id>/run_manifest.json \
    --reviews output/<client>/<run-id>/reviews.json \
    --out output/<client>/<run-id>/basecut.mp4
# （可选）HyperFrames 叠字幕/转场精修： scripts/hf_engine.py
```
> 出片机制：视频模型**音画一体**（台词交上去，配音+对口型+画面一次生成，无第二步配音）；
> 多段**并行**提交异步轮询，墙钟≈单段，别串行。

**产出**：`output/basecut.mp4`（最终版底片）。

---

## 阶段 5 · 🔴 第一轮用户确认 + 下载底片（闸门）

把底片用绝对路径 Markdown 交给客户**下载预览**，复述完整脚本+分镜确认结果。
**客户拍板底片 OK 后**才进阶段 6。不满意则回阶段 3/4 修正，不硬改视频。

---

## 阶段 6 · 视频逆向工程（底片确认后）

```bash
python3 scripts/video_reverse.py reverse --basecut output/basecut.mp4 \
    --target-model kling-v3-omni-video --frames 12 --out-dir output \
    --motion-assets output/content_spec.json   # 文档型可带上动效素材清单
```
用「顶级AI视频提示词架构师」提示词逐镜头拆解底片（主体锚点/动作/景别/机位/焦段/构图/
景深/运镜/光线/色温/材质/节奏/转场/环境声音），**先输出完整时间轴**，再反推出结合动效
素材、在 Remotion 中使用的**方案命令**（含起始画面/人物动作/摄影机运动/结束画面/镜头衔接
+ 防变脸/服装漂移/肢体错误/背景闪烁/动作断裂/物理关系失真的禁止项）。

**产出**：`output/reverse_timeline.md` + `output/remotion_scheme.json`。
> 逆向分析需多模态视觉模型；若本地未配 vision，脚本会返回错误，可由客户提供分析或人工补 scheme。

---

## 阶段 7 · 本地 Remotion 剪辑 → 最终成片

```bash
python3 scripts/final_edit.py run --scheme output/remotion_scheme.json \
    --basecut output/<client>/<run-id>/captioned_basecut.mp4 \
    --client <client> --manifest output/<client>/<run-id>/run_manifest.json \
    --caption-manifest output/<client>/<run-id>/caption_manifest.json \
    --out output/<client>/<run-id>/final.mp4
```
方案命令 + 动效素材 → Remotion shotlist：底片作背景视频层，按 camera_move 套运镜，
按 motion_overlay 叠动效字幕/图形/数据（对齐 `remotion-com-skills` 组件库风格），本地渲染
（零 Credit）。渲染前自动把本地媒体拷进 `remotion_engine/public/`（绕过 Remotion 的
`file://` 安全拦截）。可选 `brand_kit` 叠 Logo 水印、`ocr_check` 字幕检测。

**产出**：`output/final.mp4`（最终成片）。

---

## 顺序速查

素材（六视图/动效素材）→ 脚本 → 分镜图 → 底片(拆分·多段·合成) → **①确认+下载** →
逆向工程(时间轴+方案命令) → 本地 Remotion 剪辑 → 成片。
