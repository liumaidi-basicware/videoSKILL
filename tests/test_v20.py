#!/usr/bin/env python3
"""v20 单元测试 —— subtitle_overlay.analyze() 字幕安全区智能推荐 schema 容错 + 数值兜底。

背景（本轮真实发现的 bug）：
  实测对 3 个不同在线视觉模型（qwen3.6-plus / kimi-k2.6 / glm-5v-turbo）调用 analyze()，
  它们都返回语义正确但结构不同的 JSON（百分比 margin、坐标框 x_min/x_max、嵌套
  safe_zones.action_safe.pixels 等），而不是 prompt 要求的精确 safe_zone.bottom_px/
  left_px/right_px/max_height_px。旧代码 `if not parsed or "safe_zone" not in parsed`
  一旦没对上顶层 key 就直接判失败、丢弃真实分析结果、静默回退成静态默认安全区——
  也就是说这个"智能推荐"功能在真实模型面前实际上从未真正生效过。

  修复：
    1. 首次输出没命中 schema 时，不直接判失败，而是把模型自己的回复喂回去做一次
       纯换算（_reformat_to_schema），让同一个模型把自己的结论转成精确 schema。
    2. 换算这一步本身也可能算错（如把坐标框 x_min/x_max 直接当 margin，导致
       left_px+right_px 几乎吃满整个画面宽度），所以换算结果 + 首次直出命中 schema
       的结果都统一过 _clamp_safe_zone() 做数值夹紧兜底，保证任何路径产出的安全区
       在几何上都是可用的（不会 0 宽/0 高/超出画布）。

  无外部 API 调用，全 mock，覆盖新逻辑与边界。
"""
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import subtitle_overlay as so


class TestClampSafeZone(unittest.TestCase):
    """_clamp_safe_zone 数值兜底：结构对但数值不可用时也要夹回合理边界。"""

    def test_normal_values_pass_through_unchanged(self):
        parsed = {"safe_zone": {"bottom_px": 192, "left_px": 60, "right_px": 60, "max_height_px": 300}}
        out = so._clamp_safe_zone(parsed, width=1080, height=1920)
        sz = out["safe_zone"]
        self.assertEqual(sz["bottom_px"], 192)
        self.assertEqual(sz["left_px"], 60)
        self.assertEqual(sz["right_px"], 60)
        self.assertEqual(sz["max_height_px"], 300)

    def test_coordinate_box_values_get_scaled_down(self):
        """真实复现场景：模型把 x_min=108,x_max=972（坐标框）直接错填成 left_px/right_px，
        导致 left+right=1080 几乎等于整个画布宽度，几乎没有可用文字空间。"""
        parsed = {"safe_zone": {"bottom_px": 100, "left_px": 108, "right_px": 972, "max_height_px": 300}}
        out = so._clamp_safe_zone(parsed, width=1080, height=1920)
        sz = out["safe_zone"]
        self.assertLessEqual(sz["left_px"] + sz["right_px"], int(1080 * 0.8))
        # 保持原始比例（108:972 ≈ 1:9）
        self.assertAlmostEqual(sz["left_px"] / float(sz["right_px"]), 108 / 972.0, places=2)

    def test_negative_and_non_numeric_values_default_safely(self):
        parsed = {"safe_zone": {"bottom_px": -50, "left_px": "abc", "right_px": None, "max_height_px": 300}}
        out = so._clamp_safe_zone(parsed, width=1080, height=1920)
        sz = out["safe_zone"]
        self.assertGreaterEqual(sz["bottom_px"], 0)
        self.assertGreaterEqual(sz["left_px"], 0)
        self.assertGreaterEqual(sz["right_px"], 0)

    def test_bottom_plus_height_exceeding_canvas_gets_shrunk(self):
        parsed = {"safe_zone": {"bottom_px": 1800, "left_px": 40, "right_px": 40, "max_height_px": 800}}
        out = so._clamp_safe_zone(parsed, width=1080, height=1920)
        sz = out["safe_zone"]
        self.assertLessEqual(sz["bottom_px"] + sz["max_height_px"], 1920)

    def test_zero_height_gets_floored_to_minimum(self):
        parsed = {"safe_zone": {"bottom_px": 100, "left_px": 40, "right_px": 40, "max_height_px": 0}}
        out = so._clamp_safe_zone(parsed, width=1080, height=1920)
        sz = out["safe_zone"]
        self.assertGreaterEqual(sz["max_height_px"], int(1920 * 0.10))

    def test_missing_safe_zone_key_uses_defaults_then_clamps(self):
        out = so._clamp_safe_zone({}, width=1080, height=1920)
        sz = out["safe_zone"]
        for k in ("bottom_px", "left_px", "right_px", "max_height_px"):
            self.assertIn(k, sz)
            self.assertGreaterEqual(sz[k], 0)


class TestReformatToSchemaClamped(unittest.TestCase):
    """_reformat_to_schema 换算成功后也要过 clamp，不能直接放行不可用数值。"""

    @patch("br_client.chat")
    def test_reformat_result_gets_clamped(self, mock_chat):
        # 换算本身"结构对了"但数值仍不可用（坐标框式 left/right）
        mock_chat.return_value = (
            '```json\n{"safe_zone": {"bottom_px": 100, "left_px": 108, '
            '"right_px": 972, "max_height_px": 300}}\n```'
        )
        result = so._reformat_to_schema("fake-key", "qwen3.6-plus", "prior raw resp", 1080, 1920, lambda m: None)
        self.assertIsNotNone(result)
        sz = result["safe_zone"]
        self.assertLessEqual(sz["left_px"] + sz["right_px"], int(1080 * 0.8))

    @patch("br_client.chat")
    def test_reformat_still_unparseable_returns_none(self, mock_chat):
        mock_chat.return_value = "抱歉，我无法确定具体像素值。"
        result = so._reformat_to_schema("fake-key", "qwen3.6-plus", "prior raw resp", 1080, 1920, lambda m: None)
        self.assertIsNone(result)

    @patch("br_client.chat")
    def test_reformat_network_error_returns_none_not_raise(self, mock_chat):
        mock_chat.side_effect = RuntimeError("network down")
        result = so._reformat_to_schema("fake-key", "qwen3.6-plus", "prior raw resp", 1080, 1920, lambda m: None)
        self.assertIsNone(result)


class TestAnalyzeSchemaTolerance(unittest.TestCase):
    """analyze() 端到端：首次输出 schema 不符时不直接判失败，走纠偏转换而非静默回退默认值。"""

    def setUp(self):
        import tempfile
        # analyze() 先 os.path.exists(video_path) 才会走到 mock 的抽帧逻辑，
        # 用真实存在的空文件占位（内容无关，抽帧本身被 mock 掉不会真的读它）。
        fd, self.fake_video = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

    def tearDown(self):
        try:
            os.remove(self.fake_video)
        except OSError:
            pass

    def _fake_frames(self, video_path, n=4):
        return (["/tmp/fake_frame_1.png"], "/tmp/fake_tmpdir")

    @patch("subtitle_overlay._cleanup")
    @patch("br_client.to_image_ref")
    @patch("subtitle_overlay._pick_vision_model")
    @patch("br_client.chat")
    @patch("key_setup.load_key")
    @patch("ocr_check.extract_frames")
    def test_off_schema_response_gets_reformatted_not_discarded(
        self, mock_extract, mock_load_key, mock_chat, mock_pick_model, mock_to_ref, mock_cleanup
    ):
        mock_extract.side_effect = self._fake_frames
        mock_load_key.return_value = "fake-key"
        mock_pick_model.return_value = "qwen3.6-plus"
        mock_to_ref.return_value = "https://fake.cdn/frame1.png"
        # 首次分析：真实复现的"语义对、结构不符 schema"响应（用了 margins_percentage 而非 safe_zone）
        off_schema_resp = (
            '```json\n{"safe_zone_recommendation": {"margins_percentage": '
            '{"bottom": 12, "left": 5, "right": 5}, "max_height_percentage": 18}}\n```'
        )
        # 纠偏转换：把上面语义正确地换算成精确 schema
        reformatted_resp = (
            '```json\n{"safe_zone": {"bottom_px": 230, "left_px": 54, '
            '"right_px": 54, "max_height_px": 346}}\n```'
        )
        mock_chat.side_effect = [off_schema_resp, reformatted_resp]

        result = so.analyze(self.fake_video, frames=1, width=1080, height=1920, verbose=False)

        self.assertIn("safe_zone", result)
        self.assertEqual(result["safe_zone"]["bottom_px"], 230)
        # 必须真的用上了纠偏转换的结果，而不是静默回退的默认值
        self.assertNotIn("_raw", result)
        self.assertEqual(mock_chat.call_count, 2)

    @patch("subtitle_overlay._cleanup")
    @patch("br_client.to_image_ref")
    @patch("subtitle_overlay._pick_vision_model")
    @patch("br_client.chat")
    @patch("key_setup.load_key")
    @patch("ocr_check.extract_frames")
    def test_reformat_also_fails_falls_back_to_default_with_raw_kept(
        self, mock_extract, mock_load_key, mock_chat, mock_pick_model, mock_to_ref, mock_cleanup
    ):
        mock_extract.side_effect = self._fake_frames
        mock_load_key.return_value = "fake-key"
        mock_pick_model.return_value = "qwen3.6-plus"
        mock_to_ref.return_value = "https://fake.cdn/frame1.png"
        mock_chat.side_effect = ["这段视频画面比较居中。", "抱歉，我无法转换为精确像素。"]

        result = so.analyze(self.fake_video, frames=1, width=1080, height=1920, verbose=False)

        self.assertIn("safe_zone", result)
        self.assertIn("_raw", result)  # 兜底路径应保留原始响应供排查

    @patch("subtitle_overlay._cleanup")
    @patch("br_client.to_image_ref")
    @patch("subtitle_overlay._pick_vision_model")
    @patch("br_client.chat")
    @patch("key_setup.load_key")
    @patch("ocr_check.extract_frames")
    def test_on_schema_first_try_still_gets_clamped(
        self, mock_extract, mock_load_key, mock_chat, mock_pick_model, mock_to_ref, mock_cleanup
    ):
        """即使首次直出就命中 schema，数值仍可能不可用（坐标框误填），也要经过 clamp。"""
        mock_extract.side_effect = self._fake_frames
        mock_load_key.return_value = "fake-key"
        mock_pick_model.return_value = "qwen3.6-plus"
        mock_to_ref.return_value = "https://fake.cdn/frame1.png"
        mock_chat.return_value = (
            '```json\n{"safe_zone": {"bottom_px": 100, "left_px": 108, '
            '"right_px": 972, "max_height_px": 300}}\n```'
        )

        result = so.analyze(self.fake_video, frames=1, width=1080, height=1920, verbose=False)

        sz = result["safe_zone"]
        self.assertLessEqual(sz["left_px"] + sz["right_px"], int(1080 * 0.8))
        self.assertEqual(mock_chat.call_count, 1)  # 命中 schema 不需要走纠偏转换


if __name__ == "__main__":
    unittest.main()
