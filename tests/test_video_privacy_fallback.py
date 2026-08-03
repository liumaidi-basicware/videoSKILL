#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import br_client  # noqa: E402
import video_engine as ve  # noqa: E402
import video_models  # noqa: E402


PRIVACY_MESSAGE = "上传的参考图检测到真实人物，因隐私保护不支持生成"


class VideoPrivacyFallbackTests(unittest.TestCase):
    def test_classifier_requires_reference_person_and_rejection(self):
        self.assertTrue(br_client.is_video_reference_privacy_error(PRIVACY_MESSAGE))
        self.assertTrue(br_client.is_video_reference_privacy_error(
            "The uploaded reference image contains a real person and was rejected due to privacy restrictions"))
        self.assertFalse(br_client.is_video_reference_privacy_error("Prompt rejected by content safety policy"))
        self.assertFalse(br_client.is_video_reference_privacy_error("Failed to download reference image"))

    def test_video_task_error_preserves_privacy_category(self):
        error = br_client.video_task_error({"status": "failed", "message": PRIVACY_MESSAGE})
        self.assertIsInstance(error, br_client.BRVideoReferencePrivacyError)
        self.assertIsInstance(error.payload, dict)

    def test_single_rebuilds_kling_prompt_after_seedance_privacy_rejection(self):
        submissions = []

        def create(_key, text, **kwargs):
            submissions.append((text, kwargs))
            return "task-%d" % len(submissions)

        waits = [br_client.BRVideoReferencePrivacyError(PRIVACY_MESSAGE), "https://x/video.mp4"]
        catalog = ve._normalize_model_catalog([
            {"modelId": "provider/kling", "modelName": "kling-v3-omni-video",
             "online": True, "status": True, "allowVideoType": [1, 2, 3, 4, 5],
             "integratedAudio": True},
        ])
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_model_catalog", return_value=catalog), \
             mock.patch.object(video_models, "_model_catalog", return_value=catalog), \
             mock.patch.object(ve.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
             mock.patch.object(ve.br_client, "create_video", side_effect=create), \
             mock.patch.object(ve.br_client, "wait_video", side_effect=waits):
            url, _ = ve.render("原始剧本", video_type=5, urls=["human.png", "product.png"], draft=True,
                               model="seedance-2.0", seedance_native=True, verbose=False)
        self.assertEqual(url, "https://x/video.mp4")
        self.assertEqual([item[1]["model"] for item in submissions],
                          ["seedance-2.0", "provider/kling"])
        self.assertIn("Seedance 2.0 视频提示词", submissions[0][0])
        self.assertNotIn("Seedance 2.0 视频提示词", submissions[1][0])
        self.assertIn("Kling 视频提示词", submissions[1][0])
        self.assertIn("原始剧本", submissions[1][0])
        for key in ("video_type", "urls", "resolution", "ratio", "duration", "negative_prompt"):
            self.assertEqual(submissions[0][1][key], submissions[1][1][key])

    def test_single_does_not_fallback_for_generic_error_or_when_disabled(self):
        for error, enabled in ((br_client.BRError("invalid duration"), True),
                               (br_client.BRVideoReferencePrivacyError(PRIVACY_MESSAGE), False)):
            with self.subTest(error=error, enabled=enabled), \
                 mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
                 mock.patch.object(ve.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                 mock.patch.object(ve.br_client, "create_video", return_value="task") as create, \
                 mock.patch.object(ve.br_client, "wait_video", side_effect=error):
                with self.assertRaises(br_client.BRError):
                    ve.render("剧本", video_type=5, urls=["human.png"], model="seedance-2.0", draft=True,
                              verbose=False, allow_model_fallback=enabled)
                self.assertEqual(create.call_count, 1)

    def test_batch_poll_privacy_failure_replaces_only_failed_task(self):
        submissions = []

        def create(_key, text, **kwargs):
            submissions.append((text, kwargs))
            return "task-%d" % len(submissions)

        states = {
            "task-1": {"status": "failed", "message": PRIVACY_MESSAGE},
            "task-2": {"status": "succeeded", "videoUrl": "https://x/b.mp4"},
            "task-3": {"status": "succeeded", "videoUrl": "https://x/a.mp4"},
        }
        segments = [
            {"id": "a", "text": "A", "duration": 5, "video_type": 5,
             "urls": ["human.png", "product.png"], "seedance_native": True},
            {"id": "b", "text": "B", "duration": 5, "video_type": 2,
             "urls": ["product.png"]},
        ]
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", side_effect=lambda preferred=None, video_type=None, **_: preferred or "seedance-2.0"), \
             mock.patch.object(ve.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
             mock.patch.object(ve.br_client, "create_video", side_effect=create), \
             mock.patch.object(ve.br_client, "get_video", side_effect=lambda _key, task: states[task]), \
             mock.patch("time.sleep", return_value=None):
            results = ve.render_batch(segments, model="seedance-2.0", verbose=False, draft=True)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(len(submissions), 3)
        self.assertEqual(submissions[2][1]["model"], "kling-v3-omni-video")
        self.assertEqual(results[0]["fallback_reason"], "reference_real_person_privacy")
        self.assertEqual(results[0]["model"], "kling-v3-omni-video")
        self.assertEqual(results[1]["retry_count"], 0)

    def test_chain_fallback_keeps_tail_refs_and_extend_url(self):
        submissions = []

        def create(_key, text, **kwargs):
            submissions.append((text, kwargs))
            return "task-%d" % len(submissions)

        waits = ["https://x/first.mp4",
                 br_client.BRVideoReferencePrivacyError(PRIVACY_MESSAGE),
                 "https://x/second.mp4"]
        segments = [
            {"id": "a", "text": "A", "duration": 5, "video_type": 5,
             "urls": ["human.png", "product.png"], "out_path": None},
            {"id": "b", "text": "B", "duration": 5, "video_type": 5,
             "urls": ["human.png", "product.png"], "_locked_urls": True,
             "extend_from_previous": True, "oral_broadcast": True,
             "seedance_native": True, "out_path": None},
        ]
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", side_effect=lambda preferred=None, video_type=None, **_: preferred or "seedance-2.0"), \
             mock.patch.object(ve.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
             mock.patch.object(ve.br_client, "create_video", side_effect=create), \
             mock.patch.object(ve.br_client, "wait_video", side_effect=waits), \
             mock.patch.object(ve.br_client, "download", return_value=None), \
             mock.patch.object(ve, "_extract_last_frame", return_value="tail.png"):
            results = ve.render_chained(segments, model="seedance-2.0", verbose=False, draft=True)
        self.assertTrue(all(result["ok"] for result in results))
        seedance_retry_source = submissions[1][1]
        kling_retry = submissions[2][1]
        self.assertEqual(seedance_retry_source["urls"], kling_retry["urls"])
        self.assertEqual(kling_retry["video_type"], 5)
        self.assertEqual(kling_retry["extend_video_url"], "https://x/first.mp4")
        self.assertEqual(kling_retry["model"], "kling-v3-omni-video")
        self.assertEqual(results[1]["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
