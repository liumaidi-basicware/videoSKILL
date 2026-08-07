---
name: ugc-review
description: 第一人称测评 UGC 场景：自拍视角、口语化文案、生活化场景，模拟真人用户体验分享。
---

# UGC 测评场景（第一人称）

## 适用场景

客户需要效果营销中转化率最高的"真人测评"风格视频。数字人以第一人称
"我用了 X 周"的口吻，在生活化场景中自然分享使用体验。

## 脚本约束（替代标准共创漏斗的脚本阶段）

### 禁用词汇表

以下词汇在 UGC 场景中**禁止出现**，Agent 引导时须主动拦截：

- 营销腔：颠覆、引领、赋能、生态、矩阵、闭环、抓手
- 夸张承诺：最好、第一、唯一、永久、绝对
- 品牌自夸：行业领先、荣获、认证（除非客户明确要求且作为事实陈述）

### 文案要求

| 维度 | 标准 |
|---|---|
| 人称 | 全程第一人称"我"，不出现"我们品牌""本公司" |
| 字数 | 50-300 词（15-60s 视频） |
| 结构 | 钩子（1 句真实感受）→ 2-3 个使用细节 → 自然推荐（非硬广 CTA） |
| 语气 | 口语化、有停顿、允许犹豫词（"怎么说呢""其实"） |
| 真实感 | 必须包含至少 1 个"不完美"细节（如"刚用的时候不太习惯"） |

### 钩子模板（供 Agent 引导客户选择）

1. **痛点切入**："用了两周，说三个我没想到的地方"
2. **反差钩**："买之前以为是 XX，结果完全不是"
3. **场景钩**："在 XX 的时候用了一下，发现一个细节"
4. **对比钩**："之前用 XX，换了这个之后感受很明显"

## 数字人板要求

### 自拍构图锁定块

在 `storyboard_plan.json` 的 `characters[].immutable_features` 中追加：

```json
{
  "shot_style": "selfie_pov",
  "camera_distance": "close-up-to-medium",
  "camera_angle": "slightly_above_eye_level",
  "arm_visible": true,
  "holding_phone": true,
  "scene_type": "casual_indoor",
  "clothing_style": "casual_leisure",
  "background_blur": "shallow_depth_of_field"
}
```

### 场景选择

| 场景 | 适用产品 | 构图要点 |
|---|---|---|
| 卧室/床边 | 美容、个护、小家电 | 自然光、床头柜道具 |
| 车内 | 车载、数码配件 | 侧光、中控台作背景 |
| 咖啡店 | 文创、数码 | 暖光、拿铁作道具 |
| 厨房台面 | 厨电、食品 | 顶光、台面产品摆放 |

## 故事板约束

- 动态格数故事板中至少约 40% 镜头为自拍角度（handheld、slightly_shaky）
- 产品特写格不少于 3 格（手部接触、细节、使用状态）
- 不允许出现"品牌 logo 大特写"格——UGC 不做硬广定格
- `panel_plan` 的 `camera_movement` 优先 `handheld` / `slow_push`

## 视频生成参数

```json
{
  "video_type": 4,
  "ratio": "9:16",
  "duration": 30,
  "negative_prompt": "professional lighting, studio, green screen, logo overlay, text overlay, watermark, advertisement style, corporate"
}
```

## 质检补充

除标准 take_review 80 分闸门外，额外检查：
- 脚本审查（`prompt_review`）须确认无禁用词汇
- 人物板须确认自拍角度（非正面证件照角度）
- 故事板须确认 ≥5 格自拍构图
