"""
v14 零到一测试套件 — 纯本地、无网络、无 API Key
覆盖 remotion-com-skills 组件库集成 + 文档/PPT 内容动效 + 修正版 4/6/7 阶段管线。

  T01  content_scaffold: KIND_REQUIRED 与 KIND_DEFAULT_SECONDS 一致性
  T02  content_scaffold: _seconds_to_frames 取整/下限
  T03  content_scaffold: scaffold — 从 txt 提取标题起骨架
  T04  content_scaffold: validate — 合法 spec 通过
  T05  content_scaffold: validate — 缺必填 prop 报错
  T06  content_scaffold: validate — 占位残留告警 + 未知 kind 报错
  T07  content_scaffold: validate — 时长累计正确
  T08  script_splitter: _find_shot_image 匹配 shot_<id>.*
  T09  script_splitter: _collect_anchor_urls 分镜图优先 + shot 覆盖 plan
  T10  script_splitter: _pick_video_type 分支
  T11  script_splitter: _shot_duration_seconds 显式/估算/下限
  T12  script_splitter: split — 完整拆分 + videoType + text 拼接
  T13  script_splitter: split — 缺分镜图进 missing_images
  T14  script_splitter: assemble — 单段直接复制
  T15  video_reverse: _extract_json_block — ```json``` 块
  T16  video_reverse: _extract_json_block — 裸 {} 兜底 + 失败返回 None
  T17  video_reverse: _pick_vl_model — 离线信任首选
  T18  video_reverse: REVERSE_PROMPT 含禁止项与目标模型占位
  T19  final_edit: _map_move 中英关键词映射
  T20  final_edit: _map_transition 映射 + 默认 cut
  T21  final_edit: _overlay_to_content 标题/要点拆分
  T22  final_edit: compile_shotlist — scheme→shotlist 完整映射
  T23  final_edit: compile_shotlist — 底片不存在报错
  T24  final_edit: _stage_media_to_public — 本地路径拷 public 改相对
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import content_scaffold as cs
import script_splitter as ss
import video_reverse as vr
import final_edit as fe


def _make_jpg(path):
    """写一个极小的合法 JPEG（不依赖 Pillow）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 最小 JPEG: SOI + APP0 + EOI 足够被当作文件存在（渲染测试不解码）
    data = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
    with open(path, "wb") as f:
        f.write(data)


def _make_mp4(path):
    """写一个占位 mp4 文件（仅测试路径逻辑，不解码）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)


class TestContentScaffold(unittest.TestCase):
    def test_01_kind_tables_consistent(self):
        # 每个必填表里的 kind 都应有默认时长
        for k in cs.KIND_REQUIRED:
            self.assertIn(k, cs.KIND_DEFAULT_SECONDS, "kind %s 缺默认时长" % k)

    def test_02_seconds_to_frames(self):
        self.assertEqual(cs._seconds_to_frames(3, 30), 90)
        self.assertEqual(cs._seconds_to_frames(0.5, 30), 15)
        self.assertEqual(cs._seconds_to_frames(0, 30), 1)  # 下限 1

    def test_03_scaffold_extracts_title(self):
        tmp = tempfile.mkdtemp()
        try:
            doc = os.path.join(tmp, "d.txt")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("量子计算入门\n第一章\n")
            spec = cs.scaffold(doc, fps=30, orientation="portrait")
            self.assertEqual(spec["width"], 1080)
            self.assertEqual(spec["height"], 1920)
            self.assertEqual(spec["scenes"][0]["kind"], "hero")
            self.assertEqual(spec["scenes"][0]["props"]["title"], "量子计算入门")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_04_validate_ok(self):
        spec = {"fps": 30, "scenes": [
            {"kind": "hero", "durationInFrames": 60, "props": {"title": "T"}},
            {"kind": "metrics", "durationInFrames": 60,
             "props": {"metrics": [{"value": 9, "label": "x"}]}},
        ]}
        r = cs.validate(spec)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["scene_count"], 2)

    def test_05_validate_missing_required(self):
        spec = {"fps": 30, "scenes": [
            {"kind": "hero", "durationInFrames": 60, "props": {}},  # 缺 title
        ]}
        r = cs.validate(spec)
        self.assertFalse(r["ok"])
        self.assertTrue(any("title" in e for e in r["errors"]))

    def test_06_validate_placeholder_and_unknown_kind(self):
        spec = {"fps": 30, "scenes": [
            {"kind": "hero", "durationInFrames": 60, "props": {"title": "[待填标题]"}},
            {"kind": "bogus", "durationInFrames": 60, "props": {}},
        ]}
        r = cs.validate(spec)
        self.assertFalse(r["ok"])  # 未知 kind → error
        self.assertTrue(any("bogus" in e for e in r["errors"]))
        self.assertTrue(any("占位" in w for w in r["warnings"]))

    def test_07_validate_duration_sum(self):
        spec = {"fps": 30, "scenes": [
            {"kind": "hero", "durationInFrames": 45, "props": {"title": "A"}},
            {"kind": "section", "durationInFrames": 75, "props": {"title": "B"}},
        ]}
        r = cs.validate(spec)
        self.assertEqual(r["total_frames"], 120)
        self.assertEqual(r["total_seconds"], 4.0)


class TestScriptSplitter(unittest.TestCase):
    def test_08_find_shot_image(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "shot_s1.jpg")
            _make_jpg(path)
            self.assertEqual(ss._find_shot_image({"s1": path}, "s1"), path)
            self.assertIsNone(ss._find_shot_image({"s1": path}, "s9"))
            self.assertIsNone(ss._find_shot_image({}, "s1"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_09_collect_anchor_urls(self):
        plan_refs = {"digital_human_portraits": ["/p/host.png"],
                     "product_images": ["/p/prod.png"]}
        shot = {"asset_refs": {"product_images": ["/p/prod2.png"]}}
        # 默认黑白故事板：彩色素材优先，黑白分镜图垫后作构图提示
        urls = ss._collect_anchor_urls(shot, plan_refs, "/sb/shot_s1.jpg")
        self.assertEqual(urls[0], "/p/host.png")          # 彩色人物板首位（锚定颜色）
        self.assertEqual(urls[-1], "/sb/shot_s1.jpg")     # 黑白分镜图垫后
        # 彩色故事板模式：分镜图作首帧锚优先（旧语义）
        urls_color = ss._collect_anchor_urls(shot, plan_refs, "/sb/shot_s1.jpg",
                                             bw_storyboard=False)
        self.assertEqual(urls_color[0], "/sb/shot_s1.jpg")
        # shot 级 product_images 覆盖 plan 级
        self.assertIn("/p/prod2.png", urls)
        self.assertNotIn("/p/prod.png", urls)
        # 上限 4
        self.assertLessEqual(len(urls), 4)

    def test_10_pick_video_type(self):
        self.assertEqual(ss._pick_video_type(0, False), 1)
        # 数字人 + 多图（人物板+场景图，人景同框/多主体）→ type5（seedance 也支持，不强制 kling）
        self.assertEqual(ss._pick_video_type(2, True), 5)
        # 数字人 + 单图（仅一张人物锚定图）→ type4（单张身份锚定，仅 kling）
        self.assertEqual(ss._pick_video_type(1, True), 4)
        self.assertEqual(ss._pick_video_type(3, False), 5)  # 多图无数字人
        self.assertEqual(ss._pick_video_type(1, False), 2)  # 单图
        self.assertEqual(ss._pick_video_type(1, True, has_environment=True), 5)  # 人景/产品环境

    def test_11_shot_duration_seconds(self):
        self.assertEqual(ss._shot_duration_seconds({"duration": 5}, 3), 5)
        self.assertEqual(ss._shot_duration_seconds({"duration": 1}, 3), 3)  # 下限
        # 无 duration 按台词字数估：18字 / 4.5 = 4s
        est = ss._shot_duration_seconds({"dialogue": "一二三四五六七八九十一二三四五六七八"}, 3)
        self.assertEqual(est, 4)

    def test_12_split_full(self):
        plan = {"aspect_ratio": "9:16",
                "asset_refs": {"digital_human_portraits": ["/p/host.png"]},
                "shots": [
                    {"id": "s1", "duration": 4, "dialogue": "大家好",
                     "visual": "中景", "camera": "推近", "characters": ["host"]},
                ]}
        r = ss.split(plan, storyboard_dir=None, fps=30, client="test",
                     allow_unconfirmed=True)
        self.assertEqual(r["ratio"], "9:16")
        seg = r["segments"][0]
        self.assertEqual(seg["video_type"], 4)   # 有数字人
        self.assertEqual(seg["duration"], 4)
        self.assertIn("【台词】大家好", seg["text"])
        self.assertIn("【镜头】推近", seg["text"])
        self.assertTrue(seg["storyboard_ref"])
        self.assertIn("storyboard_plan_fingerprint", seg)

    def test_13_split_missing_images(self):
        tmp = tempfile.mkdtemp()
        try:
            plan = {"shots": [{"id": "s1", "duration": 3, "dialogue": "x"}]}
            r = ss.split(plan, storyboard_dir=tmp, fps=30, client="test",
                         draft_allow_unapproved_storyboard=True)
            self.assertIn("s1", r["missing_images"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_13b_split_records_storyboard_revision(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "storyboard_result.json"), "w", encoding="utf-8") as handle:
                handle.write('{"plan_fingerprint":"abc123"}')
            plan = {"shots": [{"id": "s1", "duration": 3, "dialogue": "x"}]}
            _make_jpg(os.path.join(tmp, "shot_s1.jpg"))
            with self.assertRaisesRegex(ValueError, "CLIENT_MISMATCH|STALE_STORYBOARD"):
                ss.split(plan, storyboard_dir=tmp, fps=30, allow_text2video=True,
                         client="test", draft_allow_unapproved_storyboard=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_14_assemble_single(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "seg1.mp4")
            _make_mp4(src)
            out = os.path.join(tmp, "basecut.mp4")
            spec = {"segments": [{"id": "s1", "out_path": src}]}
            r = ss.assemble(spec, None, out, legacy_unsafe=True)
            self.assertTrue(r["ok"])
            self.assertEqual(r["segment_count"], 1)
            self.assertTrue(os.path.exists(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestVideoReverse(unittest.TestCase):
    def test_15_extract_json_fenced(self):
        text = "时间轴...\n\n```json\n{\"target_model\":\"kling\",\"shots\":[{\"id\":\"s1\"}]}\n```\n完"
        got = vr._extract_json_block(text)
        self.assertIsNotNone(got)
        self.assertEqual(got["shots"][0]["id"], "s1")

    def test_16_extract_json_bare_and_fail(self):
        # 无 fence 的裸对象兜底
        got = vr._extract_json_block('前言 {"fps":30,"shots":[]} 后语')
        self.assertEqual(got["fps"], 30)
        # 完全无 JSON
        self.assertIsNone(vr._extract_json_block("纯文本没有 json"))
        self.assertIsNone(vr._extract_json_block(""))

    def test_17_pick_vl_model_offline_fallback(self):
        # 列表拉取失败（离线）时应回退到 _VL_FALLBACK
        import br_client
        orig = br_client.list_models
        try:
            def _boom(cat=None):
                raise RuntimeError("offline")
            br_client.list_models = _boom
            self.assertEqual(vr._pick_vl_model(), vr._VL_FALLBACK)
        finally:
            br_client.list_models = orig

    def test_18_reverse_prompt_content(self):
        self.assertIn("[目标视频模型]", vr.REVERSE_PROMPT)
        for banned in ["变脸", "服装漂移", "肢体错误", "背景闪烁", "动作断裂", "物理关系失真"]:
            self.assertIn(banned, vr.REVERSE_PROMPT)
        # SCHEME_SPEC 要求 json 输出
        self.assertIn("json", vr.SCHEME_SPEC)

    def test_18c_normalize_remotion_scenes(self):
        # 模型返回 remotion.scenes[] 结构 → 归一成 shots[]
        raw = {"remotion": {"composition": {"fps": 30, "width": 1080, "height": 1920},
               "scenes": [
                   {"name": "TitleCard", "start": 0, "end": 30,
                    "elements": [{"type": "text", "content": "AI 网关\n统一接入"},
                                 {"type": "rectangle"}]},
                   {"name": "CTA", "start": 30, "end": 60,
                    "elements": [{"type": "text", "content": "现在就来试试"}]},
               ]}}
        norm = vr._normalize_scheme(raw, fps=30)
        self.assertEqual(len(norm["shots"]), 2)
        # start/end 是帧号(0,30)，30<=120 视为秒；这里 30>120 才转，故 30 当秒
        self.assertEqual(norm["shots"][0]["start_sec"], 0)
        self.assertIn("AI 网关", norm["shots"][0]["motion_overlay"])
        self.assertEqual(norm["shots"][1]["motion_overlay"], "现在就来试试")
        self.assertEqual(norm["_normalized_from"], "scenes")

    def test_18d_normalize_passthrough_shots(self):
        # 已含 shots 的规范 scheme 原样返回
        good = {"shots": [{"id": "s1", "start_sec": 0, "end_sec": 3}]}
        self.assertIs(vr._normalize_scheme(good, 30), good)

    def test_18e_normalize_sequences_frames(self):
        # kimi-k3 真实结构：顶层 sequences[]，from + durationInFrames（帧制），layers 里带文字
        raw = {
            "composition": {"fps": 30, "width": 1080, "height": 1920},
            "sequences": [
                {"id": "shot01", "from": 0, "durationInFrames": 84,
                 "layers": [{"id": "bar", "type": "rect", "style": {"fill": "#E53935"}},
                            {"id": "t1", "type": "text", "content": "AI 网关"}]},
                {"id": "shot02", "from": 84, "durationInFrames": 36,
                 "layers": [{"id": "t2", "type": "text", "content": "现在就来试试"}]},
            ],
        }
        n = vr._normalize_scheme(raw, 30)
        self.assertEqual(len(n["shots"]), 2)
        # 帧制：from=0 → 0s；durationInFrames=84 → 84/30=2.8s
        self.assertEqual(n["shots"][0]["start_sec"], 0.0)
        self.assertEqual(n["shots"][0]["end_sec"], 2.8)
        # from=84 → 84/30=2.8s（不是 84 秒！这是帧号回归 bug 的守卫）
        self.assertEqual(n["shots"][1]["start_sec"], 2.8)
        self.assertEqual(n["shots"][1]["end_sec"], 4.0)
        # rect 图层被跳过，只收文本
        self.assertEqual(n["shots"][0]["motion_overlay"], "AI 网关")
        self.assertEqual(n["shots"][1]["motion_overlay"], "现在就来试试")

    def test_18b_pick_vl_model_from_live_list(self):
        # mock 出一个含 image 类型的在线模型，_pick_vl_model 应按偏好命中
        import br_client
        fake = [
            {"modelId": "qwen3-vl-plus", "online": True,
             "multimodelTypes": '["image","text"]'},
            {"modelId": "kimi-k3", "online": True,
             "multimodelTypes": '["image","text","video"]'},  # 首选视觉模型
            {"modelId": "minimax-m2.5", "online": True,
             "multimodelTypes": '["text"]'},           # 纯文本，应被过滤
            {"modelId": "old-vl", "online": False,
             "multimodelTypes": '["image"]'},           # 离线，应被过滤
        ]
        orig = br_client.list_models
        try:
            br_client.list_models = lambda cat=None: fake
            # kimi-k3 是偏好序首位，应优先命中
            self.assertEqual(vr._pick_vl_model(), "kimi-k3")
            self.assertEqual(vr._list_vision_models(), {"qwen3-vl-plus", "kimi-k3"})
        finally:
            br_client.list_models = orig


class TestBrClientChatExtract(unittest.TestCase):
    """br_client._extract_chat_text 对 BasicRouter 两种响应形态的归一化。"""
    def test_25_openai_top_level_choices(self):
        import br_client
        resp = {"choices": [{"message": {"role": "assistant", "content": "你好"}}]}
        self.assertEqual(br_client._extract_chat_text(resp), "你好")

    def test_26_doubao_multimodal_envelope(self):
        import br_client
        # {code,data:{message:{content:[{type:output_text,text}]}}}
        resp = {"code": 200, "message": "success", "data": {
            "message": {"role": "assistant",
                        "content": [{"type": "output_text", "text": "时间轴分析结果"}]}}}
        self.assertEqual(br_client._extract_chat_text(resp), "时间轴分析结果")

    def test_27_envelope_str_content(self):
        import br_client
        resp = {"code": 200, "data": {"message": {"content": "纯字符串内容"}}}
        self.assertEqual(br_client._extract_chat_text(resp), "纯字符串内容")

    def test_28_business_error_raises(self):
        import br_client
        with self.assertRaises(br_client.BRError):
            br_client._extract_chat_text({"code": -1, "message": "余额不足", "data": None})

    def test_29_stream_delta_openai(self):
        import br_client
        obj = {"choices": [{"delta": {"content": "片段A"}}]}
        self.assertEqual(br_client._extract_stream_delta(obj), "片段A")

    def test_30_stream_delta_envelope(self):
        import br_client
        obj = {"data": {"message": {"content": [{"type": "output_text", "text": "片段B"}]}}}
        self.assertEqual(br_client._extract_stream_delta(obj), "片段B")

    def test_31_stream_delta_empty(self):
        import br_client
        self.assertEqual(br_client._extract_stream_delta({"foo": "bar"}), "")
        self.assertEqual(br_client._extract_stream_delta("notdict"), "")


class TestFinalEdit(unittest.TestCase):
    def test_19_map_move(self):
        self.assertEqual(fe._map_move("轻推近景"), "push_in")
        self.assertEqual(fe._map_move("向左横摇"), "pan_left")
        self.assertEqual(fe._map_move("pull out slowly"), "pull_out")
        self.assertEqual(fe._map_move("固定机位"), "still")
        self.assertEqual(fe._map_move(""), "still")

    def test_20_map_transition(self):
        self.assertEqual(fe._map_transition("淡入淡出"), "fade")
        self.assertEqual(fe._map_transition("硬切"), "cut")
        self.assertEqual(fe._map_transition(None), "cut")

    def test_21_overlay_to_content(self):
        title, bullets = fe._overlay_to_content("AI 网关：统一接入；降本增效")
        self.assertTrue(title)
        self.assertIn("降本增效", bullets)
        t2, b2 = fe._overlay_to_content("")
        self.assertIsNone(t2)
        self.assertEqual(b2, [])

    def test_22_compile_shotlist(self):
        tmp = tempfile.mkdtemp()
        try:
            bc = os.path.join(tmp, "basecut.mp4")
            _make_mp4(bc)
            scheme = {"fps": 30, "width": 1080, "height": 1920, "shots": [
                {"id": "s1", "start_sec": 0, "end_sec": 2.5,
                 "camera_move": "推近", "motion_overlay": "标题：要点一",
                 "transition_to_next": "淡入"},
                {"id": "s2", "start_sec": 2.5, "end_sec": 5,
                 "camera_move": "固定", "transition_to_next": "硬切"},
            ]}
            sl = fe.compile_shotlist(scheme, bc)
            self.assertEqual(len(sl["shots"]), 2)
            self.assertEqual(sl["shots"][0]["move"], "push_in")
            self.assertEqual(sl["shots"][0]["durationInFrames"], 75)  # 2.5*30
            self.assertEqual(sl["shots"][0]["sourceStartFrame"], 0)
            self.assertEqual(sl["shots"][1]["sourceStartFrame"], 75)
            self.assertEqual(sl["shots"][0]["video"], os.path.abspath(bc))
            # 第二镜入场转场 = 第一镜 transition_to_next(淡入→fade)
            self.assertEqual(sl["shots"][1]["transition"], "fade")
            self.assertEqual(sl["shots"][1]["move"], "still")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_23_compile_missing_basecut(self):
        scheme = {"shots": [{"id": "s1", "start_sec": 0, "end_sec": 3}]}
        with self.assertRaises(FileNotFoundError):
            fe.compile_shotlist(scheme, "/nonexistent/basecut.mp4")

    def test_23b_compile_rejects_invalid_timeline(self):
        tmp = tempfile.mkdtemp()
        try:
            bc = os.path.join(tmp, "basecut.mp4")
            _make_mp4(bc)
            for start, end in [(-1, 2), (2, 2), (3, 2)]:
                with self.assertRaisesRegex(ValueError, "INVALID_SHOT_RANGE"):
                    fe.compile_shotlist(
                        {"shots": [{"start_sec": start, "end_sec": end}]}, bc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "ffmpeg/ffprobe required")
    def test_23c_compile_probes_real_basecut_duration(self):
        tmp = tempfile.mkdtemp()
        try:
            bc = os.path.join(tmp, "basecut.mp4")
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90:r=30:d=2", "-f", "lavfi", "-i",
                "anullsrc=r=48000:cl=stereo", "-shortest", "-t", "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", bc,
            ], check=True, capture_output=True)
            sl = fe.compile_shotlist(
                {"fps": 30, "shots": [{"start_sec": 0.5, "end_sec": 1.5}]},
                bc, require_basecut_duration=True)
            self.assertEqual(sl["shots"][0]["sourceStartFrame"], 15)
            self.assertEqual(sl["shots"][0]["durationInFrames"], 30)
            with self.assertRaisesRegex(ValueError, "SHOT_EXCEEDS_BASECUT"):
                fe.compile_shotlist(
                    {"shots": [{"start_sec": 0, "end_sec": 2.5}]}, bc,
                    require_basecut_duration=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_23d_final_normalization_has_delivery_codec_flags(self):
        tmp = tempfile.mkdtemp()
        try:
            source = os.path.join(tmp, "raw.mp4")
            output = os.path.join(tmp, "final.mp4")
            _make_mp4(source)
            completed = type("Completed", (), {"returncode": 0})()
            def fake_run(cmd, *args, **kwargs):
                _make_mp4(cmd[-1])
                self.assertIn("libx264", cmd)
                self.assertIn("yuv420p", cmd)
                self.assertIn("48000", cmd)
                self.assertIn("+faststart", cmd)
                self.assertEqual(cmd[cmd.index("-r") + 1], "30")
                return completed
            with patch.object(fe.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 patch.object(fe.subprocess, "run", side_effect=fake_run), \
                 patch("remotion_engine.ensure_ffmpeg_on_path", return_value=(True, "mock")), \
                 patch("remotion_engine._media_output_ok", return_value=True):
                fe._normalize_final_video(source, output)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_24_stage_media_to_public(self):
        import remotion_engine
        tmp = tempfile.mkdtemp()
        public_dir = os.path.join(remotion_engine.ENGINE, "public")
        staged_name = None
        try:
            src = os.path.join(tmp, "unique_basecut_v14test.mp4")
            _make_mp4(src)
            shotlist = {"shots": [{"video": src, "durationInFrames": 30}]}
            fe._stage_media_to_public(shotlist)
            staged = shotlist["shots"][0]["video"]
            staged_name = staged
            # 应改为相对文件名（非绝对路径、非 file://）
            self.assertFalse(staged.startswith("/"))
            self.assertFalse(staged.startswith("file://"))
            self.assertTrue(os.path.exists(os.path.join(public_dir, staged)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            # 清理拷进 public 的测试文件
            if staged_name:
                p = os.path.join(public_dir, staged_name)
                if os.path.exists(p):
                    os.remove(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
