#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import script_splitter as ss  # noqa: E402
import seedance_prompt  # noqa: E402
import video_segmentation as vs  # noqa: E402


class SceneContractReferenceTests(unittest.TestCase):
    def test_scene_boundary_prevents_duration_packing(self):
        parts = vs.partition_shots([
            {"id": "a", "duration": 8, "scene_id": "studio"},
            {"id": "b", "duration": 7, "scene_id": "street"},
        ])
        self.assertEqual([part["duration"] for part in parts], [8, 7])

    def test_legacy_plan_keeps_duration_packing(self):
        parts = vs.partition_shots([
            {"id": "a", "duration": 8}, {"id": "b", "duration": 7},
        ])
        self.assertEqual([part["duration"] for part in parts], [15])

    def test_split_emits_typed_references_and_does_not_extend_across_scene(self):
        plan = {
            "aspect_ratio": "16:9",
            "asset_refs": {"digital_human_portraits": ["https://example.com/human.png"],
                           "product_images": ["https://example.com/product.png"]},
            "shots": [
                {"id": "a", "duration": 10, "scene_id": "studio", "visual": "人物展示产品"},
                {"id": "b", "duration": 10, "scene_id": "street", "visual": "户外使用产品"},
            ],
        }
        result = ss.split(plan, client="test", allow_unconfirmed=True)
        self.assertTrue(result["scene_aware"])
        self.assertEqual(len(result["segments"]), 2)
        first, second = result["segments"]
        self.assertEqual([ref["type"] for ref in first["references"]],
                         ["character_identity", "product_identity"])
        self.assertEqual(first["urls"], [ref["url"] for ref in first["references"]])
        self.assertFalse(second["extend_video"])
        self.assertEqual(second["clip_contract"]["scopes"]["clip"]["continuation_mode"],
                         "fresh_scene")

    def test_typed_reference_prompt_has_selective_inheritance(self):
        prompt = seedance_prompt.compile_prompt(
            {"duration": 5, "urls": ["human.png", "board.jpg"]},
            [{"label": "人物", "type": "character_identity", "scope": "scene"},
             {"label": "分镜", "type": "storyboard_composition", "scope": "beat"}],
        )
        self.assertIn("不要复制参考图背景、姿势、构图或光线", prompt)
        # Storyboard reference must instruct the model to READ annotations as
        # motion guidance while NOT rendering them visually.
        self.assertIn("读取导演标注作为运动指导", prompt)
        self.assertIn("绝不在成片中渲染标注本身", prompt)
        self.assertIn("不得扩大到其他镜头", prompt)


if __name__ == "__main__":
    unittest.main()
