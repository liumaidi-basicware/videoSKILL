#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import script_splitter as ss  # noqa: E402
import seedance_prompt  # noqa: E402
import video_segmentation as vs  # noqa: E402


class VideoSegmentationTests(unittest.TestCase):
    def test_fifty_seconds_becomes_four_seedance_units(self):
        shots = [{"id": "s1", "duration": 10}, {"id": "s2", "duration": 10},
                 {"id": "s3", "duration": 10}, {"id": "s4", "duration": 10},
                 {"id": "s5", "duration": 10}]
        parts = vs.partition_shots(shots)
        self.assertEqual([p["duration"] for p in parts], [15, 15, 15, 5])
        self.assertTrue(all(p["duration"] <= 15 for p in parts))

    def test_non_oral_long_plan_uses_independent_segments(self):
        plan = {"aspect_ratio": "16:9", "shots": [
            {"id": "a", "duration": 10, "visual": "a"},
            {"id": "b", "duration": 10, "visual": "b"},
        ]}
        result = ss.split(plan, allow_text2video=True, client="test")
        self.assertEqual([s["duration"] for s in result["segments"]], [15, 5])
        self.assertFalse(result["segments"][0].get("extend_video"))
        self.assertFalse(result["segments"][1].get("extend_video"))

    def test_only_oral_broadcast_can_use_model_extension(self):
        base = {"aspect_ratio": "16:9", "shots": [
            {"id": "a", "duration": 10, "visual": "a"},
            {"id": "b", "duration": 10, "visual": "b"},
        ]}
        non_oral = ss.split(base, allow_text2video=True, client="test")
        self.assertEqual(non_oral["generation_strategy"], "segmented")
        self.assertFalse(any(s.get("extend_video") for s in non_oral["segments"]))

        oral = dict(base, scene_type="oral-broadcast")
        oral_result = ss.split(oral, allow_text2video=True, client="test")
        self.assertEqual(oral_result["generation_strategy"], "extend")
        self.assertTrue(oral_result["oral_broadcast"])
        self.assertTrue(oral_result["segments"][1]["extend_video"])

    def test_seedance_prompt_contains_structured_director_details(self):
        prompt = seedance_prompt.compile_prompt({
            "duration": 15,
            "ratio": "16:9",
            "timeline": [{"start": 0, "end": 15, "action": "展示产品",
                           "camera": "slow push-in", "shot_size": "medium close-up",
                           "composition": "rule of thirds", "lighting": "soft key",
                           "character_action": "举起产品", "micro_expression": "自信微笑"}],
            "continuity_out": "人物保持正面持物",
        })
        for term in ("0-15秒", "景别", "构图", "灯光", "人物动作", "微表情", "衔接点"):
            self.assertIn(term, prompt)


if __name__ == "__main__":
    unittest.main()
