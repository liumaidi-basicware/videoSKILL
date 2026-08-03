#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import seedance_prompt  # noqa: E402


class SeedancePromptTests(unittest.TestCase):
    def test_compiles_native_time_ranges_and_reference_roles(self):
        prompt = seedance_prompt.compile_prompt({
            "duration": 5,
            "timeline": [
                {"start": 0, "end": 2, "action": "产品从暗处揭示", "camera": "slow push-in"},
                {"start": 2, "end": 5, "action": "产品旋转展示材质", "camera": "orbit", "sfx": "低沉冲击"},
            ],
        }, [{"label": "产品多视图", "role": "主体", "intent": "作为首帧外观锚定"}], style="premium")
        self.assertIn("0-2秒", prompt)
        self.assertIn("2-5秒", prompt)
        self.assertIn("素材1（产品多视图）：主体", prompt)
        self.assertIn("镜头push", prompt)
        self.assertIn("声音/情绪", prompt)

    def test_audit_requires_reference_unless_explicit_text_to_video(self):
        result = seedance_prompt.audit_segments([{"text": "a"}])
        self.assertFalse(result["ok"])
        allowed = seedance_prompt.audit_segments([{"text": "a", "allow_text2video": True}])
        self.assertTrue(allowed["ok"])

    def test_model_detection(self):
        self.assertTrue(seedance_prompt.is_seedance_model("seedance-2.0"))
        self.assertFalse(seedance_prompt.is_seedance_model("kling-v3-omni-video"))


if __name__ == "__main__":
    unittest.main()
