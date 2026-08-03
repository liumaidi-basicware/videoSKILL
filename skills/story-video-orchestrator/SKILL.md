---
name: story-video-orchestrator
description: 将创意、素材、故事板、Seedance 片段、质检和后期连接为可恢复的故事视频流水线。
---

# 故事视频编排

通过文件契约衔接阶段：

```text
brief.json -> storyboard_plan.json -> storyboard_result.json
-> segments.json -> batch_results.json -> basecut.mp4
-> subtitle/motion plan -> final.mp4
```

## 阶段职责

1. 本地模型提炼目标、受众、核心动作和情绪弧线。
2. 审计人物、产品、场景和关键状态的素材覆盖率。
3. 校验并生成 16:9、4x3、12 格黑白预演故事板。
4. 每段只设置一个主导运镜，编译 Seedance 原生时间节点 prompt。
5. 先提交全部独立片段并统一轮询；需要连续性时使用 `--chain`。
6. 执行 OCR、画面质量、人物/产品一致性和音频确认。
7. 用 Remotion、HyperFrames 和 ffmpeg 完成本地可控的编排与文字层。

每个运行使用 `scripts/run_manifest.py` 记录版本、素材、输出、任务和审批状态，失败时只重跑未完成片段。
