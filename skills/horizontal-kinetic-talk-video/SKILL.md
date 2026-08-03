---
name: horizontal-kinetic-talk-video
description: 16:9 横版口播增强视频：原始口播、校准字幕、语义章节、动态卡片、PIP 和 Remotion 质检。
---

# 横版口播增强

适用于真人口播、数字人口播和讲解型横版视频。视频底片与动态排版分离：口播底片负责真实说话，Remotion 负责章节、卡片、PIP、进度条和字幕层。

## 推荐流程

1. 将原始口播视频统一为 `1920x1080 H.264/AAC`。

```bash
python3 scripts/remotion_engine.py normalize-input \
  --input source.mp4 --out output/input/speaker.mp4
```

2. 使用 `script_splitter.py derive-captions` 生成或确认 `lines.json`。
3. 用 `kinetic_talk.py` 编译横版 props：

```bash
python3 scripts/kinetic_talk.py \
  --video input/speaker.mp4 \
  --duration 30 \
  --captions output/lines.json \
  --out output/kinetic-props.json \
  --title "核心观点" \
  --eyebrow "品牌口播"
```

4. 用 Remotion 渲染：

```bash
python3 scripts/remotion_engine.py render-kinetic \
  --spec output/kinetic-props.json \
  --out output/kinetic-talk.mp4
```

## 质检

- 输出必须是 `1920x1080`。
- 字幕 `start/end` 应与实际语音确认。
- 场景章节不能重叠，最后一秒不能丢字幕。
- PIP 不得裁切人物脸部，必要时调整 `pipObjectPosition` 和 `pipScale`。
- 卡片不能遮挡眼睛、嘴部和字幕安全区。
- 动态组件不得覆盖产品关键区域。
- 生产渲染先使用 `--concurrency=1`，确认稳定后再提高并发。
