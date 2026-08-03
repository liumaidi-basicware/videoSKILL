import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import video_engine  # noqa: E402


class VideoReferenceHandoffGateTests(unittest.TestCase):
    def test_missing_usage_board_blocks_video(self):
        segment = {
            "id": "s1",
            "required_reference_types": [
                "storyboard_composition", "character_board",
                "product_board", "product_usage_identity"],
            "references": [
                {"type": "storyboard_composition"},
                {"type": "character_board"},
                {"type": "product_board"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "REFERENCE_HANDOFF_INCOMPLETE"):
            video_engine._validate_reference_handoff([segment])

    def test_complete_reference_contract_passes(self):
        types = ["storyboard_composition", "character_board",
                 "product_board", "product_usage_identity"]
        video_engine._validate_reference_handoff([{
            "id": "s1", "required_reference_types": types,
            "references": [{"type": value} for value in types],
        }])

    def test_formal_model_capacity_rejects_seedance_single_image_limit(self):
        with mock.patch.object(video_engine, "_model_catalog", return_value={
                "records": {"seedance": {"image_count": 1}}, "aliases": {}}):
            with self.assertRaisesRegex(ValueError, "REFERENCE_COUNT_UNSUPPORTED"):
                video_engine._validate_model_reference_capacity(
                    "seedance", 4, formal=True)

    def test_model_picker_uses_fallback_with_same_reference_count(self):
        catalog = {
            "records": {
                "seedance": {"active": True, "conflict": False,
                             "allow_types": {5}, "integrated_audio": True,
                             "image_count": 1},
                "kling": {"active": True, "conflict": False,
                           "allow_types": {5}, "integrated_audio": True,
                           "image_count": 4},
            },
            "aliases": {"seedance-2.0": {"seedance"}, "kling": {"kling"}},
        }
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog), \
                mock.patch.object(video_engine, "VIDEO_MODEL_FALLBACK", ["seedance", "kling"]):
            selected = video_engine._pick_video_model(
                "seedance", video_type=5, formal=True,
                allow_fallback=True, reference_count=4)
        self.assertEqual(selected, "kling")


if __name__ == "__main__":
    unittest.main()
