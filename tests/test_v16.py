"""
v16 零到一测试套件 — 纯本地、无网络、无 API Key
覆盖三项新增能力（按 seedance 官方提示词指南对齐）：
  ① storyboard 人物板：近景人脸↔全身一致性强锁（防 ID 漂移）
  ② storyboard 故事板：默认黑白 + 主体定义句式 + 镜头1→9 分镜 + 双胞胎/无字幕约束
  ③ video_engine：--locked-refs 跨段固定素材锁 + --results-out 合成交接（防工作流断开）

  T01  cast_prompt 含近景人脸↔全身身份锚强锁块
  T02  cast_prompt 大头照作 identity anchor
  T03  shot_prompt 默认黑白（STRICT BLACK-AND-WHITE）
  T04  shot_prompt bw=False → 无黑白块
  T05  shot_prompt shot.color_mode='color' 覆盖默认黑白
  T06  shot_prompt 含主体定义句式（将<特征>定义为<标签>）
  T07  shot_prompt 含镜头1→9 分镜时序
  T08  shot_prompt 含双胞胎全局约束 + 无字幕/Logo/水印
  T09  shot_prompt 无 characters 时不产出主体定义块
  T10  render_storyboard 把 bw 透传给 shot_prompt（signature 检查）
  T11  _apply_locked_refs 注入共享锚到每段最前、去重、限 4 张
  T12  _apply_locked_refs 升 videoType（单图→4，多图→5）
  T13  _apply_locked_refs 不改原 segments（纯函数）
  T14  _apply_locked_refs 空 locked_refs 原样返回
  T15  video_engine main --results-out 落盘 batch_results（mock render_batch）
  T16  video_engine main --locked-refs 注入（mock render_batch 校验 segments）
  T17  results-out JSON 可被 script_splitter.assemble 读取（端到端交接）
"""
import os
import sys
import json
import shutil
import tempfile
import inspect
import unittest
import io
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import storyboard as sb
import video_engine as ve
import script_splitter as ss


def _make_mp4(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")


class TestCastFaceLock(unittest.TestCase):
    def test_01_face_fullbody_lock_block(self):
        plan = {"characters": [{"id": "h", "name": "主播", "appearance": "港式亲和"}]}
        p = sb.cast_prompt(plan)
        self.assertIn("FACE ↔ FULL-BODY IDENTITY LOCK", p)
        self.assertIn("same person", p.lower())

    def test_02_headshot_identity_anchor(self):
        plan = {"characters": [{"id": "h", "name": "主播"}]}
        p = sb.cast_prompt(plan)
        self.assertIn("大头照", p)
        self.assertIn("identity anchor", p)


class TestShotBlackWhite(unittest.TestCase):
    def _plan_shot(self):
        plan = {"project_title": "demo", "characters": [
            {"id": "h", "name": "主播", "costume": "红裙", "appearance": "长发"}]}
        shot = {"id": "s1", "duration": 3, "dialogue": "你好", "characters": ["h"]}
        return plan, shot

    def test_03_default_bw(self):
        plan, shot = self._plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("STRICT BLACK-AND-WHITE", p)
        self.assertIn("monochrome", p.lower())

    def test_04_bw_false_no_block(self):
        plan, shot = self._plan_shot()
        p = sb.shot_prompt(plan, shot, 1, bw=False)
        self.assertNotIn("STRICT BLACK-AND-WHITE", p)

    def test_05_shot_color_mode_override(self):
        plan, shot = self._plan_shot()
        shot["color_mode"] = "color"
        p = sb.shot_prompt(plan, shot, 1)  # bw 默认 True，但 shot 覆盖为 color
        self.assertNotIn("STRICT BLACK-AND-WHITE", p)

    def test_05b_plan_color_mode_override(self):
        plan, shot = self._plan_shot()
        plan["color_mode"] = "color"
        p = sb.shot_prompt(plan, shot, 1)
        self.assertNotIn("STRICT BLACK-AND-WHITE", p)


class TestShotSeedanceConstraints(unittest.TestCase):
    def _plan_shot(self, chars=("h",)):
        plan = {"project_title": "demo", "characters": [
            {"id": "h", "name": "主播", "costume": "红裙", "hair": "长直发", "appearance": "亲和"}]}
        shot = {"id": "s1", "duration": 3, "dialogue": "你好", "characters": list(chars)}
        return plan, shot

    def test_06_subject_definition(self):
        plan, shot = self._plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("SUBJECT DEFINITION", p)
        self.assertIn("定义为主体", p)
        self.assertIn("主播", p)

    def test_07_shot_sequence(self):
        plan, shot = self._plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("ORDERED SHOT SEQUENCE", p)
        self.assertIn("镜头1", p)

    def test_08_antitwin_and_no_text(self):
        plan, shot = self._plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("ANTI-TWIN", p)
        self.assertIn("双胞胎", p)
        self.assertIn("水印", p)
        self.assertIn("Logo", p)

    def test_09_no_characters_no_subject_block(self):
        plan, shot = self._plan_shot(chars=())
        p = sb.shot_prompt(plan, shot, 1)
        self.assertNotIn("SUBJECT DEFINITION", p)


class TestRenderStoryboardSignature(unittest.TestCase):
    def test_10_render_storyboard_has_bw_param(self):
        sig = inspect.signature(sb.render_storyboard)
        self.assertIn("bw", sig.parameters)
        self.assertTrue(sig.parameters["bw"].default)


class TestLockedRefs(unittest.TestCase):
    def test_11_inject_front_dedup_cap4(self):
        segs = [{"text": "a", "urls": ["own1.png"]}]
        locked = ["cast.png", "hero.png", "cast.png"]  # 含重复
        out = ve._apply_locked_refs(segs, locked)
        self.assertEqual(out[0]["urls"][0], "cast.png")
        self.assertIn("own1.png", out[0]["urls"])
        # 去重：cast.png 只出现一次
        self.assertEqual(out[0]["urls"].count("cast.png"), 1)
        self.assertLessEqual(len(out[0]["urls"]), 4)

    def test_12_videotype_bump(self):
        single = ve._apply_locked_refs([{"text": "a"}], ["cast.png"])
        self.assertEqual(single[0]["video_type"], 4)
        multi = ve._apply_locked_refs([{"text": "a"}], ["cast.png", "hero.png"])
        self.assertEqual(multi[0]["video_type"], 5)

    def test_13_pure_no_mutation(self):
        segs = [{"text": "a", "urls": ["own.png"]}]
        ve._apply_locked_refs(segs, ["cast.png"])
        self.assertEqual(segs[0]["urls"], ["own.png"])  # 原对象未变

    def test_14_empty_locked_passthrough(self):
        segs = [{"text": "a"}]
        self.assertIs(ve._apply_locked_refs(segs, None), segs)
        self.assertIs(ve._apply_locked_refs(segs, []), segs)


class TestBwAnchorOrdering(unittest.TestCase):
    def test_18_bw_color_refs_first(self):
        # 黑白故事板：彩色人物板/产品图优先锚定颜色，黑白分镜图垫后
        plan_refs = {"digital_human_portraits": ["/p/host.png"]}
        urls = ss._collect_anchor_urls({}, plan_refs, "/sb/shot_s1.jpg", bw_storyboard=True)
        self.assertEqual(urls[0], "/p/host.png")
        self.assertEqual(urls[-1], "/sb/shot_s1.jpg")

    def test_19_split_default_bw_from_plan(self):
        # plan 默认无 color_mode → 按黑白处理，彩色素材优先
        plan = {"client": "test", "asset_refs": {"product_images": ["https://example.com/prod.png"]},
                "shots": [{"id": "s1", "duration": 3, "visual": "中景"}]}
        tmp = tempfile.mkdtemp(prefix="v16bw_")
        try:
            image = os.path.join(tmp, "shot_s1.jpg")
            _make_mp4(image)  # 占位分镜图
            with open(os.path.join(tmp, "storyboard_result.json"), "w", encoding="utf-8") as f:
                json.dump({"client": "test", "run_id": "r1", "out_dir": tmp,
                           "plan_fingerprint": sb.plan_fingerprint(plan),
                           "shots": [{"shot": {"id": "s1"}, "abspath": image}]}, f)
            r = ss.split(plan, storyboard_dir=tmp, fps=30, client="test",
                         allow_unconfirmed=True,
                         draft_allow_unapproved_storyboard=True)
            seg = r["segments"][0]
            self.assertEqual(seg["urls"][0], "https://example.com/prod.png")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_20_split_color_storyboard_shot_first(self):
        plan = {"client": "test", "asset_refs": {"product_images": ["https://example.com/prod.png"]},
                "shots": [{"id": "s1", "duration": 3, "visual": "中景"}]}
        tmp = tempfile.mkdtemp(prefix="v16col_")
        try:
            with open(os.path.join(tmp, "shot_s1.jpg"), "wb") as f:
                f.write(b"x")
            image = os.path.join(tmp, "shot_s1.jpg")
            with open(os.path.join(tmp, "storyboard_result.json"), "w", encoding="utf-8") as f:
                json.dump({"client": "test", "run_id": "r1", "out_dir": tmp,
                           "plan_fingerprint": sb.plan_fingerprint(plan),
                           "shots": [{"shot": {"id": "s1"}, "abspath": image}]}, f)
            r = ss.split(plan, storyboard_dir=tmp, fps=30, bw_storyboard=False,
                         client="test", allow_unconfirmed=True,
                         draft_allow_unapproved_storyboard=True)
            seg = r["segments"][0]
            self.assertTrue(seg["urls"][0].endswith("shot_s1.jpg"))  # 彩色板：分镜图首
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBatchResultsHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v16_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_segments(self):
        seg_path = os.path.join(self.tmp, "segments.json")
        out1 = os.path.join(self.tmp, "seg_s1.mp4")
        segs = [{"id": "s1", "text": "hi", "video_type": 1, "out_path": out1}]
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segs, f)
        return seg_path, out1

    def test_15_results_out_written(self):
        seg_path, out1 = self._write_segments()
        _make_mp4(out1)
        results_out = os.path.join(self.tmp, "batch_results.json")
        fake = [{"ok": True, "videoUrl": "http://x/a.mp4", "localPath": out1,
                 "absPath": out1, "error": None}]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ve, "render_batch", return_value=fake) as m, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            rc = ve.main(["--batch", seg_path, "--results-out", results_out, "--json", "--draft"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(os.path.exists(results_out))
        with open(results_out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["localPath"], out1)

    def test_16_locked_refs_injected_to_segments(self):
        seg_path, out1 = self._write_segments()
        captured = {}

        def _fake(segments, **kw):
            captured["segments"] = segments
            return [{"ok": True, "localPath": out1, "absPath": out1, "videoUrl": None, "error": None}]

        _make_mp4(out1)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ve, "render_batch", side_effect=_fake), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            ve.main(["--batch", seg_path, "--locked-refs", "cast.png", "hero.png", "--json", "--draft"])
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        self.assertEqual(stderr.getvalue(), "")
        seg0 = captured["segments"][0]
        self.assertEqual(seg0["urls"][0], "cast.png")
        self.assertEqual(seg0["video_type"], 5)

    def test_17_end_to_end_assemble_reads_results(self):
        # video_engine --results-out 落盘 → script_splitter.assemble 直接消费（合成交接不断链）
        seg_path, out1 = self._write_segments()
        _make_mp4(out1)
        results_out = os.path.join(self.tmp, "batch_results.json")
        fake = [{"ok": True, "videoUrl": None, "localPath": out1, "absPath": out1,
                 "error": None, "segment_id": "s1", "ocr_warning": False,
                 "ocr_texts": []}]
        with open(seg_path, encoding="utf-8") as f:
            raw = json.load(f)
        fingerprint = ve.artifact_contract.build_video_handoff(raw[0])["fingerprint"]
        raw[0]["video_handoff_fingerprint"] = fingerprint
        fake[0]["video_handoff_fingerprint"] = fingerprint
        fake[0]["take_fingerprint"] = ve.take_review.take_fingerprint(fake[0])
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ve, "render_batch", return_value=fake), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            ve.main(["--batch", seg_path, "--results-out", results_out, "--json", "--draft"])
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        self.assertEqual(stderr.getvalue(), "")
        with open(seg_path, encoding="utf-8") as f:
            spec = json.load(f)
        with open(results_out, encoding="utf-8") as f:
            results = json.load(f)
        basecut = os.path.join(self.tmp, "basecut.mp4")
        r = ss.assemble(spec, results, basecut)  # 单段 → 直接复制
        self.assertTrue(r["ok"])
        self.assertTrue(os.path.exists(basecut))


if __name__ == "__main__":
    unittest.main(verbosity=2)
