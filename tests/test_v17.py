"""
v17 零到一测试套件 — 纯本地、无网络、无 API Key
覆盖「字幕叠加 + 位置智能推荐」总方案 + hf_engine alpha/精确定位扩展。

  T01  hf_engine._fmt_from_out: 扩展名/显式覆盖推断
  T02  hf_engine._scene_position_css: 精确像素定位（bottom/left/right/max-height）
  T03  hf_engine._scene_position_css: 无精确像素回退 pos 语义档位
  T04  hf_engine.build_html: transparent 背景
  T05  hf_engine.build_html: 精确定位 + accent 高亮进入 HTML
  T06  hf_engine.build_html: color 背景仍可用（回归）
  T07  subtitle_overlay._extract_json_block: ```json``` / 裸 {} / 失败
  T08  subtitle_overlay._pick_vision_model: 离线兜底
  T09  subtitle_overlay._pick_vision_model: mock 列表按偏好命中
  T10  subtitle_overlay._default_safe_zone: 竖屏比例合理
  T11  subtitle_overlay.build_scenes: safe_zone → 精确像素 scene + transparent
  T12  subtitle_overlay.build_scenes: 逐句 override 生效
  T13  subtitle_overlay.analyze: 视觉模型 mock → 解析安全区
  T14  subtitle_overlay.analyze: 模型失败 → 兜底安全区
  T15  subtitle_overlay.compose: mock ffmpeg → overlay 命令正确 + 验证阈值
  T16  subtitle_overlay.run: 全链路 mock（analyze+render+compose）
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import hf_engine as hf
import subtitle_overlay as so


def _wf(path, data):
    with open(path, "wb") as f:
        f.write(data)


class TestHfFormat(unittest.TestCase):
    def test_01_fmt_from_out(self):
        self.assertEqual(hf._fmt_from_out("a/x.mov"), "mov")
        self.assertEqual(hf._fmt_from_out("a/x.webm"), "webm")
        self.assertEqual(hf._fmt_from_out("a/x.mp4"), "mp4")
        self.assertEqual(hf._fmt_from_out("a/x.mkv"), "mp4")   # 未知→mp4
        self.assertEqual(hf._fmt_from_out("a/x.mp4", fmt="mov"), "mov")  # 显式覆盖


class TestScenePositionCss(unittest.TestCase):
    def test_02_precise_px(self):
        sc = {"bottom_px": 300, "left_px": 60, "right_px": 60, "max_height_px": 400}
        css = hf._scene_position_css(sc, 1080, 1920, 90)
        self.assertIn("position:absolute", css)
        self.assertIn("bottom:300px", css)
        self.assertIn("left:60px", css)
        self.assertIn("right:60px", css)
        self.assertIn("max-height:400px", css)

    def test_03_fallback_pos(self):
        css = hf._scene_position_css({"pos": "lower"}, 1080, 1920, 84)
        self.assertIn("top:", css)
        self.assertIn("width:1080px", css)


class TestBuildHtml(unittest.TestCase):
    def test_04_transparent_bg(self):
        spec = {"resolution": [1080, 1920], "fps": 30,
                "background": {"type": "transparent"},
                "scenes": [{"text": "hi", "start": 0, "end": 2}]}
        html = hf.build_html(spec)
        self.assertIn("background:transparent;", html)

    def test_05_precise_and_accent(self):
        spec = {"resolution": [1080, 1920], "fps": 30,
                "background": {"type": "transparent"},
                "scenes": [{"text": "65W [[快充]]", "start": 0, "end": 2,
                            "bottom_px": 250, "left_px": 40, "right_px": 40, "size": 88}]}
        html = hf.build_html(spec)
        self.assertIn("bottom:250px", html)
        self.assertIn('class="accent"', html)
        self.assertIn("font-size:88px", html)

    def test_06_color_bg_regression(self):
        spec = {"resolution": [1080, 1920], "fps": 30,
                "background": {"type": "color", "color": "#0B1220"},
                "scenes": [{"text": "hi", "start": 0, "end": 2, "pos": "center"}]}
        html = hf.build_html(spec)
        self.assertIn("background:#0B1220;", html)
        self.assertIn("top:", html)


class TestVisionHelpers(unittest.TestCase):
    def test_07_extract_json_block(self):
        self.assertEqual(so._extract_json_block('```json\n{"a":1}\n```')["a"], 1)
        self.assertEqual(so._extract_json_block('noise {"b":2} tail')["b"], 2)
        self.assertIsNone(so._extract_json_block("no json here"))
        self.assertIsNone(so._extract_json_block(""))

    def test_08_pick_vision_offline_fallback(self):
        with patch.object(so, "_list_vision_models", return_value=set()):
            self.assertEqual(so._pick_vision_model(), so.VISION_FALLBACK)

    def test_09_pick_vision_preference(self):
        with patch.object(so, "_list_vision_models", return_value={"kimi-k2.6", "qwen3.6-plus"}):
            self.assertEqual(so._pick_vision_model(), "qwen3.6-plus")  # 偏好序第一命中
        with patch.object(so, "_list_vision_models", return_value={"kimi-k2.6"}):
            self.assertEqual(so._pick_vision_model(), "kimi-k2.6")

    def test_10_default_safe_zone(self):
        d = so._default_safe_zone(1080, 1920)
        self.assertTrue(d["_fallback"])
        self.assertIn("bottom_px", d["safe_zone"])
        self.assertLess(d["safe_zone"]["bottom_px"], 1920)


class TestBuildScenes(unittest.TestCase):
    def test_11_safezone_to_scenes(self):
        lines = [{"text": "a", "start": 0, "end": 2}, {"text": "b", "start": 2, "end": 4}]
        sz = {"safe_zone": {"bottom_px": 280, "left_px": 50, "right_px": 50, "max_height_px": 380},
              "font_size_main": 92}
        spec = so.build_scenes(lines, sz)
        self.assertEqual(spec["background"]["type"], "transparent")
        self.assertEqual(spec["scenes"][0]["bottom_px"], 280)
        self.assertEqual(spec["scenes"][0]["size"], 92)
        self.assertEqual(spec["duration"], 4)

    def test_12_line_override(self):
        lines = [{"text": "a", "start": 0, "end": 2, "bottom_px": 999, "size": 120}]
        sz = {"safe_zone": {"bottom_px": 280}, "font_size_main": 92}
        spec = so.build_scenes(lines, sz)
        self.assertEqual(spec["scenes"][0]["bottom_px"], 999)  # 逐句覆盖
        self.assertEqual(spec["scenes"][0]["size"], 120)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v17a_")
        self.vid = os.path.join(self.tmp, "v.mp4")
        _wf(self.vid, b"\x00\x00\x00\x18ftypmp42")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_common(self):
        fake_frames = ([os.path.join(self.tmp, "f0.jpg")], self.tmp)
        _wf(fake_frames[0][0], b"x")
        return fake_frames

    def test_13_analyze_success(self):
        frames = self._patch_common()
        good = '```json\n{"safe_zone":{"bottom_px":300,"left_px":40,"right_px":40,"max_height_px":400},"font_size_main":90,"font_size_sub":60}\n```'
        with patch.object(so, "key_setup", create=True), \
             patch("key_setup.load_key", return_value="sk-x"), \
             patch("ocr_check.extract_frames", return_value=frames), \
             patch("br_client.to_image_ref", return_value="http://x/f.jpg"), \
             patch.object(so, "_pick_vision_model", return_value="qwen3.6-plus"), \
             patch("br_client.chat", return_value=good):
            r = so.analyze(self.vid, frames=1, verbose=False)
        self.assertEqual(r["safe_zone"]["bottom_px"], 300)
        self.assertEqual(r["model"], "qwen3.6-plus")
        self.assertNotIn("_fallback", r)

    def test_14_analyze_model_fail_fallback(self):
        frames = self._patch_common()
        with patch("key_setup.load_key", return_value="sk-x"), \
             patch("ocr_check.extract_frames", return_value=frames), \
             patch("br_client.to_image_ref", return_value="http://x/f.jpg"), \
             patch.object(so, "_pick_vision_model", return_value="qwen3.6-plus"), \
             patch("br_client.chat", side_effect=RuntimeError("network")):
            r = so.analyze(self.vid, frames=1, verbose=False)
        self.assertTrue(r["_fallback"])
        self.assertIn("bottom_px", r["safe_zone"])


class TestComposeRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v17c_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_15_compose_cmd_and_verify(self):
        vid = os.path.join(self.tmp, "in.mp4"); _wf(vid, b"v")
        alpha = os.path.join(self.tmp, "sub.mov"); _wf(alpha, b"a")
        out = os.path.join(self.tmp, "out.mp4")
        captured = {}

        def fake_run(args, **kw):
            captured["args"] = args
            # 模拟 ffmpeg 产出成片
            _wf(args[-1], b"o" * 1000)
            m = MagicMock(); m.returncode = 0; m.stdout = b""
            return m

        with patch("ocr_check._ffmpeg_bins", return_value=("/usr/bin/ffmpeg", "/usr/bin/ffprobe")), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.object(so, "_probe_duration", return_value=2.0), \
             patch.object(so, "_ffprobe_frame_bytes", return_value=1600 * 1024):
            r = so.compose(vid, alpha, out, verify_min_kb=200, verbose=False, require_alpha=False)
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["verify_kb"], 200)
        # overlay=0:0:format=auto 出现在 filter_complex
        joined = " ".join(captured["args"])
        self.assertIn("overlay=0:0:format=auto", joined)
        self.assertIn("libx264", joined)

    def test_16_run_full_chain(self):
        vid = os.path.join(self.tmp, "in.mp4"); _wf(vid, b"v")
        out = os.path.join(self.tmp, "final.mp4")
        lines = [{"text": "hi", "start": 0, "end": 2}]
        sz = {"safe_zone": {"bottom_px": 300, "left_px": 40, "right_px": 40, "max_height_px": 400},
              "font_size_main": 90, "model": "qwen3.6-plus"}

        def fake_render(spec, out_path, **kw):
            _wf(out_path, b"alpha")
            return out_path

        with patch.object(so, "analyze", return_value=sz), \
             patch("hf_engine.render", side_effect=fake_render), \
             patch.object(so, "compose", return_value={"ok": True, "out": out, "verify_kb": 1600.0}):
            r = so.run(vid, lines, out, keep_intermediate=True, verbose=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["safe_zone"]["bottom_px"], 300)
        self.assertEqual(r["vision_model"], "qwen3.6-plus")


class TestFixesV17(unittest.TestCase):
    """v17 修复回归：阈值缩放 / 时间轴校验 / _write_text 建目录 / compose 自动阈值。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v17f_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_17_verify_threshold_scales(self):
        # 基准竖屏 1080x1920 → 200KB
        self.assertEqual(so._verify_threshold_kb(1080, 1920), 200.0)
        # 横屏同像素 1920x1080 → 同 200（像素数相同）
        self.assertEqual(so._verify_threshold_kb(1920, 1080), 200.0)
        # 720p 竖屏 720x1280 → 明显低于 200（不误判）
        self.assertLess(so._verify_threshold_kb(720, 1280), 200.0)
        # 极小画面走 floor 下限 40
        self.assertEqual(so._verify_threshold_kb(64, 64), 40.0)

    def test_18_build_scenes_rejects_bad_timeline(self):
        sz = {"safe_zone": {"bottom_px": 300}, "font_size_main": 90}
        with self.assertRaises(ValueError):
            so.build_scenes([{"text": "x", "start": 3, "end": 2}], sz)  # end<=start
        with self.assertRaises(ValueError):
            so.build_scenes([{"text": "x", "start": 1, "end": 1}], sz)  # 相等
        with self.assertRaises(ValueError):
            so.build_scenes([{"text": "x", "start": 0}], sz)            # 缺 end

    def test_19_write_text_creates_dirs(self):
        target = os.path.join(self.tmp, "a", "b", "c", "safe.json")
        so._write_text(target, '{"ok":true}')
        self.assertTrue(os.path.exists(target))
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"ok":true}')

    def test_20_compose_auto_threshold_lowres(self):
        # 720p 横屏成片，帧字节数 ~60KB：写死 200 会误判，自动阈值应放行
        vid = os.path.join(self.tmp, "in.mp4"); _wf(vid, b"v")
        alpha = os.path.join(self.tmp, "sub.mov"); _wf(alpha, b"a")
        out = os.path.join(self.tmp, "out.mp4")

        def fake_run(args, **kw):
            _wf(args[-1], b"o" * 1000)
            m = MagicMock(); m.returncode = 0; m.stdout = b""
            return m

        with patch("ocr_check._ffmpeg_bins", return_value=("/ff", "/fp")), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.object(so, "_probe_duration", return_value=2.0), \
             patch.object(so, "_ffprobe_frame_bytes", return_value=60 * 1024):
            r = so.compose(vid, alpha, out, width=1280, height=720, verbose=False,
                          require_alpha=False)
        # 1280x720 自动阈值 = 200*(921600/2073600) ≈ 88.9KB，远低于写死的 200
        self.assertLess(r["verify_min_kb"], 200)   # 阈值确实随分辨率下调
        self.assertGreater(r["verify_min_kb"], 40)  # 但高于 floor

    def test_21_compose_floor_allows_tiny(self):
        vid = os.path.join(self.tmp, "in.mp4"); _wf(vid, b"v")
        alpha = os.path.join(self.tmp, "sub.mov"); _wf(alpha, b"a")
        out = os.path.join(self.tmp, "out.mp4")

        def fake_run(args, **kw):
            _wf(args[-1], b"o" * 1000)
            m = MagicMock(); m.returncode = 0; m.stdout = b""
            return m

        with patch("ocr_check._ffmpeg_bins", return_value=("/ff", "/fp")), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.object(so, "_probe_duration", return_value=2.0), \
             patch.object(so, "_ffprobe_frame_bytes", return_value=50 * 1024):
            r = so.compose(vid, alpha, out, width=640, height=360, verbose=False,
                          require_alpha=False)
        self.assertEqual(r["verify_min_kb"], 40.0)  # 走 floor
        self.assertTrue(r["ok"])                     # 50KB >= 40 floor


if __name__ == "__main__":
    unittest.main(verbosity=2)
