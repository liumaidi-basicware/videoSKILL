import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import panel_expansion  # noqa: E402
import storyboard_enhancements  # noqa: E402
import video_engine  # noqa: E402


class StoryboardEnhancementsContractTests(unittest.TestCase):
    def _segment(self, root):
        sheet = os.path.join(root, "sheet.jpg")
        host = os.path.join(root, "host.jpg")
        for path in (sheet, host):
            with open(path, "wb") as handle:
                handle.write(b"fixture")
        return {
            "id": "s1", "storyboard_ref": True, "storyboard_path": sheet,
            "storyboard_dir": root, "storyboard_panel_index": 1,
            "ref_tags": ["@host"], "references": [{"tag": "@host", "url": host}],
            "reference_bindings": [{"tag": "@host", "role": "primary_subject",
                                    "must_be_visible": True}],
        }

    def test_formal_submission_requires_quality_and_customer_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            segment = self._segment(root)
            with self.assertRaisesRegex(ValueError, "PANEL_EXPANSION_APPROVAL_REQUIRED"):
                video_engine._prepare_storyboard_panel_submission("sk-test", segment, formal=True)

    def test_manual_qa_confirmation_can_promote_invalid_automated_panel(self):
        with tempfile.TemporaryDirectory() as root:
            segment = self._segment(root)
            panel = os.path.join(root, "panel.jpg")
            with open(panel, "wb") as handle:
                handle.write(b"fixture")
            segment["storyboard_panel"] = {
                "path": panel,
                "sha256": panel_expansion.artifact_contract.file_sha256(panel)
            }
            segment["panel_quality"] = {
                "pass": False,
                "score": 0,
                "issues": ["VISION_QA_INVALID_JSON"],
                "raw": "not json",
            }
            confirmed = panel_expansion.confirm([segment], manual_qa=True,
                                                reviewer="user", note="客户已人工确认")
            self.assertTrue(confirmed[0]["panel_quality"]["pass"])
            self.assertEqual(confirmed[0]["panel_quality"]["method"], "manual_human_review")
            self.assertEqual(confirmed[0]["storyboard_panel_approval"]["qa_method"], "manual_human_review")
            self.assertIn("manual_review", confirmed[0]["storyboard_panel_approval"])

    def test_reference_budget_fails_closed_above_provider_limit(self):
        segment = {"id": "s1", "reference_bindings": [
            {"tag": "@host"}, {"tag": "@product"}, {"tag": "@usage"}, {"tag": "@scene"}]}
        with self.assertRaisesRegex(ValueError, "REFERENCE_BUDGET_EXCEEDED"):
            panel_expansion.reference_budget(segment)

    def test_risk_profile_escalates_human_product_usage(self):
        profile = panel_expansion.risk_profile({"id": "s1", "ref_tags": ["@host", "@product", "@usage"]})
        self.assertTrue(profile["high_risk"])
        self.assertEqual(profile["recommended_candidates"], 3)

    def test_continuity_graph_rejects_unexplained_axis_flip(self):
        graph = storyboard_enhancements.build_continuity_graph([
            {"id": "s1", "scene_id": "a", "camera": "push", "shot_size": "wide",
             "screen_direction": "left"},
            {"id": "s2", "scene_id": "a", "camera": "pull", "shot_size": "close",
             "screen_direction": "right"},
        ])
        self.assertFalse(graph["ok"])
        self.assertIn("CONTINUITY_AXIS_CROSSING", graph["errors"][0])

    def test_stale_artifact_marks_downstream_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            segment = self._segment(root)
            panel = os.path.join(root, "panel.jpg")
            with open(panel, "wb") as handle:
                handle.write(b"old")
            segment["storyboard_panel"] = {"path": panel, "sha256": "wrong", "recipe_sha256": None}
            stale = storyboard_enhancements.stale_artifacts([segment])
            self.assertFalse(stale["ok"])
            self.assertIn("final", stale["stale"][0]["invalidate"])


if __name__ == "__main__":
    unittest.main()
