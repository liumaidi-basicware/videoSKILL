---
name: subtitle-overlay-vision
description: "Add burned-in subtitles to a finished vertical video the right way — a vision model (online image-capable, prefers qwen3.6-plus) analyzes representative frames to recommend a pixel-precise subtitle safe zone, then HyperFrames renders a ProRes-4444 alpha subtitle layer (transparent background) that ffmpeg overlays back onto the film. Use when the client asks to add/burn/position subtitles or captions on a rendered marketing video, when the default upper/center/lower position tiers are too coarse, or when subtitles must never cover the subject's face. Covers safe-zone analysis, transparent HyperFrames scenes with exact bottom/left/right px, ProRes vs VP9 alpha tradeoffs, ffmpeg overlay=format=auto compositing, and frame-size verification."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [subtitles, captions, hyperframes, prores, alpha, ffmpeg, overlay, vision-model, qwen3.6-plus, safe-zone, vertical-video, marketing-video]
prerequisites:
  commands: [python3, ffmpeg, node, npx]
---

# 字幕叠加 + 位置智能推荐（Subtitle overlay with vision-recommended placement）

把字幕**真正叠入**成片的正确做法，一个总方案解决两件事：

- **A. 位置智能推荐**：视觉模型（online 图像多模态，偏好 `qwen3.6-plus`）分析成片代表帧，输出**精确像素**安全区（`bottom_px/left_px/right_px/max_height_px` + 字号），绕过 `hf_engine` 的 `upper/center/lower` 三档粗定位，字幕不压人脸/主体、避开背景光效密集区。
- **B. 真正叠加**：HyperFrames 用 **transparent 背景**渲出 **ProRes 4444 alpha** 字幕层，再用 ffmpeg `overlay=0:0:format=auto` 叠回成片。不烧死、不绿底抠像、alpha 无损。

一句话入口：`python3 scripts/subtitle_overlay.py run --video <成片> --lines <逐句台词.json> --out <加字幕成片>`。

## 何时用

- 客户说「加字幕 / 打字幕 / 字幕位置不对 / 字幕挡住脸了 / 字幕要在下方安全区」。
- 已有一条**无字幕成片**（`video_engine.py` 出的底片或最终版），需要叠一层字幕。
- 竖屏 Reels/Shorts/TVC，字幕要落在画面安全区且不遮挡主体。

## 前置（铁律）

1. 先跑密钥闸门：`python3 scripts/key_setup.py gate`（`STORED` 才继续；`BLOCKED` 停下让客户贴 key）。
2. `python3 scripts/hf_engine.py doctor` 确认 Node/npx + ffmpeg 就绪（缺则 `/setup`）。视觉分析走 BasicRouter，无需本地模型。

## 台词 JSON 格式（lines.json）

逐句字幕，时间轴以秒为单位；`[[关键词]]` 会高亮成 accent 品牌色：

```json
[
  {"text": "65W [[快充]]，隨時滿電", "start": 0.0, "end": 2.5},
  {"text": "僅 320g，輕薄隨行", "start": 2.5, "end": 5.0, "preset": "slide_left"}
]
```

可选逐句字段覆盖全局安全区：`size`、`preset`（fade_up/slide_left/slide_right/pop/typewriter/fade）、`bottom_px`、`left_px`、`right_px`、`max_height_px`。

## 一步到位（推荐）

出片/加字幕前先跟客户打招呼给体感：「正在分析画面 + 叠字幕，大约 1–2 分钟」。

```bash
python3 scripts/subtitle_overlay.py run \
  --video output/video/basicrouter_tvc_final.mp4 \
  --lines output/lines.json \
  --out output/video/basicrouter_tvc_subtitled.mp4 \
  --width 1080 --height 1920 --alpha-fmt mov
```

返回 JSON 带 `ok / out(绝对路径) / safe_zone / vision_model / verify_kb`。默认渲染后清理中间文件（alpha mov + `_hf_sub/` 工程目录）；`--keep-intermediate` 保留调试。

`verify_kb` = 叠加后成片抽帧的字节数（KB），`ok:false` 意味着 `verify_kb < verify_min_kb`（帧信息量异常，可能全黑/叠加失败）——停下排查，别交付。`verify_min_kb` 按分辨率自动缩放（基准 1080×1920→200KB，横屏/720p 等比下调、设 40KB 下限），横屏或低分辨率成片不会被误判。

给客户看片用绝对路径 Markdown：`[加字幕成片](<绝对路径>)`。

## 分步（需要人工确认安全区/字幕稿时）

```bash
# 1) 视觉模型分析 4 帧 → 推荐安全区（可先给客户看 reasoning 再决定）
python3 scripts/subtitle_overlay.py analyze \
  --video output/video/final.mp4 --frames 4 --out output/safe_zone.json

# 2) 台词 + 安全区 → HyperFrames 场景 JSON（transparent 背景 + 精确定位）
python3 scripts/subtitle_overlay.py build-scenes \
  --lines output/lines.json --safe-zone output/safe_zone.json \
  --out output/subtitle_scenes_v2.json

# 3) 渲 ProRes alpha 字幕层
python3 scripts/hf_engine.py render \
  --spec output/subtitle_scenes_v2.json \
  --out output/video/subtitles_alpha.mov --format mov

# 4) ffmpeg overlay 叠回成片 + 验证
python3 scripts/subtitle_overlay.py compose \
  --video output/video/final.mp4 \
  --alpha output/video/subtitles_alpha.mov \
  --out output/video/final_subtitled.mp4
```

## ProRes(mov) vs VP9(webm) alpha 取舍

| | mov（ProRes 4444）| webm（VP9 alpha）|
|---|---|---|
| 中间文件 | 200–400MB | 20–50MB |
| 速度 | 快（Apple Silicon 硬件加速）| 慢约 2–3× |
| 画质 | 无损 alpha | 接近无损 |

**默认推荐 mov**（Apple Silicon 硬件加速、alpha 无损）。磁盘紧张或跨机传输选 `--alpha-fmt webm`。

## 关键实现点（改脚本时别踩）

- `hf_engine.build_html`：`background.type="transparent"` → `background:transparent;`；场景带 `bottom_px/left_px/right_px/max_height_px` 走 `_scene_position_css` 绝对定位（`position:absolute;bottom:Xpx;left:Ypx;right:Zpx`），没给精确像素才回退旧 `pos` 三档。
- `hf_engine.render(..., fmt=...)`：透传 `--format` 给 hyperframes；缺省按输出扩展名推断（`_fmt_from_out`）。
- ffmpeg 合成固定用 `[1:v]scale=W:H[sub];[0:v][sub]overlay=0:0:format=auto[v]`，`-map 0:a?` 保留原音轨，`libx264 -crf 16 -preset slow`。
- 视觉模型选择 `_pick_vision_model`：实时查 `/employee/models`，online 且 `multimodelTypes` 含 image，偏好序 `qwen3.6-plus → kimi-k2.6 → qwen3-vl-plus → gpt-5.4`，全离线兜底 `qwen3.6-plus`。
- 视觉分析失败（网络/模型/解析）→ `_default_safe_zone` 竖屏保守兜底（下方 14%、两侧 40px），带 `_fallback:true`；不中断流程，但可告诉客户「这次用的是默认安全区」。

## Pitfalls

- **别用绿底 colorkey 抠像**：透明 alpha（ProRes/VP9）比绿底扣除干净，无边缘残留。方案已固定走 transparent+alpha。
- **别把字幕烧进 video_engine 出片阶段**：字幕/动效不走 BasicRouter 视频模型（会乱码、不可改）。字幕永远是本地 HyperFrames alpha 层后期叠加。
- **中文/粤语不乱码**靠 HyperFrames 浏览器渲染真实字体（`hf_engine` 已注入 PingFang/Noto CJK @font-face）；libass 兜底会乱码，仅无 Node 时用。
- **verify_kb 偏低**先查 alpha 层是否真的透明（背景没设 transparent 会整块盖住画面）、overlay 尺寸是否匹配（scale 到成片 W×H）。
- **【真实故障，已修复】字幕层若渲成不透明格式，成片会只剩字幕+声音，画面完全消失**：`hf_engine.render()` 的 `--format` 若被误传成 `mp4`（或输出文件用了 `.mp4` 后缀让 `_fmt_from_out` 推断出 mp4），`background:transparent` 的 CSS 会被浏览器画布合成成不透明黑底（h264/yuv420p，无 alpha 通道）——这个"字幕层"实际上是铺满全屏的黑色矩形+白字，`compose()` 的 `overlay=0:0` 会把它整块盖在底片上；音轨仍从底片单独 `-map 0:a?` 保留正常，所以症状精确是"成片只剩字幕文字和原声音轨，画面消失"（真实复现过：ffprobe 显示 mp4 输出 codec=h264/pix_fmt=yuv420p；mov 正确输出 codec=prores/pix_fmt=yuva444p12le）。**已加两道防呆**：`hf_engine.render()` 在 `spec.background.type=="transparent"` 时强制要求格式为 mov/webm 且渲染后 ffprobe 实测校验确有 alpha，`subtitle_overlay.compose()` 合成前也会 ffprobe 校验 alpha_path（`require_alpha=True` 默认），任一环节没有 alpha 通道直接报错拒绝，不再产出这种"看似成功实则遮盖画面"的成片。**永远用 `.mov`/`.webm` 输出透明字幕层，不要用 `.mp4`。**
- 台词时间轴要和配音对齐；`kling-v3-omni-video` 是音画一体，配音时间轴以成片实际语音为准，别凭台词字数硬估。

## 验证

改动后跑 `tests/test_v17.py`（纯本地、无网络 mock）：覆盖 fmt 推断、精确定位 CSS、transparent HTML、安全区解析/兜底、build_scenes、analyze（mock 视觉模型）、compose（mock ffmpeg + overlay 命令断言）、run 全链路。
