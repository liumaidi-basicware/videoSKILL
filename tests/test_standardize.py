#!/usr/bin/env python3
"""0-1 单测：br_client 异步图像生成接口 + asset_prep.standardize。

不发真实网络请求 —— 全部 mock br_client._request / to_image_ref / list_image_models /
key_setup.load_key，覆盖新增模块的正常路径、边界（视频模板抽帧、多候选、无 spec 兜底）
和降级（ratio/resolution 不在模型规格内时回退）。
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import br_client          # noqa: E402
import asset_prep         # noqa: E402


class CreateImageGenerationTests(unittest.TestCase):
    """POST /v1/image-generations 请求体构造 + taskId 提取。"""

    def test_body_only_required_fields(self):
        captured = {}

        def fake_request(method, path, api_key=None, body=None, **kw):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return {"code": 200, "message": "ok", "data": {"taskId": "img_123"}}

        with mock.patch.object(br_client, "_request", side_effect=fake_request):
            task_id = br_client.create_image_generation("k", "a cat")

        self.assertEqual(task_id, "img_123")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/v1/image-generations")
        self.assertEqual(captured["body"]["text"], "a cat")
        self.assertEqual(captured["body"]["model"], "seedream-5.0")
        self.assertNotIn("imageUrls", captured["body"])  # 未传参考图不应出现该字段
        self.assertNotIn("resolution", captured["body"])
        self.assertNotIn("ratio", captured["body"])

    def test_body_with_all_optional_fields(self):
        captured = {}

        def fake_request(method, path, api_key=None, body=None, **kw):
            captured["body"] = body
            return {"code": 200, "data": {"taskId": "img_456"}}

        with mock.patch.object(br_client, "_request", side_effect=fake_request):
            br_client.create_image_generation(
                "k", "白底电商主图", model="seedream-5.0",
                image_urls=["https://x/1.png"], count=2,
                resolution="1080p", ratio="3:2", callback_url="https://cb")

        body = captured["body"]
        self.assertEqual(body["imageUrls"], ["https://x/1.png"])
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["resolution"], "1080p")
        self.assertEqual(body["ratio"], "3:2")
        self.assertEqual(body["callbackUrl"], "https://cb")

    def test_no_task_id_raises(self):
        with mock.patch.object(br_client, "_request",
                               return_value={"code": 200, "data": {}}):
            with self.assertRaises(br_client.BRError):
                br_client.create_image_generation("k", "prompt")


class WaitImageGenerationTests(unittest.TestCase):
    """轮询 status pending→success/failed，解析 images JSON 字符串。"""

    def test_success_parses_images_json_string(self):
        calls = {"n": 0}

        def fake_get(api_key, task_id):
            calls["n"] += 1
            if calls["n"] < 2:
                return {"status": "pending", "images": None}
            return {"status": "success",
                    "images": json.dumps(["https://x/1.png", "https://x/2.png"])}

        with mock.patch.object(br_client, "get_image_generation", side_effect=fake_get), \
             mock.patch("time.sleep"):
            urls = br_client.wait_image_generation("k", "img_1", interval=0, max_wait=10)

        self.assertEqual(urls, ["https://x/1.png", "https://x/2.png"])
        self.assertEqual(calls["n"], 2)

    def test_failed_status_raises(self):
        with mock.patch.object(br_client, "get_image_generation",
                               return_value={"status": "failed", "errorMessage": "boom"}):
            with self.assertRaises(br_client.BRError):
                br_client.wait_image_generation("k", "img_1", interval=0, max_wait=10)

    def test_success_without_images_raises(self):
        with mock.patch.object(br_client, "get_image_generation",
                               return_value={"status": "success", "images": "[]"}):
            with self.assertRaises(br_client.BRError):
                br_client.wait_image_generation("k", "img_1", interval=0, max_wait=10)

    def test_timeout_raises(self):
        with mock.patch.object(br_client, "get_image_generation",
                               return_value={"status": "pending", "images": None}), \
             mock.patch("time.sleep"):
            with self.assertRaises(br_client.BRError):
                br_client.wait_image_generation("k", "img_1", interval=5, max_wait=4)


class StandardizeTests(unittest.TestCase):
    """asset_prep.standardize：图片源 / 视频模板源 / 规格降级 / 多候选。"""

    def setUp(self):
        self.client = "unittest_client_%s" % id(self)
        self.tmp_img = os.path.join(SCRIPTS, "_tmp_test_source.png")
        with open(self.tmp_img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")

    def tearDown(self):
        if os.path.isfile(self.tmp_img):
            os.remove(self.tmp_img)
        brief_path = asset_prep._brief_path(self.client)
        # _brief_path points to <assets>/<client>/brief.json. Remove only the
        # temporary client directory, never the shared project assets root.
        client_dir = os.path.dirname(brief_path)
        if os.path.isdir(client_dir):
            import shutil
            shutil.rmtree(client_dir, ignore_errors=True)

    @staticmethod
    def _fake_download(url, dest, **_kwargs):
        with open(dest, "wb") as f:
            f.write(b"png")

    def _patch_common(self, list_image_models_ret=None):
        patches = [
            mock.patch("key_setup.load_key", return_value="sk-test"),
            mock.patch.object(br_client, "to_image_ref", return_value="data:image/png;base64,x"),
            mock.patch.object(br_client, "create_image_generation", return_value="img_task_1"),
            mock.patch.object(br_client, "wait_image_generation",
                              return_value=["https://cdn/a.png"]),
            mock.patch.object(br_client, "download", side_effect=self._fake_download),
            mock.patch.object(br_client, "list_image_models",
                              return_value=list_image_models_ret or []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_image_source_creates_pending_candidate(self):
        self._patch_common()
        result = asset_prep.standardize(
            self.client, self.tmp_img, "改成白底电商主图", tag="hero")

        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(len(result["candidates"]), 1)
        entry = result["candidates"][0]
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["tag"], "hero")
        self.assertEqual(entry["source_kind"], "product_or_screenshot")
        self.assertTrue(os.path.isfile(entry["abspath"]))

        brief = asset_prep._load_brief(self.client)
        self.assertEqual(len(brief["images"]), 1)
        self.assertEqual(brief["images"][0]["status"], "pending")

    def test_create_one_uses_async_retrieve_without_changing_default_model(self):
        captured = {}

        def fake_create(api_key, text, model=None, **kwargs):
            captured["api_key"] = api_key
            captured["text"] = text
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "img_task_1"

        with mock.patch.object(br_client, "create_image",
                               side_effect=AssertionError("legacy sync path used")), \
             mock.patch.object(br_client, "create_image_generation",
                               side_effect=fake_create), \
             mock.patch.object(br_client, "wait_image_generation",
                               return_value=["https://cdn/a.png"]):
            url = asset_prep._create_one(
                "sk-test", "prompt", ["ref"], "1:1", "2k", None)

        self.assertEqual(url, "https://cdn/a.png")
        self.assertEqual(captured["model"], "seedream-5.0")
        self.assertEqual(captured["kwargs"]["image_urls"], ["ref"])
        self.assertEqual(captured["kwargs"]["ratio"], "1:1")
        self.assertEqual(captured["kwargs"]["resolution"], "2k")

    def test_clean_image_uses_gpt_image_2_async_cleanup(self):
        captured = {}

        def fake_create(api_key, text, model=None, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "img_cleanup"

        self._patch_common()
        with mock.patch.object(br_client, "create_image_generation",
                               side_effect=fake_create):
            result = asset_prep.clean_image(
                self.client, self.tmp_img, "清洗成白底产品图")

        self.assertEqual(captured["model"], "gpt-image-2")
        self.assertEqual(captured["kwargs"]["image_urls"],
                         ["data:image/png;base64,x"])
        self.assertEqual(result["via"], "clean_image")
        self.assertEqual(result["status"], "pending")

    def test_cutout_uses_async_image_generation_result_download(self):
        captured = {}

        def fake_create(api_key, text, model=None, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs
            return "img_cutout"

        self._patch_common()
        with mock.patch.object(br_client, "create_image_generation",
                               side_effect=fake_create):
            result = asset_prep.cutout(self.client, self.tmp_img)

        self.assertEqual(captured["model"], "kling-v3-omni-image")
        self.assertEqual(captured["kwargs"]["image_urls"],
                         ["data:image/png;base64,x"])
        self.assertEqual(result["tag"], "cutout")
        self.assertEqual(result["status"], "pending")

    def test_source_cleanup_rejects_non_gpt_image_model(self):
        self._patch_common()
        with self.assertRaises(SystemExit) as error:
            asset_prep.standardize(self.client, self.tmp_img, "prompt", model="seedream-5.0")
        self.assertIn("SOURCE_CLEANUP_MODEL_REQUIRED", str(error.exception))

    def test_video_template_source_extracts_frame(self):
        self._patch_common()
        tmp_video = os.path.join(SCRIPTS, "_tmp_test_template.mp4")
        with open(tmp_video, "wb") as f:
            f.write(b"fake video bytes")
        try:
            fake_frame = os.path.join(SCRIPTS, "_tmp_test_extracted_frame.png")
            with open(fake_frame, "wb") as f:
                f.write(b"frame")

            with mock.patch.object(asset_prep, "_extract_mid_frame", return_value=fake_frame) as m:
                result = asset_prep.standardize(
                    self.client, tmp_video, "按这个模板风格生成同构图产品图", tag="scene")
            m.assert_called_once()
            self.assertEqual(result["candidates"][0]["source_kind"], "video_template_frame")
            # 临时帧用后应被清理
            self.assertFalse(os.path.isfile(fake_frame))
        finally:
            if os.path.isfile(tmp_video):
                os.remove(tmp_video)

    def test_video_template_frame_extraction_failure_raises(self):
        self._patch_common()
        tmp_video = os.path.join(SCRIPTS, "_tmp_test_template2.mp4")
        with open(tmp_video, "wb") as f:
            f.write(b"fake")
        try:
            with mock.patch.object(asset_prep, "_extract_mid_frame", return_value=None):
                with self.assertRaises(SystemExit):
                    asset_prep.standardize(self.client, tmp_video, "prompt")
        finally:
            os.remove(tmp_video)

    def test_ratio_resolution_fallback_when_unsupported_by_model_spec(self):
        captured = {}

        def fake_create(api_key, text, model=None, image_urls=None, count=1,
                        resolution=None, ratio=None):
            captured["ratio"] = ratio
            captured["resolution"] = resolution
            return "img_task_2"

        self._patch_common(list_image_models_ret=[
             {"id": "gpt-image-2", "ratios": ["16:9", "9:16"],
             "resolutions": ["720p", "1080p"]}])
        with mock.patch.object(br_client, "create_image_generation", side_effect=fake_create):
            asset_prep.standardize(self.client, self.tmp_img, "prompt",
                                   ratio="1:1", resolution="4k")

        # 请求的 1:1 / 4k 都不在模型规格里 -> 应回退到规格第一个可选值
        self.assertEqual(captured["ratio"], "16:9")
        self.assertEqual(captured["resolution"], "720p")

    def test_multiple_candidates_get_variant_suffix(self):
        self._patch_common()
        with mock.patch.object(br_client, "wait_image_generation",
                              return_value=["https://cdn/a.png", "https://cdn/b.png"]):
            result = asset_prep.standardize(self.client, self.tmp_img, "prompt", count=2)

        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["variant"], "a")
        self.assertEqual(result["candidates"][1]["variant"], "b")

    def test_missing_api_key_raises(self):
        with mock.patch("key_setup.load_key", return_value=None):
            with self.assertRaises(SystemExit):
                asset_prep.standardize(self.client, self.tmp_img, "prompt")

    def test_source_not_found_raises(self):
        self._patch_common()
        with self.assertRaises(SystemExit):
            asset_prep.standardize(self.client, "/no/such/file.png", "prompt")


if __name__ == "__main__":
    unittest.main()
