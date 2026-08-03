import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import prompt_review
import storyboard
import video_engine


class PromptReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "scene_type": "oral-broadcast",
            "product_facts": {"product_name": "灰色产品", "product_color": "灰色"},
            "continuity_contract": {"scene_id": "studio-1"},
            "shots": [{"id": "s1", "dialogue": "第一段", "visual": "主持人介绍"},
                      {"id": "s2", "dialogue": "第二段", "visual": "展示产品"}],
        }

    def test_polish_is_pending_and_confirm_requires_explicit_transition(self):
        response = '{"prompt_zh":"详细中文提示词","negative_prompt_zh":"不要文字"}'
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat", return_value=response):
            review = prompt_review.polish(self.plan, "video", model="review-model")
        self.assertEqual(review["status"], "pending")
        self.assertEqual(len(review["prompts"]), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            prompt_review.save_pending(review, path)
            confirmed = prompt_review.confirm(path)
        self.assertEqual(confirmed["status"], "confirmed")

    def test_video_gate_injects_only_confirmed_prompts(self):
        review = {"status": "confirmed", "stage": "video",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1"},
                              {"shot_id": "s2", "prompt_zh": "P2"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(review, handle, ensure_ascii=False)
            path = handle.name
        try:
            segments = [dict(shot) for shot in self.plan["shots"]]
            video_engine._require_confirmed_prompt_review(path, "video", segments)
            self.assertEqual([s["approved_prompt_zh"] for s in segments], ["P1", "P2"])
        finally:
            os.remove(path)

    def test_storyboard_gate_rejects_missing_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with self.assertRaises(Exception) as error:
                storyboard._load_prompt_review_for_shots(path, self.plan)
        self.assertIn("PROMPT_REVIEW", str(error.exception))
