import os
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import video_engine  # noqa: E402


class StoryboardRenderHandoffTests(unittest.TestCase):
    def test_missing_revised_storyboard_is_rejected_before_submit(self):
        segment = {
            "id": "s6",
            "text": "test",
            "storyboard_ref": True,
            "storyboard_path": "/tmp/storyboard-revision-that-is-gone.jpg",
        }
        with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(video_engine.br_client, "create_video") as submit:
            with self.assertRaisesRegex(ValueError, "STALE_STORYBOARD"):
                video_engine.render_batch([segment], verbose=False, draft=True)
        submit.assert_not_called()

    def test_storyboard_rules_require_realistic_video(self):
        rules = video_engine.STORYBOARD_VIDEO_RULES
        self.assertIn("photorealistic live-action", rules)
        self.assertIn("NEVER render the video as a sketch", rules)

    def test_revision_fingerprint_is_checked(self):
        with tempfile.TemporaryDirectory() as root:
            storyboard_dir = os.path.join(root, "storyboard")
            os.makedirs(storyboard_dir)
            image = os.path.join(storyboard_dir, "shot_s1.jpg")
            open(image, "wb").close()
            with open(os.path.join(storyboard_dir, "storyboard_result.json"), "w", encoding="utf-8") as handle:
                json.dump({"plan_fingerprint": "new-revision"}, handle)
            segment = {
                "id": "s1", "text": "test", "storyboard_ref": True,
                "storyboard_path": image, "storyboard_dir": storyboard_dir,
                "storyboard_plan_fingerprint": "old-revision",
                "storyboard_result_fingerprint": "old-revision",
            }
            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                 mock.patch.object(video_engine.br_client, "create_video") as submit:
                with self.assertRaisesRegex(ValueError, "STALE_STORYBOARD"):
                    video_engine.render_batch([segment], verbose=False, draft=True)
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
