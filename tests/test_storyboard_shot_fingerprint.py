import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import script_splitter as splitter
import storyboard


class StoryboardShotFingerprintTests(unittest.TestCase):
    def _result(self, root, plan):
        shots = []
        for shot in plan["shots"]:
            path = os.path.join(root, "shot_%s.jpg" % shot["id"])
            with open(path, "wb") as handle:
                handle.write(shot["id"].encode("ascii"))
            shots.append({"shot": shot, "path": path, "abspath": path,
                          "url": "https://cdn.example/%s.png" % shot["id"],
                          "sha256": storyboard._file_sha256(path),
                          "plan_fingerprint": storyboard.plan_fingerprint(plan),
                          "shot_fingerprint": storyboard.shot_fingerprint(shot)})
        result = {"client": "acme", "run_id": "run-1", "out_dir": root,
                  "plan_fingerprint": storyboard.plan_fingerprint(plan),
                  "expected_shot_ids": [shot["id"] for shot in plan["shots"]],
                  "shots": shots}
        path = os.path.join(root, "storyboard_result.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        storyboard.confirm_storyboard(path)
        return path

    def test_dialogue_only_change_keeps_panels_current(self):
        plan = {"client": "acme", "shots": [
            {"id": "s1", "duration": 3, "visual": "wide product shot", "dialogue": "old"},
            {"id": "s2", "duration": 3, "visual": "host holds product", "dialogue": "old"},
        ]}
        revised = json.loads(json.dumps(plan))
        revised["shots"][0]["dialogue"] = "new line"
        with tempfile.TemporaryDirectory() as root:
            result_path = self._result(root, plan)
            self.assertTrue(storyboard.storyboard_approval_is_current(
                result_path, client="acme", run_id="run-1", out_dir=root,
                plan_fingerprint_value=storyboard.plan_fingerprint(revised)))

    def test_visual_change_marks_only_affected_panel_stale(self):
        plan = {"client": "acme", "shots": [
            {"id": "s1", "duration": 3, "visual": "wide product shot"},
            {"id": "s2", "duration": 3, "visual": "host holds product"},
        ]}
        revised = json.loads(json.dumps(plan))
        revised["shots"][1]["visual"] = "macro detail of product"
        with tempfile.TemporaryDirectory() as root:
            self._result(root, plan)
            _, result = splitter._load_storyboard_result(root)
            self.assertEqual(
                splitter._stale_storyboard_shot_ids(result, revised["shots"], storyboard),
                {"s2"})

    def test_reference_fingerprint_tracks_local_file_content_not_only_path(self):
        with tempfile.TemporaryDirectory() as root:
            ref = os.path.join(root, "usage.jpg")
            with open(ref, "wb") as handle:
                handle.write(b"usage-v1")
            registry = [{"tag": "@usage", "url": ref,
                         "type": "product_usage_identity"}]
            before = storyboard.reference_fingerprint(registry)
            with open(ref, "wb") as handle:
                handle.write(b"usage-v2")
            after = storyboard.reference_fingerprint(registry)
        self.assertNotEqual(before, after)

    def test_subtitle_cleanup_and_motion_elements_do_not_stale_panel(self):
        old = {
            "id": "s1",
            "visual": "product on clean table",
            "scene_prompt": "干净桌面，无、无文字、无水印",
            "prop_prompts": ["手机界面"],
            "motion_elements": ["字幕"],
        }
        revised = {
            "id": "s1",
            "visual": "product on clean table",
            "scene_prompt": "干净桌面，无字幕、无文字、无水印",
            "prop_prompts": ["手机界面字幕。"],
            "motion_elements": ["后期标签动效"],
        }
        self.assertEqual(storyboard.shot_fingerprint(old),
                         storyboard.shot_fingerprint(revised))

    def test_subject_change_still_changes_shot_fingerprint(self):
        base = {"id": "s1", "visual": "single yellow round speaker on table"}
        revised = {"id": "s1", "visual": "single blue square speaker on table"}
        self.assertNotEqual(storyboard.shot_fingerprint(base),
                            storyboard.shot_fingerprint(revised))


if __name__ == "__main__":
    unittest.main()
