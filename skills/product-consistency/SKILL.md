---
name: product-consistency
description: 为 Seedance 视频建立人物与产品身份锚，产品素材自动生成并确认多角度九宫格产品板。
---

# 人物与产品一致性

## 产品资产闸门

当客户提供产品、包装、设备或其他明确物品素材时，正式视频生成前必须：

1. 建立 `product_library` SKU。
2. 以客户真实产品图作为参考生成多方位产品图。
3. 生成一张产品一致性九宫格：正面、左右45度、左右侧面、背面、顶部/控制区、材质细节、使用场景。
4. 展示 `product_board_pending.png`，客户确认后执行：

```bash
python3 scripts/product_board.py confirm --client <client> --sku <sku>
```

只有 `product_board.png`（confirmed）和 confirmed 方位图能进入正式出片。

## 生成依赖顺序

素材不能一次性并行生成。固定顺序是：

```text
用户上传真实产品图并确认
→ 生成产品九宫格板
→ 客户确认产品板
→ 生成人物六视图板（如有人物）
→ 客户确认人物板
→ 生成逐段故事板
→ 客户确认故事板
→ 视频
```

产品板候选绑定源产品图指纹；用户替换产品图后必须重新生成。`product_board_pending.jpg` 仅在源素材指纹和模型均一致时才可恢复复用。

## 视频参考策略

- 数字人：人物板正脸特写 + 全身参考图作为共享身份锚。
- 产品：hero + 多方位视图 + confirmed 产品九宫格作为产品身份锚。
- 场景：确认的场景图作为环境锚。
- Seedance prompt 必须明确每张参考素材的用途，不写模糊的“参考图片”。
- 不依赖 seed 保证一致性；使用参考图锁、尾帧串联和视频延长。

## 延长优先

相邻镜头属于同一连续动作、人物和产品状态时，优先使用延长字段：

```json
{
  "extend_from_previous": true,
  "duration": 5,
  "timeline": [{"start": 0, "end": 5, "action": "延续上一段动作", "camera": "保持上一段主运镜"}]
}
```

延长只生成新增时长，不重新生成上一段；运镜字段仍由原有 shot-list 控制。动作或机位发生明显变化时，才拆成新段并使用 `--chain` 尾帧衔接。
