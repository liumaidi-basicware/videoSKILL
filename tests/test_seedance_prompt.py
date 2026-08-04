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
        # New director-grade structure: core instruction + follow-up beats
        self.assertIn("产品从暗处揭示", prompt)
        self.assertIn("镜头push", prompt)
        self.assertIn("2-5秒", prompt)
        self.assertIn("素材1（产品多视图）：主体", prompt)

    def test_kling_camera_first_structure(self):
        """Kling prompts should lead with camera direction."""
        prompt = seedance_prompt.compile_prompt({
            "duration": 5,
            "timeline": [{"start": 0, "end": 5, "action": "产品展示", "camera": "slow push-in"}],
        }, target_model="kling-v3-omni-video")
        # Kling: camera first
        self.assertTrue(prompt.index("push") < prompt.index("产品展示"))

    def test_seedance_subject_first_structure(self):
        """Seedance prompts should lead with subject action."""
        prompt = seedance_prompt.compile_prompt({
            "duration": 5,
            "timeline": [{"start": 0, "end": 5, "action": "产品展示", "camera": "slow push-in"}],
        }, target_model="seedance-2.0")
        # Seedance: subject first
        self.assertTrue(prompt.index("产品展示") < prompt.index("push"))

    def test_storyboard_rules_included_when_storyboard_ref(self):
        prompt = seedance_prompt.compile_prompt({
            "duration": 5, "storyboard_ref": True,
            "timeline": [{"start": 0, "end": 5, "action": "测试"}],
        })
        self.assertIn("故事板是黑白素描预演", prompt)
        self.assertIn("绝不输出素描", prompt)

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
