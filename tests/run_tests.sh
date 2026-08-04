#!/bin/bash
# 测试分组运行脚本
# 用法: bash tests/run_tests.sh [group|all|smoke]
#   group: storyboard|video|pipeline|assets|infra|caption|contract
#   all: 全量 57 个
#   smoke: 核心 5 个快速冒烟

set -e
cd "$(dirname "$0")/.."

GROUP="${1:-smoke}"

run_group() {
    local name="$1"; shift
    echo "=== $name ==="
    for f in "$@"; do
        if [ -f "tests/$f" ]; then
            result=$(python3 "tests/$f" 2>&1 | tail -3)
            if echo "$result" | grep -q "FAILED"; then
                echo "  FAIL: $f"
            else
                echo "  OK: $f"
            fi
        fi
    done
}

case "$GROUP" in
    storyboard)
        run_group "故事板" \
            test_storyboard_enhancements.py test_storyboard_model.py \
            test_storyboard_plan_json_errors.py test_storyboard_provenance.py \
            test_storyboard_render_handoff.py test_storyboard_resume.py \
            test_strict_storyboard_handoff.py
        ;;
    video)
        run_group "视频引擎" \
            test_v13.py test_v14.py test_v15.py test_v16.py test_v17.py \
            test_v18.py test_v19.py test_v20.py test_v21.py \
            test_video_manifest_fail_closed.py test_video_model_alias.py \
            test_video_model_catalog_audio_contract.py \
            test_video_privacy_fallback.py test_video_segmentation.py
        ;;
    pipeline)
        run_group "流水线/manifest" \
            test_pipeline_delivery_e2e.py test_manifest_ledger_concurrency.py \
            test_run_manifest_identity.py test_generation_dependencies.py \
            test_four_stage_pipeline.py test_confirmation_contracts.py
        ;;
    assets)
        run_group "素材/产品" \
            test_asset_feedback_refine.py test_asset_recovery.py \
            test_product_consistency.py test_brief_persistence.py \
            test_analyze_image.py test_image_utils.py
        ;;
    infra)
        run_group "基础设施/安全" \
            test_key_setup.py test_path_security.py \
            test_security_and_capabilities.py test_no_inline_imports.py \
            test_no_text_in_frame_rule.py test_agent_runtime.py \
            test_runtime_platform.py test_cjk_font.py test_ux.py \
            test_prompt_review.py
        ;;
    caption)
        run_group "字幕/后期" \
            test_caption_final_manifest.py test_kinetic_talk.py \
            test_local_media_engines.py test_media_qc.py \
            test_optimization_report_regressions.py
        ;;
    contract)
        run_group "契约/集成" \
            test_batch_schema_and_ratio.py test_br_client_resilience.py \
            test_reference_handoff_contract.py test_reference_handoff_video_gate.py \
            test_scene_contract_references.py test_second_round_findings.py \
            test_standardize.py test_seedance_prompt.py test_take_review_ocr_gate.py
        ;;
    smoke)
        run_group "快速冒烟" \
            test_v13.py test_v14.py test_v15.py \
            test_storyboard_enhancements.py test_key_setup.py
        ;;
    all)
        run_group "全量 57 个" tests/test_*.py
        ;;
    *)
        echo "Usage: bash tests/run_tests.sh [storyboard|video|pipeline|assets|infra|caption|contract|smoke|all]"
        exit 1
        ;;
esac
