import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import script_splitter as ss  # noqa: E402
import video_engine as ve  # noqa: E402
import aspect_ratio  # noqa: E402


class BatchSchemaAndRatioTests(unittest.TestCase):
    def test_video_engine_accepts_split_result_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"segments": [{"id": "s1", "ratio": "16:9"}],
                       "total_seconds": 3}, f)
            path = f.name
        try:
            segments = ve._load_batch_segments(path)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["ratio"], "16:9")
        finally:
            os.unlink(path)

    def test_video_engine_batch_ratio_override_does_not_mutate_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"id": "s1", "ratio": "16:9"}], f)
            path = f.name
        try:
            segments = ve._load_batch_segments(path, ratio_override="9:16")
            self.assertEqual(segments[0]["ratio"], "9:16")
            with open(path, encoding="utf-8") as source:
                self.assertEqual(json.load(source)[0]["ratio"], "16:9")
        finally:
            os.unlink(path)

    def test_split_ratio_override_keeps_storyboard_ratio_independent(self):
        plan = {"aspect_ratio": "16:9", "shots": [
            {"id": "s1", "duration": 3, "dialogue": "测试", "asset_refs": {
                "product_images": ["/tmp/product.png"]}}
        ]}
        result = ss.split(plan, ratio_override="9:16", client="test",
                          allow_unconfirmed=True)
        self.assertEqual(result["ratio"], "9:16")
        self.assertEqual(result["segments"][0]["ratio"], "9:16")

    def test_video_aspect_ratio_alias_is_used_for_output_ratio(self):
        self.assertEqual(
            aspect_ratio.output_ratio({"aspect_ratio": "16:9", "video_aspect_ratio": "1:1"}),
            "1:1")

    def test_confirmed_render_plan_ratio_overrides_stale_video_alias(self):
        plan = {"aspect_ratio": "16:9", "video_aspect_ratio": "9:16",
                "render_plan": {"status": "confirmed", "ratio": "16:9"},
                "shots": [{"id": "s1", "duration": 4, "dialogue": "测试"}]}
        result = ss.split(plan, client="test", allow_text2video=True)
        self.assertEqual(result["ratio"], "16:9")
        self.assertEqual(result["segments"][0]["ratio"], "16:9")


if __name__ == "__main__":
    unittest.main()
