import os
import sys
import unittest
import json
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import br_client  # noqa: E402
import video_engine  # noqa: E402


class VideoModelAliasTests(unittest.TestCase):
    def test_known_canonical_seedance_id_maps_to_legacy_name(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[]):
            self.assertEqual(
                br_client._legacy_video_model_name("dreamina-seedance-2-0-260128"),
                "seedance-2.0")

    def test_live_catalog_name_wins_over_fallback(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[{
            "id": "canonical-seedance", "modelName": "seedance-2.0"
        }]):
            self.assertEqual(
                br_client._legacy_video_model_name("canonical-seedance"),
                "seedance-2.0")

    def test_legacy_candidates_prefer_provider_model_name_over_internal_id(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[{
            "modelId": "kling-v3-omni",
            "modelName": "kling-v3-omni-video",
            "online": True,
        }]):
            self.assertEqual(
                br_client._legacy_video_model_candidates("kling-v3-omni"),
                ["kling-v3-omni-video", "kling-v3-omni"])

    def test_create_video_uses_documented_v1_generation_endpoint(self):
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs["body"]))
            return {"code": 200, "data": {"taskId": "task-kling"}}

        with mock.patch.object(br_client, "_request", side_effect=request):
            self.assertEqual(
                br_client.create_video("sk-test", "hello", model="kling-v3-omni"),
                "task-kling")
        self.assertEqual(len(calls), 1)
        method, path, body = calls[0]
        self.assertEqual((method, path), ("POST", "/v1/video-generations"))
        self.assertEqual(body["model"], "kling-v3-omni")
        self.assertIn("imageUrls", body)
        self.assertNotIn("urls", body)

    def test_create_video_submits_provider_model_name_for_canonical_seedance_id(self):
        calls = []

        def request(method, path, **kwargs):
            calls.append(kwargs["body"])
            return {"code": 200, "data": {"taskId": "task-seedance"}}

        with mock.patch.object(br_client, "API_MODE", "v1"), \
             mock.patch.object(br_client, "list_video_models", return_value=[{
                "modelId": "dreamina-seedance-2-0-260128",
                "modelName": "seedance-2.0",
        }]), mock.patch.object(br_client, "_request", side_effect=request):
            self.assertEqual(br_client.create_video(
                "sk-test", "hello", model="dreamina-seedance-2-0-260128"),
                "task-seedance")
        self.assertEqual(calls[0]["model"], "seedance-2.0")

    def test_create_video_retries_with_sibling_kling_submission_name_on_model_not_found(self):
        calls = []

        def request(_method, _path, **kwargs):
            calls.append((kwargs["body"]["model"], kwargs["idempotency_key"]))
            if len(calls) == 1:
                raise br_client.BRError(
                    "HTTP 400: Model not found: kling-v3-omni-video",
                    http_status=400,
                )
            return {"code": 200, "data": {"taskId": "task-kling"}}

        with mock.patch.object(br_client, "list_video_models", return_value=[]), \
             mock.patch.object(br_client, "_request", side_effect=request):
            self.assertEqual(
                br_client.create_video("sk-test", "hello", model="kling-v3-omni-video"),
                "task-kling")

        self.assertEqual(calls, [
            ("kling-v3-omni-video", mock.ANY),
            ("kling-v3-omni", mock.ANY),
        ])
        self.assertNotEqual(calls[0][1], calls[1][1])

    def test_kling_model_check_does_not_query_catalog(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[]), \
             mock.patch.object(br_client, "list_models",
                               side_effect=AssertionError("no network")):
            self.assertTrue(br_client._legacy_video_model_name("kling-v3-omni-video"))
        import video_models
        with mock.patch.object(video_models, "_model_catalog",
                               side_effect=AssertionError("no catalog")):
            self.assertTrue(video_models._is_kling_video_model("kling-v3-omni-video"))
            self.assertFalse(video_models._is_kling_video_model("seedance-2.0"))

    def test_prompt_limit_fallback_preserves_confirmed_product_color_contract(self):
        segment = {
            "dialogue": "这颗马卡龙，不只是好看。",
            "text": "【画面】黄色 Momax 1-Vibe Go Lite 马卡龙磁吸无线音箱贴到手机背面。",
            "storyboard_ref": True,
            "storyboard_ref_mode": "native_storyboard",
        }
        compact = video_engine._fit_video_prompt_limit("x" * 5000, segment,
                                                       "seedance-2.0", limit=1200)
        self.assertIn("Seedance-native storyboard/contact sheet", compact)
        self.assertIn("annotation colors/arrows/marks are reading-only", compact)
        self.assertIn("Do NOT render any arrows", compact)
        self.assertIn("NEVER render the video as a sketch", compact)
        self.assertIn("黄色 Momax 1-Vibe Go Lite", compact)
        self.assertIn("macaron color", compact)
        self.assertIn("do not recolor", compact)
        self.assertNotIn("neutral grey", compact)

    def test_prompt_limit_fallback_preserves_kling_panel_artifact_ban(self):
        segment = {
            "dialogue": "磁吸一贴，就很稳。",
            "text": "【画面】黄色 Momax 1-Vibe Go Lite 磁吸到手机背面。",
            "storyboard_ref": True,
            "storyboard_ref_mode": "expanded_panel",
        }
        compact = video_engine._fit_video_prompt_limit("x" * 5000, segment,
                                                       "kling-v3-omni-video", limit=1200)
        self.assertIn("SINGLE 16:9 reference plate", compact)
        self.assertIn("annotation colors/arrows/marks are reading-only", compact)
        self.assertIn("Do NOT render any arrows", compact)
        self.assertIn("NEVER render the video as a sketch", compact)
        self.assertIn("磁吸到手机背面", compact)

    def test_kling_storyboard_ref_uses_single_panel_not_native_storyboard_rules(self):
        segment = {
            "id": "s1",
            "storyboard_ref": True,
            "storyboard_ref_mode": "expanded_panel",
            "seedance_native": False,
            "text": "【画面】黄色产品单镜头特写。",
        }
        prompt = video_engine._submission_text(
            segment, "kling-v3-omni-video", storyboard_ref=True)
        self.assertIn("SINGLE 16:9 reference plate", prompt)
        self.assertIn("not a multi-panel board", prompt)
        self.assertNotIn("twelve-panel storyboard contact sheet", prompt)
        self.assertNotIn("panel 1", prompt)
        self.assertNotIn("12格故事板", prompt)

    def test_seedance_native_mode_uses_seedance_storyboard_rules(self):
        segment = {
            "id": "s1",
            "storyboard_ref": True,
            "storyboard_ref_mode": "native_storyboard",
            "seedance_native": True,
            "text": "【画面】按多格故事板执行。",
        }
        prompt = video_engine._submission_text(
            segment, "seedance-2.0", storyboard_ref=True)
        self.assertIn("Seedance-native storyboard", prompt)
        self.assertIn("FINAL storyboard/contact sheet", prompt)
        self.assertNotIn("SINGLE 16:9 reference plate", prompt)
        self.assertTrue(video_engine._uses_native_storyboard_prompt(
            segment, "seedance-2.0"))


if __name__ == "__main__":
    unittest.main()
