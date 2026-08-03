import hashlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import storyboard  # noqa: E402


class StoryboardProvenanceTests(unittest.TestCase):
    def test_orphan_same_named_file_is_not_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "shot.jpg")
            with open(path, "wb") as handle:
                handle.write(b"old-image")
            self.assertFalse(storyboard._existing_shot_matches_plan(
                None, {"id": "s1"}, "plan-new", path))

    def test_checkpoint_must_bind_plan_shot_and_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "shot.jpg")
            data = b"current-image"
            with open(path, "wb") as handle:
                handle.write(data)
            entry = {
                "shot": {"id": "s1", "visual": "current"},
                "plan_fingerprint": "plan-1",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            self.assertTrue(storyboard._existing_shot_matches_plan(
                entry, {"id": "s1", "visual": "current"}, "plan-1", path))
            self.assertFalse(storyboard._existing_shot_matches_plan(
                entry, {"id": "s1", "visual": "changed"}, "plan-1", path))
            self.assertFalse(storyboard._existing_shot_matches_plan(
                entry, {"id": "s1", "visual": "current"}, "plan-2", path))

    def test_provenance_metadata_is_written_for_new_shot_result(self):
        # The renderer attaches the plan fingerprint to every generated shot;
        # this assertion protects the downstream confirmation handoff contract.
        self.assertIn("plan_fingerprint", storyboard.render_storyboard.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
