"""
v15 零到一测试套件 — 纯本地、无网络、无 API Key
覆盖三项新增能力：
  ① video_engine 模型选型：seedance 首位 + capability-aware（type4→kling）
  ② video_reverse 三分离：subtitles 规范化 + srt 落盘 + motion_overlay 合成
  ③ final_edit：差异化动效映射 + 字幕轨编译进 shotlist + srt 解析

  T01  video_engine: VIDEO_MODEL_FALLBACK seedance 首位 + DEFAULT_MODEL
  T02  video_engine: _model_supports_type — type4 仅 kling
  T03  video_engine: _pick_video_model 离线 type1 → seedance
  T04  video_engine: _pick_video_model 离线 type4 → kling（能力回落）
  T05  video_engine: _pick_video_model 在线过滤（mock 可用列表）
  T06  video_reverse: SCHEME_SPEC 含三分离 + subtitles 契约
  T07  video_reverse: _sec_to_srt_ts 时间码格式
  T08  video_reverse: _normalize_subtitles 字段容错 + 排序 + index
  T09  video_reverse: write_srt 完整落盘 + 缺时间兜底
  T10  video_reverse: _synthesize_overlay 从 motion_content 合成
  T11  video_reverse: _postprocess_scheme 补 overlay + 规范 subtitles
  T12  video_reverse: _normalize_scheme 从 scenes 保留 motion_suggestion/content
  T13  final_edit: _map_motion_style 命中 + 内容形态兜底
  T14  final_edit: _map_motion_position 命中 + style 默认
  T15  final_edit: _build_motion_overlay 三分离 → props
  T16  final_edit: _build_motion_overlay 向后兼容（仅自由文本）
  T17  final_edit: _srt_ts_to_sec 往返
  T18  final_edit: _parse_srt 解析
  T19  final_edit: _load_subtitles 优先 subtitles，退回 _srt
  T20  final_edit: compile_shotlist 编译 motionOverlay + subtitles 轨
"""
import os
import sys
import shutil
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import video_engine as ve
import video_reverse as vr
import final_edit as fe
import remotion_engine as rme
import storyboard as sb


def _make_mp4(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")


class TestVideoEngineModelSelection(unittest.TestCase):
    def test_01_fallback_order(self):
        self.assertEqual(ve.VIDEO_MODEL_FALLBACK[0], "seedance-2.0")
        self.assertEqual(ve.VIDEO_MODEL_FALLBACK[-1], "wan2.7-i2v")
        self.assertEqual(ve.DEFAULT_MODEL, "seedance-2.0")
        self.assertIn("kling-v3-omni-video", ve.VIDEO_MODEL_FALLBACK)

    def test_02_model_supports_type(self):
        # 网关字段不可用时退回硬编码 CAPS 表；mock 掉网关读取保证离线确定性
        with patch.object(ve, "_model_allow_types", return_value={}):
            self.assertTrue(ve._model_supports_type("seedance-2.0", 1))
            self.assertFalse(ve._model_supports_type("seedance-2.0", 4))  # seedance 不支持单图参考
            self.assertTrue(ve._model_supports_type("seedance-2.0", 5))   # 但支持多图/多主体
            self.assertTrue(ve._model_supports_type("kling-v3-omni-video", 4))
            self.assertFalse(ve._model_supports_type("unknown-model", 4))  # 表外能力未知，fail closed
            self.assertTrue(ve._model_supports_type("seedance-2.0", None))

    def test_02b_model_supports_type_trusts_gateway_field(self):
        # 优先信任网关权威 allowVideoType（即便与硬编码表不同也以网关为准）
        with patch.object(ve, "_model_allow_types",
                          return_value={"seedance-2.0": {1, 2, 3, 5}}):
            self.assertTrue(ve._model_supports_type("seedance-2.0", 5))
            self.assertFalse(ve._model_supports_type("seedance-2.0", 4))

    def test_02c_model_allow_types_parses_gateway_field(self):
        # _model_allow_types 应把网关字符串 "[1,2,3,5]" 解析成 set，且缓存后可读
        fake = [
            {"modelName": "seedance-2.0", "allowVideoType": "[1,2,3,5]"},
            {"modelName": "kling-v3-omni-video", "allowVideoType": "[1,2,3,4,5]"},
            {"modelName": "no-field"},                       # 无字段跳过
            {"modelName": "bad", "allowVideoType": "not-json"},  # 解析失败跳过
        ]
        # 清缓存避免其它用例污染
        for a in ("_allow_video", "_allow_ts_video"):
            if hasattr(ve._model_allow_types, a):
                delattr(ve._model_allow_types, a)
        with patch.object(ve.br_client, "list_models", return_value=fake):
            allow = ve._model_allow_types("video")
        self.assertEqual(allow["seedance-2.0"], {1, 2, 3, 5})
        self.assertEqual(allow["kling-v3-omni-video"], {1, 2, 3, 4, 5})
        self.assertNotIn("no-field", allow)
        self.assertNotIn("bad", allow)
        # 清缓存，避免影响后续用例
        for a in ("_allow_video", "_allow_ts_video"):
            if hasattr(ve._model_allow_types, a):
                delattr(ve._model_allow_types, a)

    def test_03_pick_offline_type1_seedance(self):
        # 离线（查不到可用列表）→ 信任支持该 type 的首选
        with patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve, "_model_allow_types", return_value={}), \
             patch.object(ve, "_available_models_set", return_value=set()):
            self.assertEqual(ve._pick_video_model(video_type=1), "seedance-2.0")

    def test_03b_pick_type5_stays_seedance(self):
        # videoType=5（多图/多主体/人景同框）seedance 自身支持 → 保持 seedance，不回落 kling
        with patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve, "_model_allow_types", return_value={}), \
             patch.object(ve, "_available_models_set", return_value=set()):
            self.assertEqual(ve._pick_video_model(video_type=5), "seedance-2.0")
            self.assertEqual(ve._pick_video_model("seedance-2.0", video_type=5),
                             "seedance-2.0")

    def test_04_pick_offline_type4_kling(self):
        # type4（单张参考图）只有 kling 支持 → 即使 seedance 首位也必须回落 kling
        with patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve, "_model_allow_types", return_value={}), \
             patch.object(ve, "_available_models_set", return_value=set()):
            self.assertEqual(ve._pick_video_model(video_type=4), "kling-v3-omni-video")
            # 显式 preferred=seedance 但 type4 仍回落 kling
            self.assertEqual(ve._pick_video_model("seedance-2.0", video_type=4),
                             "kling-v3-omni-video")

    def test_05_pick_online_filtered(self):
        # 在线：seedance 不在可用列表 → 降级到可用且支持 type 的 kling
        avail = {"kling-v3-omni-video", "wan2.7-i2v"}
        with patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve, "_model_allow_types", return_value={}), \
             patch.object(ve, "_available_models_set", return_value=avail):
            self.assertEqual(ve._pick_video_model(video_type=1), "kling-v3-omni-video")
            self.assertEqual(ve._pick_video_model(video_type=4), "kling-v3-omni-video")

    def test_05b_online_primary_never_selects_wan(self):
        avail = {"seedance-2.0", "kling-v3-omni-video", "wan2.7-i2v"}
        with patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve, "_model_allow_types", return_value={}), \
             patch.object(ve, "_available_models_set", return_value=avail):
            self.assertEqual(ve._pick_video_model(video_type=2), "seedance-2.0")


class TestVideoReverseSubtitles(unittest.TestCase):
    def test_06_scheme_spec_three_way(self):
        s = vr.SCHEME_SPEC
        self.assertIn("motion_suggestion", s)
        self.assertIn("motion_content", s)
        self.assertIn("subtitles", s)
        self.assertIn("三分离", s)

    def test_07_sec_to_srt_ts(self):
        self.assertEqual(vr._sec_to_srt_ts(0), "00:00:00,000")
        self.assertEqual(vr._sec_to_srt_ts(2.4), "00:00:02,400")
        self.assertEqual(vr._sec_to_srt_ts(3661.5), "01:01:01,500")
        self.assertEqual(vr._sec_to_srt_ts(-1), "00:00:00,000")

    def test_08_normalize_subtitles(self):
        subs = [
            {"start": 2.4, "end": 5.0, "content": "第二句"},
            {"start_sec": 0.0, "end_sec": 2.4, "text": "第一句"},
            {"text": ""},  # 空 text 丢弃
        ]
        out = vr._normalize_subtitles(subs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "第一句")  # 按 start 排序
        self.assertEqual(out[0]["index"], 1)
        self.assertEqual(out[1]["text"], "第二句")

    def test_09_write_srt(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "t.srt")
            n = vr.write_srt([
                {"start_sec": 0.0, "end_sec": 2.0, "text": "AAA"},
                {"text": "BBB"},  # 缺时间 → 兜底均分
            ], p, total_duration=6.0)
            self.assertEqual(n, 2)
            with open(p, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("00:00:00,000 --> 00:00:02,000", content)
            self.assertIn("AAA", content)
            self.assertIn("BBB", content)
            # 缺时间条目应被填上合法时间码
            self.assertIn("-->", content.split("BBB")[0].strip().splitlines()[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_10_synthesize_overlay(self):
        shot = {"motion_content": {"title": "标题", "bullets": ["要点A", "要点B"],
                                   "metric": {"value": "3倍", "label": "提效"}}}
        s = vr._synthesize_overlay(shot)
        self.assertIn("标题", s)
        self.assertIn("要点A", s)
        self.assertIn("3倍", s)
        # 无 motion_content → 退回原 overlay
        self.assertEqual(vr._synthesize_overlay({"motion_overlay": "原文本"}), "原文本")

    def test_11_postprocess_scheme(self):
        scheme = {"shots": [
            {"id": "s1", "motion_content": {"title": "卖点", "bullets": ["快"]}},
            {"id": "s2", "motion_overlay": "已有摘要"},
        ], "subtitles": [{"start_sec": 0, "end_sec": 2, "text": "配音"}]}
        out = vr._postprocess_scheme(scheme)
        self.assertIn("卖点", out["shots"][0]["motion_overlay"])  # 合成补上
        self.assertEqual(out["shots"][1]["motion_overlay"], "已有摘要")  # 已有不覆盖
        self.assertEqual(len(out["subtitles"]), 1)
        self.assertEqual(out["subtitles"][0]["index"], 1)

    def test_12_normalize_scenes_keeps_three_way(self):
        # 模型返回 Remotion 式 scenes → normalize 应保留 motion_suggestion/content
        scheme = {"scenes": [
            {"id": "s1", "start": 0, "end": 3, "camera": "推近",
             "motion_suggestion": {"style": "data_card", "position": "corner"},
             "motion_content": {"metric": {"value": "99%", "label": "好评"}}},
        ]}
        out = vr._normalize_scheme(scheme, fps=30)
        self.assertEqual(len(out["shots"]), 1)
        sh = out["shots"][0]
        self.assertEqual(sh["motion_suggestion"]["style"], "data_card")
        self.assertEqual(sh["motion_content"]["metric"]["value"], "99%")


class TestFinalEditMotion(unittest.TestCase):
    def test_13_map_motion_style(self):
        self.assertEqual(fe._map_motion_style("data_card 左下角"), "data_card")
        self.assertEqual(fe._map_motion_style("要点清单"), "bullet_list")
        self.assertEqual(fe._map_motion_style("关键词快闪"), "keyword_flash")
        # 无法命中 → 内容形态兜底
        self.assertEqual(fe._map_motion_style("", has_metric=True), "data_card")
        self.assertEqual(fe._map_motion_style("", n_bullets=2), "bullet_list")
        self.assertEqual(fe._map_motion_style(""), "title_reveal")

    def test_14_map_motion_position(self):
        self.assertEqual(fe._map_motion_position("屏幕底部", "title_reveal"), "bottom")
        self.assertEqual(fe._map_motion_position("左侧", "bullet_list"), "left")
        # 缺省按 style 默认位
        self.assertEqual(fe._map_motion_position("", "data_card"), "corner")
        self.assertEqual(fe._map_motion_position(None, "lower_third"), "lower_third")

    def test_15_build_motion_overlay_three_way(self):
        shot = {
            "motion_suggestion": {"style": "data_card", "position": "corner", "timing": "结尾"},
            "motion_content": {"title": "", "bullets": [], "metric": {"value": "3倍", "label": "提效"}},
        }
        ov = fe._build_motion_overlay(shot)
        self.assertEqual(ov["style"], "data_card")
        self.assertEqual(ov["position"], "corner")
        self.assertEqual(ov["metric"]["value"], "3倍")
        self.assertEqual(ov["timing"], "结尾")

    def test_16_build_motion_overlay_backcompat(self):
        # 仅旧自由文本 → 退回 _overlay_to_content 拆 title/bullets
        shot = {"motion_overlay": "统一网关：降本；增效"}
        ov = fe._build_motion_overlay(shot)
        self.assertIsNotNone(ov)
        self.assertTrue(ov.get("title"))
        self.assertIn("增效", ov.get("bullets", []))
        # 完全无内容 → None
        self.assertIsNone(fe._build_motion_overlay({}))

    def test_17_srt_ts_roundtrip(self):
        self.assertAlmostEqual(fe._srt_ts_to_sec("00:00:02,400"), 2.4, places=3)
        self.assertAlmostEqual(fe._srt_ts_to_sec("01:01:01,500"), 3661.5, places=3)

    def test_18_parse_srt(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "x.srt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\n第一句\n\n"
                        "2\n00:00:02,000 --> 00:00:04,500\n第二句\n")
            cues = fe._parse_srt(p)
            self.assertEqual(len(cues), 2)
            self.assertEqual(cues[0]["text"], "第一句")
            self.assertAlmostEqual(cues[1]["end_sec"], 4.5, places=3)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_19_load_subtitles(self):
        # 优先 scheme.subtitles
        scheme = {"subtitles": [{"start_sec": 0, "end_sec": 2, "text": "内嵌"}]}
        subs = fe._load_subtitles(scheme)
        self.assertEqual(subs[0]["text"], "内嵌")
        # 无 subtitles 但有 _srt → 解析文件
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "y.srt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,500\n来自srt\n")
            subs2 = fe._load_subtitles({"_srt": p})
            self.assertEqual(subs2[0]["text"], "来自srt")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_20_compile_shotlist_motion_and_subtitles(self):
        tmp = tempfile.mkdtemp()
        try:
            bc = os.path.join(tmp, "basecut.mp4")
            _make_mp4(bc)
            scheme = {
                "fps": 30, "width": 1080, "height": 1920,
                "shots": [
                    {"id": "s1", "start_sec": 0, "end_sec": 3, "camera_move": "推近",
                     "motion_suggestion": {"style": "data_card", "position": "corner"},
                     "motion_content": {"metric": {"value": "99%", "label": "好评率"}},
                     "transition_to_next": "淡入"},
                    {"id": "s2", "start_sec": 3, "end_sec": 5, "camera_move": "固定",
                     "motion_content": {"title": "核心卖点", "bullets": ["省心", "省钱"]}},
                ],
                "subtitles": [
                    {"start_sec": 0.0, "end_sec": 2.5, "text": "第一句配音"},
                    {"start_sec": 2.5, "end_sec": 5.0, "text": "第二句配音"},
                ],
            }
            sl = fe.compile_shotlist(scheme, bc)
            # 动效层：结构化 motionOverlay
            self.assertEqual(sl["shots"][0]["motionOverlay"]["style"], "data_card")
            self.assertEqual(sl["shots"][1]["motionOverlay"]["style"], "bullet_list")
            self.assertEqual(sl["shots"][1]["title"], "核心卖点")  # 向后兼容仍填
            # 字幕轨：全局绝对帧
            self.assertIn("subtitles", sl)
            self.assertEqual(len(sl["subtitles"]), 2)
            self.assertEqual(sl["subtitles"][0]["fromFrame"], 0)
            self.assertEqual(sl["subtitles"][0]["durationInFrames"], 75)  # 2.5*30
            self.assertEqual(sl["subtitles"][1]["fromFrame"], 75)  # 2.5*30
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRemotionRenderOutputGuard(unittest.TestCase):
    """render() 出片存在性校验：returncode=0 但没落盘必须判失败（防假成功）。"""

    def _fake_proc(self, rc):
        class P:
            returncode = rc
        return P()

    def test_21_returncode0_no_file_raises(self):
        # 模拟 Remotion 返回 0 但不写文件 → 必须 SystemExit，不能报 ok
        tmp = tempfile.mkdtemp()
        try:
            sl = os.path.join(tmp, "sl.json")
            with open(sl, "w") as f:
                f.write('{"width":1080,"height":1920,"fps":30,"shots":[]}')
            out = os.path.join(tmp, "final.mp4")  # 故意不创建
            with patch.object(rme, "ensure_deps", lambda *a, **k: None), \
                 patch.object(rme, "ensure_ffmpeg_on_path", lambda: (True, "mock")), \
                 patch.object(rme, "find_chrome_executable", lambda: None), \
                 patch.object(rme, "fix_chrome_headless_shell", lambda: (False, "mock")), \
                 patch.object(rme.subprocess, "run", lambda *a, **k: self._fake_proc(0)):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        rme.render(sl, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_22_returncode0_with_file_ok(self):
        # returncode=0 且文件真实落盘 → 返回 out 路径
        tmp = tempfile.mkdtemp()
        try:
            sl = os.path.join(tmp, "sl.json")
            with open(sl, "w") as f:
                f.write('{"width":1080,"height":1920,"fps":30,"shots":[]}')
            out = os.path.join(tmp, "final.mp4")

            def fake_run(*a, **k):
                # 模拟 Remotion 真的写出文件
                command = a[0]
                _make_mp4(command[4])
                return self._fake_proc(0)

            with patch.object(rme, "ensure_deps", lambda *a, **k: None), \
                  patch.object(rme, "ensure_ffmpeg_on_path", lambda: (True, "mock")), \
                  patch.object(rme, "find_chrome_executable", lambda: None), \
                  patch.object(rme, "_media_output_ok", return_value=True), \
                  patch.object(rme.subprocess, "run", fake_run):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    res = rme.render(sl, out)
            self.assertEqual(os.path.abspath(res), os.path.abspath(out))
            self.assertTrue(os.path.exists(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_23_nonzero_returncode_raises(self):
        # returncode!=0 → 修复重试后仍失败 → SystemExit
        tmp = tempfile.mkdtemp()
        try:
            sl = os.path.join(tmp, "sl.json")
            with open(sl, "w") as f:
                f.write('{"width":1080,"height":1920,"fps":30,"shots":[]}')
            out = os.path.join(tmp, "final.mp4")
            with patch.object(rme, "ensure_deps", lambda *a, **k: None), \
                 patch.object(rme, "ensure_ffmpeg_on_path", lambda: (True, "mock")), \
                 patch.object(rme, "find_chrome_executable", lambda: None), \
                 patch.object(rme, "fix_chrome_headless_shell", lambda: (False, "mock")), \
                 patch.object(rme.subprocess, "run", lambda *a, **k: self._fake_proc(1)):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        rme.render(sl, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestStoryboardCastPromptRobustness(unittest.TestCase):
    """cast_prompt 对 facial_features/body_features 的 dict/string/缺失容错。
    真机实测遇到过 LLM 把 body_features 写成 string 导致 .get() 崩溃。"""

    def test_24_as_feature_dict(self):
        self.assertEqual(sb._as_feature_dict({"height": "170cm"}), {"height": "170cm"})
        self.assertEqual(sb._as_feature_dict("高挑苗条"), {"_raw": "高挑苗条"})
        self.assertEqual(sb._as_feature_dict(None), {})
        self.assertEqual(sb._as_feature_dict(""), {})
        self.assertEqual(sb._as_feature_dict(123), {})

    def test_25_feat_fallback_chain(self):
        # 具体键 > character 顶层键 > _raw 兜底
        self.assertEqual(sb._feat({"eyes": "杏眼"}, "eyes", {}), "杏眼")
        self.assertEqual(sb._feat({}, "eyes", {"eyes": "顶层眼"}), "顶层眼")
        self.assertEqual(sb._feat({"_raw": "整段描述"}, "eyes", {}), "整段描述")
        self.assertEqual(sb._feat({}, "eyes", {}), "")

    def test_26_string_features_no_crash(self):
        # body_features / facial_features 为 string → 不崩，且描述进入 prompt
        plan = {"characters": [{"id": "x", "name": "X",
                                "body_features": "高挑苗条，170cm",
                                "facial_features": "鹅蛋脸，大眼睛"}]}
        p = sb.cast_prompt(plan)
        self.assertIsNotNone(p)
        self.assertIn("高挑苗条，170cm", p)
        self.assertIn("鹅蛋脸，大眼睛", p)

    def test_27_dict_features_preserved(self):
        # dict 形态照常，且末字段为空不再误杀整行（旧 endswith('=') bug 回归）
        plan = {"characters": [{"id": "y",
                                "body_features": {"height": "165cm", "build": "修长"},
                                "facial_features": {"face": "椭圆脸", "eyes": "杏眼"}}]}
        p = sb.cast_prompt(plan)
        for kw in ["165cm", "修长", "椭圆脸", "杏眼"]:
            self.assertIn(kw, p)

    def test_28_missing_and_empty(self):
        self.assertIsNotNone(sb.cast_prompt({"characters": [{"id": "z"}]}))
        self.assertIsNone(sb.cast_prompt({"characters": []}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
