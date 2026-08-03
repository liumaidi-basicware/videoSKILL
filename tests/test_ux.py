import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ux


class UserExperienceTests(unittest.TestCase):
    def test_common_failures_are_actionable(self):
        self.assertIn("sk-", ux.friendly_error("No API key"))
        self.assertIn("额度不足", ux.friendly_error("Insufficient credit"))
        self.assertIn("重试", ux.friendly_error("HTTP 429 rate limited"))
        self.assertIn("网络", ux.friendly_error("network timeout"))

    def test_unknown_failure_preserves_progress_guidance(self):
        message = ux.friendly_error("unexpected service response")
        self.assertIn("已保留现有进度", message)

    def test_progress_copy_changes_for_batch(self):
        message = ux.progress_hint("poll", current=2, total=4)
        self.assertIn("2/4", message)
        self.assertIn("1–3 分钟", message)

    def test_absolute_path_is_absolute(self):
        self.assertTrue(os.path.isabs(ux.absolute_path("output/demo.mp4")))


if __name__ == "__main__":
    unittest.main()
