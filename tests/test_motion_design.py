import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import motion_design
import hf_engine


class MotionDesignModelTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "title": "Demo",
            "shots": [{"id": "s1", "duration": 5, "dialogue": "一句口播",
                       "product_sku": "sku-1"}],
        }
        self.response = json.dumps({
            "version": 1,
            "global_style": {"primary_color": "#112233"},
            "shots": [{
                "shot_id": "s1",
                "subtitle": {"text": "一句口播", "safe_zone": "lower_third",
                              "typography": {"size_px": 52, "max_width_px": 900,
                                             "max_lines": 2, "preset": "fade_up"}},
                "motion_overlay": {"style": "title_reveal", "safe_zone": "center",
                                    "preset": "pop", "size_px": 42, "width_px": 480},
                "video_safe_zones": ["lower_third", "center"],
            }],
        }, ensure_ascii=False)

    def test_llm_uses_public_br_client_signatures_and_records_provenance(self):
        models = [{"modelId": "qwen3.6-plus", "online": True, "status": True}]
        with mock.patch.object(motion_design.br_client, "list_models", return_value=models) as listing, \
             mock.patch.object(motion_design.br_client, "chat", return_value=self.response) as chat:
            result = motion_design.design_from_plan(
                self.plan, api_key="sk-test", require_llm=True)
        listing.assert_called_once_with(category="text")
        args, kwargs = chat.call_args
        self.assertEqual(args[0], "sk-test")
        self.assertIsInstance(args[1], list)
        self.assertEqual(kwargs["model"], "qwen3.6-plus")
        self.assertEqual(result["design_engine"],
                         {"mode": "llm", "model": "qwen3.6-plus"})

    def test_require_llm_does_not_silently_fallback(self):
        with mock.patch.object(motion_design.br_client, "list_models", return_value=[]):
            with self.assertRaisesRegex(ValueError, "MOTION_DESIGN_LLM_FAILED"):
                motion_design.design_from_plan(
                    self.plan, api_key="sk-test", require_llm=True)

    def test_optional_fallback_is_explicitly_labeled(self):
        with mock.patch.object(motion_design.br_client, "list_models", return_value=[]):
            result = motion_design.design_from_plan(self.plan, api_key="sk-test")
        self.assertEqual(result["design_engine"]["mode"], "rule_based")
        self.assertIn("在线文本模型目录为空", result["design_engine"]["reason"])
        self.assertEqual(result["shots"][0]["motion_overlay"]["style"], "title_reveal")

    def test_width_px_is_emitted_as_fixed_content_width(self):
        html = hf_engine.build_html({
            "scenes": [{"text": "紧凑字幕", "start": 0, "end": 1,
                        "left_px": 90, "right_px": 90, "width_px": 820}],
        })
        self.assertIn("left:90px;width:820px", html)
        self.assertNotIn("left:90px;right:90px", html)

    def test_model_typography_is_compiled_to_subtitle_scene_fields(self):
        shot = {"subtitle": {"text": "重点", "position": "lower_third",
                              "typography": {"size_px": 60, "max_width_px": 900,
                                             "max_lines": 1, "preset": "pop",
                                             "emphasis": ["重点"]}}}
        result = motion_design.design_to_subtitle(shot)
        self.assertEqual(result["size"], 60)
        self.assertEqual(result["width_px"], 900)
        self.assertEqual(result["max_height_lines"], 1)
        self.assertEqual(result["preset"], "pop")


if __name__ == "__main__":
    unittest.main()
