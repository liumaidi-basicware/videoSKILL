#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import kinetic_talk  # noqa: E402


class KineticTalkTests(unittest.TestCase):
    def test_builds_horizontal_props_from_captions(self):
        spec = kinetic_talk.build_spec(
            "input/speaker.mp4", 10,
            [{"start": 0, "end": 3, "text": "开场观点"}],
            palette="green", pip_video_path="input/pip.mp4")
        self.assertEqual(spec["width"], 1920)
        self.assertEqual(spec["height"], 1080)
        self.assertEqual(spec["scenes"][0]["layout"], "intro")
        self.assertTrue(spec["scenes"][0]["pip"])

    def test_rejects_overlapping_scenes(self):
        spec = {
            "width": 1920, "height": 1080, "durationInSeconds": 10,
            "captions": [],
            "scenes": [
                {"start": 0, "end": 6, "layout": "intro", "accent": "blue"},
                {"start": 5, "end": 10, "layout": "cta", "accent": "green"},
            ],
        }
        result = kinetic_talk.validate_spec(spec)
        self.assertFalse(result["ok"])
        self.assertTrue(any("重叠" in error for error in result["errors"]))

    def test_rejects_non_horizontal_output(self):
        result = kinetic_talk.validate_spec({"width": 1080, "height": 1920, "durationInSeconds": 5})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
