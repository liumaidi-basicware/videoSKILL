import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import audio_contract
import shotcraft_compile
import shotcraft_qc
import styleframe


class ShotcraftFoundationTests(unittest.TestCase):
    def test_styleframe_has_brand_and_motion_tokens(self):
        tokens = styleframe.build_tokens({"colors": {"primary": "#112233"}})
        self.assertEqual(tokens["brand"]["primary"], "#112233")
        self.assertIn("hold_min_frames", tokens["motion"])

    def test_compiler_rejects_undeclared_cards(self):
        plan = {"width": 1080, "height": 1920, "shots": [{"id": "s1", "duration": 4,
                "postproduction": {"engine": "shotcraft", "card_id": "not-real"}}]}
        with self.assertRaisesRegex(ValueError, "SHOTCRAFT_CARD_UNKNOWN"):
            shotcraft_compile.compile_spec(plan, {})

    def test_compiler_and_qc_accept_core_card(self):
        plan = {"width": 1080, "height": 1920, "fps": 30, "shots": [{"id": "s1", "duration": 4,
                "postproduction": {"engine": "shotcraft", "card_id": "spotlight-hero-card",
                                   "assets": ["@hero"]}}]}
        spec = shotcraft_compile.compile_spec(plan, {})
        report = shotcraft_qc.check(spec)
        self.assertTrue(report["passed"], report)
        self.assertEqual(spec["shots"][0]["durationInFrames"], 120)

    def test_voiceover_contract_forbids_visible_speech(self):
        with self.assertRaisesRegex(ValueError, "VOICEOVER_VISIBLE_SPEECH_FORBIDDEN"):
            audio_contract.validate({"audio_mode": "voiceover", "voice_brief": {"voice_id": "v"},
                                     "allow_visible_speech": True})


if __name__ == "__main__":
    unittest.main()
