#!/bin/bash
# 测试分组运行脚本
# 用法: bash tests/run_tests.sh [group|all|smoke]
#   group: storyboard|video|pipeline|assets|infra|caption|contract
#   all: 全量 57 个
#   smoke: 核心流程 + 近期 incident 回归冒烟（含 Seedance/Kling 故事板分流协议）

cd "$(dirname "$0")/.."

GROUP="${1:-smoke}"

PASS_FILES=0
FAIL_FILES=0
SKIP_FILES=0
MATCHED_FILES=0
TOTAL_TESTS=0
FAILED_LIST=""
TMP_OUT=$(mktemp)
trap 'rm -f "$TMP_OUT"' EXIT

# 环境探测：硬依赖密钥/网络的测试文件，在缺依赖时显式跳过（非静默）。
# 当前所有用例均为离线 mock，无需跳过；未来新增硬依赖时在此登记。
env_skip_reason() {
    local f="$1"
    case "$f" in
        # 示例: test_xxx.py) [ -z "$BASICROUTER_API_KEY" ] && { echo "缺少密钥"; return 0; } ;;
        *) return 1 ;;
    esac
}

run_group() {
    local name="$1"; shift
    echo "=== $name ==="
    for f in "$@"; do
        if [ ! -f "tests/$f" ]; then
            echo "  MISS: $f (文件不存在)"
            continue
        fi
        local reason
        if reason=$(env_skip_reason "$f"); then
            SKIP_FILES=$((SKIP_FILES+1))
            echo "  SKIP: $f ($reason)"
            continue
        fi
        MATCHED_FILES=$((MATCHED_FILES+1))
        # 用 -m unittest 模块方式运行：即使文件缺 unittest.main() 入口也能真实执行
        if python3 -m unittest "tests.${f%.py}" >"$TMP_OUT" 2>&1; then
            n=$(grep -oE 'Ran [0-9]+ test' "$TMP_OUT" | grep -oE '[0-9]+' | tail -1)
            n=${n:-0}
            TOTAL_TESTS=$((TOTAL_TESTS+n))
            PASS_FILES=$((PASS_FILES+1))
            echo "  OK: $f (Ran $n tests)"
        else
            n=$(grep -oE 'Ran [0-9]+ test' "$TMP_OUT" | grep -oE '[0-9]+' | tail -1)
            n=${n:-0}
            TOTAL_TESTS=$((TOTAL_TESTS+n))
            FAIL_FILES=$((FAIL_FILES+1))
            FAILED_LIST="$FAILED_LIST $f"
            echo "  FAIL: $f (Ran $n tests)"
            tail -5 "$TMP_OUT" | sed 's/^/      /'
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
            test_video_effect_qc.py \
            test_video_model_catalog_audio_contract.py \
            test_video_privacy_fallback.py test_video_segmentation.py
        ;;
    pipeline)
        run_group "流水线/manifest" \
            test_pipeline_delivery_e2e.py test_manifest_ledger_concurrency.py \
            test_run_manifest_identity.py test_generation_dependencies.py \
            test_four_stage_pipeline.py test_confirmation_contracts.py \
            test_split_atomicity.py
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
            test_storyboard_enhancements.py test_key_setup.py \
            test_incident_audit.py \
            test_brief_persistence.py test_strict_storyboard_handoff.py \
            test_storyboard_panel_binding.py \
            test_storyboard_model.py test_prompt_review.py \
            test_product_consistency.py test_storyboard_resume.py \
            test_standardize.py test_br_client_resilience.py \
            test_confirmation_contracts.py test_v18.py test_v19.py \
            test_split_atomicity.py \
            test_video_effect_qc.py \
            test_asset_feedback_refine.py
        ;;
    all)
        shopt -s nullglob
        all_files=(tests/test_*.py)
        all_names=("${all_files[@]#tests/}")
        if [ "${#all_names[@]}" -eq 0 ]; then
            echo "*** NO TEST FILES MATCHED tests/test_*.py — possible silent skip ***"
            exit 1
        fi
        run_group "全量 ${#all_names[@]} 个" "${all_names[@]}"
        ;;
    *)
        echo "Usage: bash tests/run_tests.sh [storyboard|video|pipeline|assets|infra|caption|contract|smoke|all]"
        exit 1
        ;;
esac

echo
echo "=== 汇总 ==="
echo "执行文件: $MATCHED_FILES (OK $PASS_FILES / FAIL $FAIL_FILES / SKIP $SKIP_FILES)"
echo "用例总数: $TOTAL_TESTS"

# 下限断言：防静默空跑（假绿）
if [ "$GROUP" = "all" ]; then
    if [ "$MATCHED_FILES" -lt 40 ]; then
        echo "*** TEST FILE COUNT BELOW FLOOR ($MATCHED_FILES < 40) — possible silent skip ***"
        exit 1
    fi
    if [ "$TOTAL_TESTS" -lt 400 ]; then
        echo "*** TEST COUNT BELOW FLOOR ($TOTAL_TESTS < 400) — possible silent skip ***"
        exit 1
    fi
fi

if [ "$GROUP" = "smoke" ]; then
    if [ "$MATCHED_FILES" -lt 18 ]; then
        echo "*** SMOKE FILE COUNT BELOW FLOOR ($MATCHED_FILES < 18) — possible stale smoke list ***"
        exit 1
    fi
    if [ "$TOTAL_TESTS" -lt 300 ]; then
        echo "*** SMOKE TEST COUNT BELOW FLOOR ($TOTAL_TESTS < 300) — possible stale smoke list ***"
        exit 1
    fi
fi

if [ "$FAIL_FILES" -gt 0 ]; then
    echo "*** FAILED FILES:$FAILED_LIST ***"
    exit 1
fi
echo "ALL GREEN"
