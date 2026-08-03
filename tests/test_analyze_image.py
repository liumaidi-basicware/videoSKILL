#!/usr/bin/env python3
"""0-1 单测：br_client.analyze_image / pick_vision_model + asset_prep.analyze_image。

背景：Hermes 平台本地 vision_analyze 工具依赖 Hermes 侧单独配置的视觉模型供应商，
客户机大概率没配（"No LLM provider configured for task=vision"），一旦触发会打断
对话流（response.failed 断流）。铁律要求图像理解走客户自己的 BasicRouter key，不
依赖本地 vision 工具 —— 本文件覆盖新增的 br_client.analyze_image() 及其在
asset_prep.py 里的 CLI 包装 analyze_image()。

不发真实网络请求：全部 mock br_client._request / to_image_ref / chat_stream / chat /
list_models / key_setup.load_key。
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

import br_client   # noqa: E402
import asset_prep  # noqa: E402


class PickVisionModelTests(unittest.TestCase):
    def test_offline_falls_back_to_default(self):
        with mock.patch.object(br_client, "list_models", return_value=[]):
            self.assertEqual(br_client.pick_vision_model(), br_client.VISION_MODEL_FALLBACK)

    def test_preference_order_hits_first_available(self):
        live = [
            {"modelId": "qwen3-vl-plus", "online": True, "multimodelTypes": '["image"]'},
            {"modelId": "kimi-k3", "online": True, "multimodelTypes": '["image"]'},
        ]
        with mock.patch.object(br_client, "list_models", return_value=live):
            # kimi-k3 排在偏好序更前面，应该命中它而不是 qwen3-vl-plus
            self.assertEqual(br_client.pick_vision_model(), "kimi-k3")

    def test_offline_models_excluded(self):
        live = [
            {"modelId": "kimi-k3", "online": False, "multimodelTypes": '["image"]'},
            {"modelId": "qwen3.6-plus", "online": True, "multimodelTypes": '["image"]'},
        ]
        with mock.patch.object(br_client, "list_models", return_value=live):
            self.assertEqual(br_client.pick_vision_model(), "qwen3.6-plus")

    def test_non_image_models_excluded(self):
        live = [
            {"modelId": "text-only-model", "online": True, "multimodelTypes": '["text"]'},
        ]
        with mock.patch.object(br_client, "list_models", return_value=live):
            self.assertEqual(br_client.pick_vision_model(), br_client.VISION_MODEL_FALLBACK)

    def test_no_preference_hit_takes_first_sorted_online_vision(self):
        live = [
            {"modelId": "zeta-vl", "online": True, "multimodelTypes": '["image"]'},
            {"modelId": "alpha-vl", "online": True, "multimodelTypes": '["image"]'},
        ]
        with mock.patch.object(br_client, "list_models", return_value=live):
            self.assertEqual(br_client.pick_vision_model(), "alpha-vl")


class AnalyzeImageBrClientTests(unittest.TestCase):
    def test_uploads_local_path_and_uses_stream_first(self):
        with mock.patch.object(br_client, "to_image_ref",
                               return_value="https://cdn/x.png") as m_ref, \
             mock.patch.object(br_client, "pick_vision_model", return_value="kimi-k3"), \
             mock.patch.object(br_client, "chat_stream",
                               return_value="这是一张开放式耳夹耳机产品图") as m_stream, \
             mock.patch.object(br_client, "chat") as m_chat:
            result = br_client.analyze_image("sk-test", "/tmp/fake.png", "描述这张图")

        self.assertEqual(result, "这是一张开放式耳夹耳机产品图")
        m_ref.assert_called_once()
        m_stream.assert_called_once()
        m_chat.assert_not_called()  # 流式成功不应再走非流式

        # image_url 传给 to_image_ref 的应是原始路径，且转出的 hosted url 进了 content
        called_content = m_stream.call_args[0][1][-1]["content"]
        self.assertEqual(called_content[0]["type"], "input_text")
        self.assertEqual(called_content[0]["text"], "描述这张图")
        self.assertEqual(called_content[1]["type"], "input_image")
        self.assertEqual(called_content[1]["image_url"], "https://cdn/x.png")

    def test_falls_back_to_non_stream_on_stream_failure(self):
        with mock.patch.object(br_client, "to_image_ref", return_value="https://cdn/x.png"), \
             mock.patch.object(br_client, "pick_vision_model", return_value="kimi-k3"), \
             mock.patch.object(br_client, "chat_stream", side_effect=RuntimeError("SSE断了")), \
             mock.patch.object(br_client, "chat", return_value="非流式兜底结果") as m_chat:
            result = br_client.analyze_image("sk-test", "/tmp/fake.png", "描述这张图")

        self.assertEqual(result, "非流式兜底结果")
        m_chat.assert_called_once()

    def test_upload_failure_raises_friendly_brerror(self):
        with mock.patch.object(br_client, "to_image_ref",
                               side_effect=RuntimeError("网络超时")):
            with self.assertRaises(br_client.BRError) as ctx:
                br_client.analyze_image("sk-test", "/tmp/fake.png", "描述")
        self.assertIn("图片上传失败", str(ctx.exception))

    def test_explicit_model_overrides_pick_vision_model(self):
        with mock.patch.object(br_client, "to_image_ref", return_value="https://cdn/x.png"), \
             mock.patch.object(br_client, "pick_vision_model") as m_pick, \
             mock.patch.object(br_client, "chat_stream", return_value="ok") as m_stream:
            br_client.analyze_image("sk-test", "/tmp/fake.png", "描述", model="qwen3.6-plus")
        m_pick.assert_not_called()
        self.assertEqual(m_stream.call_args[1]["model"], "qwen3.6-plus")

    def test_system_prompt_prepended_when_given(self):
        with mock.patch.object(br_client, "to_image_ref", return_value="https://cdn/x.png"), \
             mock.patch.object(br_client, "pick_vision_model", return_value="kimi-k3"), \
             mock.patch.object(br_client, "chat_stream", return_value="ok") as m_stream:
            br_client.analyze_image("sk-test", "/tmp/fake.png", "描述",
                                    system_prompt="你是产品分析专家")
        msgs = m_stream.call_args[0][1]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "你是产品分析专家")
        self.assertEqual(msgs[1]["role"], "user")


class AssetPrepAnalyzeImageTests(unittest.TestCase):
    def test_no_api_key_returns_friendly_error_not_exception(self):
        with mock.patch("key_setup.load_key", return_value=None):
            result = asset_prep.analyze_image("aeroclip_s1", "/tmp/fake.png")
        self.assertFalse(result["ok"])
        self.assertIn("BasicRouter key", result["error"])

    def test_success_path_returns_model_and_analysis(self):
        with mock.patch("key_setup.load_key", return_value="sk-test"), \
             mock.patch.object(br_client, "analyze_image",
                               return_value="珍珠白开放式耳夹耳机，耳夹式佩戴，无入耳硅胶塞") as m_analyze, \
             mock.patch.object(br_client, "pick_vision_model", return_value="kimi-k3"):
            result = asset_prep.analyze_image("aeroclip_s1", "/tmp/fake.png")

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "kimi-k3")
        self.assertIn("耳夹", result["analysis"])
        m_analyze.assert_called_once()
        # 默认问题应该被传入（未显式传 question 时）
        passed_question = m_analyze.call_args[0][2]
        self.assertIn("产品", passed_question)

    def test_custom_question_and_model_are_forwarded(self):
        with mock.patch("key_setup.load_key", return_value="sk-test"), \
             mock.patch.object(br_client, "analyze_image", return_value="ok") as m_analyze:
            asset_prep.analyze_image("aeroclip_s1", "/tmp/fake.png",
                                     question="这个颜色是不是珍珠白？",
                                     model="qwen3.6-plus")
        args, kwargs = m_analyze.call_args
        self.assertEqual(args[2], "这个颜色是不是珍珠白？")
        self.assertEqual(kwargs.get("model"), "qwen3.6-plus")

    def test_analysis_exception_returns_error_dict_not_raise(self):
        with mock.patch("key_setup.load_key", return_value="sk-test"), \
             mock.patch.object(br_client, "analyze_image",
                               side_effect=br_client.BRError("图片上传失败: 网络超时")):
            result = asset_prep.analyze_image("aeroclip_s1", "/tmp/fake.png")
        self.assertFalse(result["ok"])
        self.assertIn("图片分析失败", result["error"])


if __name__ == "__main__":
    unittest.main()
