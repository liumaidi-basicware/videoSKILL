---
name: seedance-video
description: Seedance 原生视频提示词与素材锚定编排能力，接入本项目的 BasicRouter video_engine。
---

# Seedance 视频技能

本技能把已确认的结构化镜头计划编译成 Seedance 更易执行的提示词；异步提交、模型选择、轮询、下载、OCR 和批量交接仍由 `scripts/video_engine.py` 负责。

## 核心约束

- 每个片段只保留一个主导连续运镜，复杂运镜拆成多个片段。
- 时间节点使用 `0-2秒：...`、`2-5秒：...`。
- 在提示词正文中明确每张参考图的角色：首帧、尾帧、主体、人物身份、产品外观或场景风格。
- 产品、角色、场景重复出现时先做素材覆盖审计；缺图先补素材。
- 首帧用 `video_type=2`，首尾帧用 `video_type=3`，多主体参考用 `video_type=5`，数字人单图身份锚定用 `video_type=4`。
- Seedance 可用时优先使用 `seedance-2.0`，能力不匹配时由 `video_engine.py` 自动回落。
- 字幕、slogan、Logo 和参数标签统一交给 HyperFrames 后期。

## 数据契约

片段可以包含：

```json
{
  "timeline": [
    {"start": 0, "end": 2, "action": "产品揭示", "camera": "slow push-in"},
    {"start": 2, "end": 5, "action": "产品旋转展示材质", "camera": "orbit"}
  ],
  "reference_roles": [
    {"label": "产品多视图", "role": "主体", "intent": "作为首帧和外观锚定"}
  ],
  "seedance_native": true
}
```

`script_splitter.py` 会为已确认的故事板镜头自动生成基础 `timeline`。需要更细的节奏时，直接在 `segments.json` 中补充时间节点。
