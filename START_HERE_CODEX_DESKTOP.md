# Codex Desktop 启动说明（请先看这里）

## 不要直接把 zip 拖进 Codex 对话

请先解压：

```bash
unzip marketing-video-agent-codex-generic.zip
cd marketing-video-agent
git init
```

然后在 **Codex Desktop** 里选择 **Open Folder / 打开文件夹**，打开解压后的：

```text
marketing-video-agent/
```

不是打开 `.zip` 文件。

## 启动方式

在 Codex Desktop 对话框输入：

```text
/basicrouter-video
```

`/basicrouter-video` 会先执行全组件检查 `python3 scripts/setup_env.py full-check`；只要 Python、ffmpeg、Node、HyperFrames、Remotion 或 Chrome Headless Shell 任一缺失，就自动进入 inline 部署并在完成后复验，不会因为 Python 核心依赖已经存在而跳过 Remotion 等组件。

如果 Codex Desktop 没有识别 slash prompt，请直接输入下面这句话：

```text
请读取 .codex/prompts/basicrouter-video.md，并严格按照里面的流程执行。先检查环境和 API Key，然后引导我确定 client、品牌、素材、数字人、剧本、完整分镜。保持顾问式共创，不要一次性问长表单；但在脚本和逐段分镜定稿后，请参考 references/professional-storyboard-enrichment.md，把每段 storyboard_plan.json 补齐景别、运镜、角度偏移、构图、光线、微表情、角色动作、场景/道具资产提示词和音频连续性。然后必须实际运行 scripts/storyboard.py 调用 gpt-image-2 生成人物板和每段视频的16:9、4x3、12格黑白故事板。脚本会返回本次会话独立的 output/storyboard/<run-id>/ 目录；请把返回 JSON 里的 storyboard_index.md / storyboard_preview.html / storyboard_embedded.md 展示给我确认，确认后才允许生成视频。若是两段或多段视频拼接，必须保持背景、人物形象、人物声音和 BGM 氛围一致，同时仍遵守 30°–50° 角度偏移 / 远中近特写跨度原则。
```

## 分镜图片没显示怎么办

分镜图片只有在 Codex 实际运行下面命令后才会生成：

```bash
python3 scripts/storyboard.py --plan output/storyboard_plan.json --out-dir output/storyboard --model gpt-image-2 --json
```

成功时，命令日志必须出现：

```text
[gpt-image-2] rendering cast board…
[gpt-image-2] rendering storyboard shot 1: ...
"ok": true
```

并生成这些文件：

```text
output/storyboard/<run-id>/cast_board.jpg
output/storyboard/<run-id>/shot_*.jpg
output/storyboard/<run-id>/storyboard_index.md
output/storyboard/<run-id>/storyboard_embedded.md
output/storyboard/<run-id>/storyboard_preview.html
output/storyboard/<run-id>/storyboard_result.json
```

如果没有这些日志/文件，说明 Codex 还没有调用 gpt-image-2。

如果文件已生成但 Codex 对话里不显示图片，请让 Codex 读取并复制：

```text
output/storyboard/<run-id>/storyboard_preview.html
output/storyboard/<run-id>/storyboard_embedded.md
```

HTML 和 embedded markdown 都把图片直接内嵌进去，通常比本地路径更容易在 Codex Desktop 里显示。

## 用 16:9、4x3、12 格故事板生成视频时

客户确认某段 `shot_*.jpg` 16:9、4x3、12 格故事板后，Codex 生成该段视频必须使用这张最终故事板作为主要视觉参考，并开启：

```bash
python3 scripts/video_engine.py \
  --text "本段最终台词/剧情" \
  --type 4 \
  --urls output/storyboard/<run-id>/shot_01_xxx.jpg \
  --storyboard-ref \
  --duration 8 \
  --ratio 9:16 \
  --out output/video/seg01.mp4 \
  --json
```

多段视频用 `segments.json` 时，每段都要写：

```json
{
  "text": "本段最终台词/剧情",
  "video_type": 4,
  "urls": ["output/storyboard/<run-id>/shot_01_xxx.jpg"],
  "storyboard_ref": true,
  "duration": 8,
  "ratio": "9:16",
  "out_path": "output/video/seg01.mp4"
}
```

硬性要求：使用上传的最终 16:9、4x3、12 格故事板作为主要视觉参考；严格保持角色/产品/场景/光线/故事顺序一致；不要整图生成，不要把 12 格当作一张图动画化；必须按照第 1 格到第 12 格分镜顺序生成连续视频；不要添加额外角色；不要改变剧情、服装、道具、产品外观和场景关系。
