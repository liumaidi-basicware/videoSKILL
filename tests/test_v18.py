"""
v18 零到一测试套件 — 纯本地、无网络、无 API Key
业务逻辑深度排查发现的两处铁律一致性缺口的修复验证：

  ① 铁律#9（绝不静默交付带字幕残留的成片）—— 此前 _ocr_guard 只把
     [OCR_WARNING] 打到 stdout 日志，results dict / --results-out 落盘的
     batch_results.json 里没有任何字段能反映检测结果；下游若走非交互式
     自动化链路（只读 JSON、不看日志）会完全看不到警告，assemble 照常
     拼接。本轮把 _ocr_guard 改为返回结构化结果并写入 results[i].ocr_warning /
     results[i].ocr_texts，同时给 script_splitter.assemble 加了默认拦截：
     只要有一段 ocr_warning=True 就拒绝拼接并报错，需 allow_ocr_warning=True
     显式放行。

     T01  _ocr_guard 检出字幕 → 返回 {subtitle_detected: True, texts: [...]}
     T02  _ocr_guard 未检出 → subtitle_detected False
     T03  _ocr_guard 不可用（ImportError）→ 返回 None
     T04  render_batch 把 ocr_warning/ocr_texts 写进 results[i]
     T05  render_chained 把 ocr_warning/ocr_texts 写进 results[i]
     T06  assemble 遇到 ocr_warning=True 默认拒绝拼接（RuntimeError）
     T07  assemble allow_ocr_warning=True 时放行
     T08  assemble 对纯字符串路径 results（无法判断）直接放行，不报错
     T09  script_splitter CLI assemble --allow-ocr-warning 透传

  ② 铁律#11（缺锚定素材不得静默降级为纯文本生视频，与
     guide_scaffold.compile_segments 的既有政策对齐）—— script_splitter.split()
     此前对零锚定素材的镜头直接 _pick_video_type(0,...) 返回 1（文生视频），
     无 needs_image 追踪、无 opt-in 开关，与 guide_scaffold 的既有铁律矛盾。
     本轮给 split() 加 allow_text2video 参数（默认 False）：零锚定的镜头默认
     跳过、不生成 segment，id 记入返回值 needs_image；仅显式传 True 才保留
     该镜并走 video_type=1。

     T10  split 零锚定镜头默认跳过，进 needs_image，不生成 segment
     T11  split allow_text2video=True 时该镜头正常生成，video_type=1
     T12  split 有锚定的镜头不受影响，正常生成
     T13  script_splitter CLI split --allow-text2video 透传 + needs_image 提示
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
import io
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import video_engine as ve
import script_splitter as ss
import take_review


def _make_mp4(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")


class TestOcrGuardStructured(unittest.TestCase):
    def _fake_ocr_module(self, report):
        m = MagicMock()
        m.check_video.return_value = report
        return m

    def test_01_detected(self):
        report = {"ocr_available": True, "subtitle_detected": True,
                  "frames_checked": 5,
                  "detections": [{"frame": 2, "texts": [{"confidence": 0.9, "text": "字幕残留"}]}]}
        fake_mod = self._fake_ocr_module(report)
        with patch.dict(sys.modules, {"ocr_check": fake_mod}):
            r = ve._ocr_guard("/tmp/x.mp4", lambda s: None)
        self.assertTrue(r["subtitle_detected"])
        self.assertIn("字幕残留", r["texts"])
        self.assertTrue(r["available"])

    def test_02_not_detected(self):
        report = {"ocr_available": True, "subtitle_detected": False, "frames_checked": 5}
        fake_mod = self._fake_ocr_module(report)
        with patch.dict(sys.modules, {"ocr_check": fake_mod}):
            r = ve._ocr_guard("/tmp/x.mp4", lambda s: None)
        self.assertFalse(r["subtitle_detected"])
        self.assertEqual(r["texts"], [])

    def test_03_unavailable_import_error(self):
        with patch.dict(sys.modules, {"ocr_check": None}):
            r = ve._ocr_guard("/tmp/x.mp4", lambda s: None)
        self.assertIsNone(r)


class TestResultsPropagation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v18_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_segments(self, n=1):
        seg_path = os.path.join(self.tmp, "segments.json")
        outs = []
        segs = []
        for i in range(n):
            out = os.path.join(self.tmp, "seg_s%d.mp4" % (i + 1))
            outs.append(out)
            segs.append({"id": "s%d" % (i + 1), "text": "hi", "video_type": 1, "out_path": out})
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segs, f)
        return seg_path, outs, segs

    def test_04_render_batch_propagates_ocr(self):
        seg_path, outs, segs = self._write_segments(1)
        _make_mp4(outs[0])

        def _fake_ocr_guard(local, log):
            return {"available": True, "subtitle_detected": True, "texts": ["残留字幕"], "frames_checked": 5}

        qc = {"passed": True, "media": {"actual_duration": 5}, "report_path": outs[0] + ".qc.json"}
        with patch.object(ve, "_media_qc_guard", return_value=qc), \
             patch.object(ve, "_ocr_guard", side_effect=_fake_ocr_guard), \
             patch.object(ve.key_setup, "load_key", return_value="k"), \
             patch.object(ve.br_client, "create_video", return_value="task1"), \
             patch.object(ve.br_client, "get_video", return_value={"status": "succeeded", "videoUrl": "http://x/a.mp4"}), \
             patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve.br_client, "list_models", return_value=[]), \
             patch.object(ve, "_available_models_set", return_value=set()), \
             patch.object(ve.br_client, "download", return_value=None):
            results = ve.render_batch(segs, verbose=False, draft=True)
        self.assertTrue(results[0]["ocr_warning"])
        self.assertEqual(results[0]["ocr_texts"], ["残留字幕"])

    def test_05_render_chained_propagates_ocr(self):
        seg_path, outs, segs = self._write_segments(1)
        _make_mp4(outs[0])

        def _fake_ocr_guard(local, log):
            return {"available": True, "subtitle_detected": True, "texts": ["残留字幕"], "frames_checked": 5}

        qc = {"passed": True, "media": {"actual_duration": 5}, "report_path": outs[0] + ".qc.json"}
        with patch.object(ve, "_media_qc_guard", return_value=qc), \
             patch.object(ve, "_ocr_guard", side_effect=_fake_ocr_guard), \
             patch.object(ve.key_setup, "load_key", return_value="k"), \
             patch.object(ve.br_client, "create_video", return_value="task1"), \
             patch.object(ve.br_client, "wait_video", return_value="http://x/a.mp4"), \
             patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve.br_client, "list_models", return_value=[]), \
             patch.object(ve, "_available_models_set", return_value=set()), \
             patch.object(ve.br_client, "to_image_ref", side_effect=lambda u, **kw: u), \
             patch.object(ve.br_client, "download", return_value=None):
            results = ve.render_chained(segs, verbose=False, draft=True)
        self.assertTrue(results[0]["ocr_warning"])
        self.assertEqual(results[0]["ocr_texts"], ["残留字幕"])


class TestSingleRenderOcrGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v18single_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, allow=False):
        out = os.path.join(self.tmp, "single.mp4")

        def fake_render(*args, **kwargs):
            kwargs["ocr_result"].update({
                "available": True, "subtitle_detected": True,
                "texts": ["残留字幕"], "frames_checked": 5,
            })
            return "http://x/a.mp4", out

        argv = ["--text", "hi", "--out", out, "--json", "--draft"]
        if allow:
            argv.append("--allow-ocr-warning")
        stdout = io.StringIO()
        with patch.object(ve, "render", side_effect=fake_render), \
             patch.object(ve.key_setup, "load_key", return_value="k"), \
             patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve.br_client, "list_models", return_value=[]), \
             patch.object(ve, "_available_models_set", return_value=set()), \
             redirect_stdout(stdout):
            rc = ve.main(argv)
        return rc, json.loads(stdout.getvalue())

    def test_warning_blocks_single_delivery(self):
        rc, result = self._run()
        self.assertEqual(rc, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["ocr_texts"], ["残留字幕"])

    def test_explicit_warning_acceptance_allows_delivery(self):
        rc, result = self._run(allow=True)
        self.assertEqual(rc, 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["ocr_warning"])


class TestAssembleOcrGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v18b_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seg_and_result(self, ocr_warning):
        out = os.path.join(self.tmp, "seg_s1.mp4")
        _make_mp4(out)
        segs = [{"id": "s1", "out_path": out, "video_handoff_fingerprint": "fp"}]
        results = [{"ok": True, "segment_id": "s1", "localPath": out,
                    "video_handoff_fingerprint": "fp", "ocr_warning": ocr_warning,
                    "ocr_texts": ["x"] if ocr_warning else []}]
        results[0]["take_fingerprint"] = take_review.take_fingerprint(results[0])
        return segs, results

    def test_06_blocks_on_ocr_warning(self):
        segs, results = self._seg_and_result(True)
        out_path = os.path.join(self.tmp, "basecut.mp4")
        with self.assertRaises(RuntimeError):
            ss.assemble(segs, results, out_path)

    def test_07_allow_ocr_warning_bypasses(self):
        segs, results = self._seg_and_result(True)
        out_path = os.path.join(self.tmp, "basecut.mp4")
        r = ss.assemble(segs, results, out_path, allow_ocr_warning=True)
        self.assertTrue(r["ok"])
        self.assertTrue(os.path.exists(out_path))

    def test_08_string_paths_require_legacy_unsafe(self):
        out = os.path.join(self.tmp, "seg_s1.mp4")
        _make_mp4(out)
        segs = [{"id": "s1", "out_path": out}]
        out_path = os.path.join(self.tmp, "basecut.mp4")
        with self.assertRaisesRegex(TypeError, "STRUCTURED_RESULTS_REQUIRED"):
            ss.assemble(segs, [out], out_path)
        r = ss.assemble(segs, [out], out_path, legacy_unsafe=True)
        self.assertTrue(r["ok"])

    def test_09_cli_allow_ocr_warning_flag(self):
        segs, results = self._seg_and_result(True)
        seg_path = os.path.join(self.tmp, "segments.json")
        results_path = os.path.join(self.tmp, "results.json")
        out_path = os.path.join(self.tmp, "basecut.mp4")
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segs, f)
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                ss.main(["assemble", "--segments", seg_path, "--results", results_path,
                         "--out", out_path, "--draft"])
        # 裸全局 flag 在正式 CLI 已禁用；旧链路必须同时显式声明 legacy unsafe。
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ss.main(["assemble", "--segments", seg_path, "--results", results_path,
                         "--out", out_path, "--allow-ocr-warning"])
            ss.main(["assemble", "--segments", seg_path, "--results", results_path,
                     "--out", out_path, "--allow-ocr-warning", "--legacy-unsafe"])
        self.assertTrue(os.path.exists(out_path))

    def test_09b_results_json_never_falls_back_to_old_segment(self):
        out = os.path.join(self.tmp, "old_seg.mp4")
        _make_mp4(out)
        segs = [{"id": "s1", "out_path": out}]
        result = [{"ok": False, "localPath": None, "error": "task failed"}]
        with self.assertRaises(RuntimeError):
            ss.assemble(segs, result, os.path.join(self.tmp, "basecut.mp4"))


class TestSplitNeedsImageGuard(unittest.TestCase):
    def test_10_zero_anchor_skipped_by_default(self):
        plan = {"shots": [{"id": "s1", "duration": 3, "dialogue": "无图镜头"}]}
        r = ss.split(plan, storyboard_dir=None, fps=30, client="test")
        self.assertEqual(len(r["segments"]), 0)
        self.assertIn("s1", r["needs_image"])

    def test_11_allow_text2video_keeps_segment(self):
        plan = {"shots": [{"id": "s1", "duration": 3, "dialogue": "无图镜头"}]}
        r = ss.split(plan, storyboard_dir=None, fps=30, allow_text2video=True,
                     client="test")
        self.assertEqual(len(r["segments"]), 1)
        self.assertEqual(r["segments"][0]["video_type"], 1)
        self.assertEqual(r["needs_image"], [])

    def test_12_shot_with_anchor_unaffected(self):
        plan = {"asset_refs": {"digital_human_portraits": ["/p/host.png"]},
                "shots": [{"id": "s1", "duration": 3, "dialogue": "有图镜头", "characters": ["host"]}]}
        r = ss.split(plan, storyboard_dir=None, fps=30, client="test",
                     allow_unconfirmed=True)
        self.assertEqual(len(r["segments"]), 1)
        self.assertEqual(r["needs_image"], [])

    def test_13_cli_split_allow_text2video_flag(self):
        tmp = tempfile.mkdtemp(prefix="v18c_")
        try:
            plan = {"shots": [{"id": "s1", "duration": 3, "dialogue": "无图镜头"}]}
            plan_path = os.path.join(tmp, "plan.json")
            out_path = os.path.join(tmp, "segments.json")
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                ss.main(["split", "--plan", plan_path, "--out", out_path,
                         "--client", "test", "--draft"])
            self.assertIn("[待补素材]", stdout.getvalue())
            self.assertIn("--allow-text2video", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data["segments"]), 0)
            self.assertIn("s1", data["needs_image"])

            out_path2 = os.path.join(tmp, "segments2.json")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                ss.main(["split", "--plan", plan_path, "--out", out_path2,
                         "--allow-text2video", "--client", "test", "--draft"])
            self.assertNotIn("[待补素材]", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            with open(out_path2, encoding="utf-8") as f:
                data2 = json.load(f)
            self.assertEqual(len(data2["segments"]), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
