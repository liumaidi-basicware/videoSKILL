# 测试文件分组说明

57 个测试文件按域分组，不移动文件位置，不破坏现有导入。

## 分组

### 故事板（7 个）
```
test_storyboard_enhancements.py    — 分镜增强（景别/运镜/构图）
test_storyboard_model.py           — 故事板模型选择
test_storyboard_plan_json_errors.py — 计划 JSON 错误处理
test_storyboard_provenance.py      — 故事板溯源
test_storyboard_render_handoff.py  — 渲染交接
test_storyboard_resume.py          — 断点续传
test_strict_storyboard_handoff.py  — 严格交接契约
```

### 视频引擎（14 个）
```
test_v13.py ~ test_v21.py         — 版本回归套件（按功能迭代编号）
test_video_manifest_fail_closed.py — manifest 失败关闭
test_video_model_alias.py          — 模型别名
test_video_model_catalog_audio_contract.py — 音频契约
test_video_privacy_fallback.py     — 隐私回退
test_video_segmentation.py         — 时长拆分
```

### 流水线/manifest（6 个）
```
test_pipeline_delivery_e2e.py      — 端到端交付
test_manifest_ledger_concurrency.py — 并发账本
test_run_manifest_identity.py      — 身份一致性
test_generation_dependencies.py    — 生成依赖
test_four_stage_pipeline.py        — 四级流水线
test_confirmation_contracts.py     — 确认契约
```

### 素材/产品（6 个）
```
test_asset_feedback_refine.py      — 素材反馈精修
test_asset_recovery.py             — 素材恢复
test_product_consistency.py        — 产品一致性
test_brief_persistence.py          — brief 持久化
test_analyze_image.py              — 图像分析
test_image_utils.py                — 图像工具
```

### 基础设施/安全（10 个）
```
test_key_setup.py                  — 密钥管理
test_path_security.py              — 路径安全
test_security_and_capabilities.py  — 安全能力
test_no_inline_imports.py          — AST 内联导入防回归
test_no_text_in_frame_rule.py      — 画面文字铁律
test_agent_runtime.py              — Agent 运行时
test_runtime_platform.py           — 运行时平台
test_cjk_font.py                   — CJK 字体
test_ux.py                         — 用户体验
test_prompt_review.py              — 提示词审核
```

### 字幕/后期（5 个）
```
test_caption_final_manifest.py     — 字幕 manifest
test_kinetic_talk.py               — 动态文字
test_local_media_engines.py        — 本地媒体引擎
test_media_qc.py                   — 媒体质检
test_optimization_report_regressions.py — 优化报告回归
```

### 契约/集成（9 个）
```
test_batch_schema_and_ratio.py     — 批量 Schema
test_br_client_resilience.py       — API 客户端韧性
test_reference_handoff_contract.py — 参考图交接契约
test_reference_handoff_video_gate.py — 参考图视频闸门
test_scene_contract_references.py  — 场景契约引用
test_second_round_findings.py      — 二轮发现
test_standardize.py                — 标准化
test_seedance_prompt.py            — Seedance 提示词
test_take_review_ocr_gate.py       — 验片 OCR 闸门
```

## 运行

```bash
# 全量
python3 -m pytest tests/ -v

# 按组（示例）
python3 tests/test_storyboard_enhancements.py
python3 tests/test_v13.py tests/test_v14.py tests/test_v15.py

# 快速冒烟（核心 5 个）
python3 tests/test_v13.py tests/test_v14.py tests/test_v15.py \
  tests/test_storyboard_enhancements.py tests/test_key_setup.py
```
