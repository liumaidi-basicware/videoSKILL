import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import incident_audit  # noqa: E402


class IncidentAuditTests(unittest.TestCase):
    def write(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_audit_blocks_stale_handoff_confirm_prompt_suggestion(self):
        with tempfile.TemporaryDirectory() as directory:
            incidents = self.write(directory, "REAL_TEST_INCIDENTS.md", (
                "# 真实链路测试事故记录\n\n"
                "### INC-003：代理 peer 校验阻断合法图片下载\n\n"
                "- 状态：已处理。\n\n"
                "### INC-005：故事板使用旧分镜计划生成“新文件”\n\n"
                "- 状态：已处理。\n\n"
                "### INC-050：磁吸关系修正后暴露 stale 分镜与 split 原子性问题\n\n"
                "- 当前状态：已处理代码层问题；generation_ready=false。\n\n"
                "### INC-052：预检恢复建议会误导确认旧视频提示词且缺少 manifest 审批链\n\n"
                "- 状态：已处理。\n\n"
                "## 深度修复审计（2026-08-06）\n\n"
                "### 仍未闭环的事项\n\n"
                "- INC-050 / INC-052 仍需客户授权付费生成。\n\n"
                "### 本次审计验证\n\n"
                "- smoke/all 全绿。\n"
            ))
            preflight = os.path.join(directory, "preflight.json")
            with open(preflight, "w", encoding="utf-8") as handle:
                json.dump({
                    "passed": False,
                    "generation_ready": False,
                    "errors": ["segments_no_declared_missing_images"],
                    "next_actions": [
                        {"code": "CONFIRM_VIDEO_PROMPT_REVIEW"},
                    ],
                }, handle)
            report = incident_audit.audit(incidents, preflight)
        self.assertFalse(report["ok"])
        self.assertIn("STALE_HANDOFF_STILL_SUGGESTS_CONFIRM_VIDEO_PROMPT",
                      report["errors"])

    def test_audit_blocks_temporary_fix_without_deep_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            incidents = self.write(directory, "REAL_TEST_INCIDENTS.md", (
                "# 真实链路测试事故记录\n\n"
                "### INC-003：代理 peer 校验阻断合法图片下载\n\n"
                "- 状态：已处理；新增回归测试。\n\n"
                "### INC-005：故事板使用旧分镜计划生成“新文件”\n\n"
                "- 状态：已处理；新增回归测试。\n\n"
                "### INC-050：磁吸关系修正后暴露 stale 分镜与 split 原子性问题\n\n"
                "- 临时修复：先改了命令。\n"
                "- 当前状态：已处理代码层问题；generation_ready=false。\n\n"
                "### INC-052：预检恢复建议会误导确认旧视频提示词且缺少 manifest 审批链\n\n"
                "- 状态：已处理；新增回归测试。\n\n"
                "### INC-053：单 shot 故事板恢复误提交其它 shot 并清空结果记录\n\n"
                "- 状态：已处理；新增回归测试。\n\n"
                "## 深度修复审计（2026-08-06）\n\n"
                "### 仍未闭环的事项\n\n"
                "- INC-050 仍需客户确认。\n\n"
                "### 本次审计验证\n\n"
                "- smoke/all 全绿。\n"
            ))
            report = incident_audit.audit(incidents)
        self.assertFalse(report["ok"])
        self.assertIn("INC-050", report["unresolved_temporary_fix_incidents"])
        self.assertIn("TEMP_FIX_WITHOUT_DEEP_EVIDENCE:INC-050", report["errors"])

    def test_real_incident_ledger_has_deep_fix_audit(self):
        report = incident_audit.audit(
            os.path.join(ROOT, "REAL_TEST_INCIDENTS.md"),
            os.path.join(ROOT, "output/momax-1vibe-go-lite-20260805-v1/video_effect_qc_preflight.json"),
        )
        self.assertTrue(report["deep_fix_audit_present"])
        self.assertGreaterEqual(report["incident_count"], 52)
        self.assertEqual([], report["open_incidents_from_audit"])
        self.assertEqual([], report["unresolved_temporary_fix_incidents"])
        self.assertNotIn("STALE_HANDOFF_STILL_SUGGESTS_CONFIRM_VIDEO_PROMPT",
                         report["errors"])
        self.assertNotIn("INC-050", report["workflow_open_incidents"])


if __name__ == "__main__":
    unittest.main()
