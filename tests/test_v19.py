"""
v19 零到一测试套件 — 纯本地、无网络、无 API Key
本轮修复的四个真实缺陷验证：

  ① 字幕透明合成 bug（用户实测反馈：成片只剩字幕+声音，画面消失）——
     根因：hf_engine.render() 渲染透明字幕层时只按输出文件扩展名/--format 推断
     格式，没有强制校验 spec.background.type=='transparent' 时必须用 mov/webm
     （alpha 格式），也没有校验渲染产物真的带 alpha 通道。一旦调用链上误用了
     mp4 输出（或漏传 --format mov），字幕层会渲成不透明黑底视频，
     subtitle_overlay.compose() 的 overlay 会把这块黑色矩形整块盖住底片画面
     （真实复现：mp4 输出 codec=h264/pix_fmt=yuv420p 无 alpha；mov 正确输出
     codec=prores/pix_fmt=yuva444p12le 带 alpha）。
     本轮加两道防线：
       - hf_engine.render()：spec 要求 transparent 但格式不支持 alpha → 直接拒绝；
         渲染完成后额外 ffprobe 校验产物确有 alpha 通道，没有则拒绝。
       - subtitle_overlay.compose()：合成前 ffprobe 校验 alpha_path 确有 alpha
         通道（require_alpha=True 默认），没有则直接拒绝合成，不产出"看似成功
         实则遮盖画面"的成片。

     T01  hf_engine.render: transparent + mp4(推断/显式) → ALPHA_FORMAT_MISMATCH
     T02  hf_engine.render: transparent + mov 但产物实测无 alpha → ALPHA_VERIFY_FAILED
     T03  hf_engine.render: transparent + mov 且产物确有 alpha → 正常返回
     T04  hf_engine.render: 非 transparent(color) 背景不受 alpha 校验影响
     T05  subtitle_overlay.compose: alpha_path 无 alpha 通道 → NO_ALPHA_CHANNEL 拒绝
     T06  subtitle_overlay.compose: alpha_path 确有 alpha 通道 → 正常合成
     T07  subtitle_overlay.compose: require_alpha=False 时跳过校验（向后兼容旧调用/测试）

  ② --chain + --locked-refs 交互 bug —— _apply_locked_refs 给每段无差别注入
     urls，render_chained 用"urls 是否非空"作为跳过尾帧串联的唯一判据，导致
     locked_refs 存在时尾帧串联被完全静默禁用。本轮给 _apply_locked_refs 注入
     的段打 _locked_urls=True 标记，render_chained 分三种情况：段自己显式 urls
     (非 locked 注入) → 尊重不串联；locked_refs 注入段 → 尾帧 + 锁定图合并；
     都没有 → 纯尾帧串联。

     T08  _apply_locked_refs 给每段打 _locked_urls=True 标记
     T09  render_chained: locked_refs 注入段 + 有上段尾帧 → 合并尾帧与锁定图(不再被短路)
     T10  render_chained: 段自己显式 urls(非 locked 注入) → 尊重段设置，不串尾帧

  ③ digital_human.py 空 persona 静默生成通用形象 —— compose_portrait_prompt
     返回 (prompt, is_generic)；create_actor 在 is_generic 且非 allow_generic 时拒绝。

     T11  compose_portrait_prompt: gender/style/persona 全空 → is_generic=True
     T12  compose_portrait_prompt: 任一非空 → is_generic=False
     T13  create_actor: 空 persona 且非 allow_generic → EMPTY_PERSONA 拒绝
     T14  create_actor: 空 persona 但 allow_generic=True → 正常生成

  ④ asset_prep 确认/待确认状态机跨模块执行 —— asset_prep.is_confirmed() +
     script_splitter.split(client=..., allow_unconfirmed=...) 校验锚定素材。

     T15  is_confirmed: pending 素材 → False
     T16  is_confirmed: confirmed 素材 → True
     T17  is_confirmed: 无 status 字段 → False（unknown 默认拒绝）
     T18  is_confirmed: brief 未追踪的路径/远程 URL → True(无法判断不拦截)
     T19  split: client 传入 + 命中 pending 素材 → 默认拒绝 UNCONFIRMED_ASSET
     T20  split: allow_unconfirmed=True → 放行并记入 unconfirmed_refs
     T21  split: client 未传 → 跳过确认检查（向后兼容）
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

import hf_engine as hf
import subtitle_overlay as so
import video_engine as ve
import digital_human as dh
import asset_prep as ap
import script_splitter as ss
import matte


def _wf(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class TestHfEngineAlphaGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19hf_")
        self.spec_transparent = {
            "resolution": [1080, 1920], "fps": 30, "duration": 2,
            "background": {"type": "transparent"},
            "scenes": [{"text": "x", "start": 0, "end": 2, "size": 40}],
        }
        self.spec_color = {
            "resolution": [1080, 1920], "fps": 30, "duration": 2,
            "background": {"type": "color", "color": "#000"},
            "scenes": [{"text": "x", "start": 0, "end": 2, "size": 40}],
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_transparent_mp4_rejected(self):
        out = os.path.join(self.tmp, "sub.mp4")
        with patch.object(hf, "_has_node", return_value=True), \
             patch.object(hf, "ensure_ffmpeg_on_path", return_value=(True, "ok")), \
             patch.object(hf, "build_project", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                hf.render(self.spec_transparent, out, work_dir=self.tmp, verbose=False)
        self.assertIn("ALPHA_FORMAT_MISMATCH", str(ctx.exception))

    def test_02_transparent_mov_no_real_alpha_rejected(self):
        out = os.path.join(self.tmp, "sub.mov")

        def fake_run_hf(args, work_dir, verbose=True):
            if args and args[0] == "render":
                _wf(args[args.index("--output") + 1], b"fake video bytes, not real prores alpha")
            return 0, ""

        with patch.object(hf, "_has_node", return_value=True), \
             patch.object(hf, "ensure_ffmpeg_on_path", return_value=(True, "ok")), \
             patch.object(hf, "build_project", return_value=None), \
             patch.object(hf, "_run_hf", side_effect=fake_run_hf), \
             patch.object(hf, "_probe_pix_fmt", return_value=("h264", "yuv420p")):
            with self.assertRaises(SystemExit) as ctx:
                hf.render(self.spec_transparent, out, work_dir=self.tmp, verbose=False)
        self.assertIn("ALPHA_VERIFY_FAILED", str(ctx.exception))

    def test_03_transparent_mov_real_alpha_passes(self):
        out = os.path.join(self.tmp, "sub.mov")

        def fake_run_hf(args, work_dir, verbose=True):
            if args and args[0] == "render":
                _wf(args[args.index("--output") + 1], b"fake prores alpha bytes")
            return 0, ""

        with patch.object(hf, "_has_node", return_value=True), \
             patch.object(hf, "ensure_ffmpeg_on_path", return_value=(True, "ok")), \
             patch.object(hf, "build_project", return_value=None), \
             patch.object(hf, "_run_hf", side_effect=fake_run_hf), \
             patch.object(hf, "_probe_pix_fmt", return_value=("prores", "yuva444p12le")):
            result = hf.render(self.spec_transparent, out, work_dir=self.tmp, verbose=False)
        self.assertEqual(result, out)

    def test_04_color_background_skips_alpha_check(self):
        out = os.path.join(self.tmp, "bg.mp4")

        def fake_run_hf(args, work_dir, verbose=True):
            if args and args[0] == "render":
                _wf(args[args.index("--output") + 1], b"opaque video, fine for color bg")
            return 0, ""

        with patch.object(hf, "_has_node", return_value=True), \
             patch.object(hf, "ensure_ffmpeg_on_path", return_value=(True, "ok")), \
             patch.object(hf, "build_project", return_value=None), \
             patch.object(hf, "_run_hf", side_effect=fake_run_hf), \
              patch.object(hf, "_probe_pix_fmt", return_value=("h264", "yuv420p")) as mock_probe:
            result = hf.render(self.spec_color, out, work_dir=self.tmp, verbose=False)
        self.assertEqual(result, out)
        mock_probe.assert_called_once()  # opaque output still must be decodable


class TestSubtitleOverlayAlphaGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19so_")
        self.video = os.path.join(self.tmp, "in.mp4"); _wf(self.video, b"v")
        self.alpha = os.path.join(self.tmp, "sub.mov"); _wf(self.alpha, b"a")
        self.out = os.path.join(self.tmp, "out.mp4")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_05_no_alpha_channel_rejected(self):
        with patch("ocr_check._ffmpeg_bins", return_value=("/ff", "/fp")), \
             patch.object(so, "_probe_stream", return_value=("h264", "yuv420p")):
            r = so.compose(self.video, self.alpha, self.out, verbose=False)
        self.assertFalse(r["ok"])
        self.assertIn("NO_ALPHA_CHANNEL", r["error"])
        self.assertFalse(os.path.exists(self.out))  # 没往下走 ffmpeg 合成

    def test_06_has_alpha_channel_proceeds(self):
        def fake_run(args, **kw):
            _wf(args[-1], b"o" * 1000)
            m = MagicMock(); m.returncode = 0; m.stdout = b""
            return m

        with patch("ocr_check._ffmpeg_bins", return_value=("/ff", "/fp")), \
              patch.object(so, "_probe_stream", return_value=("prores", "yuva444p12le")), \
              patch.object(so, "_probe_duration", return_value=2.0), \
              patch("subprocess.run", side_effect=fake_run), \
             patch.object(so, "_ffprobe_frame_bytes", return_value=1600 * 1024):
            r = so.compose(self.video, self.alpha, self.out, verify_min_kb=200, verbose=False)
        self.assertTrue(r["ok"])

    def test_07_require_alpha_false_skips_check(self):
        def fake_run(args, **kw):
            _wf(args[-1], b"o" * 1000)
            m = MagicMock(); m.returncode = 0; m.stdout = b""
            return m

        with patch("ocr_check._ffmpeg_bins", return_value=("/ff", "/fp")), \
              patch.object(so, "_probe_stream") as mock_probe, \
              patch.object(so, "_probe_duration", return_value=2.0), \
              patch("subprocess.run", side_effect=fake_run), \
             patch.object(so, "_ffprobe_frame_bytes", return_value=1600 * 1024):
            r = so.compose(self.video, self.alpha, self.out, verify_min_kb=200,
                          verbose=False, require_alpha=False)
        self.assertTrue(r["ok"])
        mock_probe.assert_not_called()


class TestChainLockedRefsInteraction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19chain_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_08_apply_locked_refs_marks_segments(self):
        segs = [{"id": "s1", "text": "a"}, {"id": "s2", "text": "b", "urls": ["own.png"]}]
        out = ve._apply_locked_refs(segs, ["cast.png", "hero.png"])
        for seg in out:
            self.assertTrue(seg["_locked_urls"])
            self.assertIn("cast.png", seg["urls"])

    def test_09_render_chained_merges_tail_with_locked_refs(self):
        # 2 段：第1段独立生成；第2段用 locked_refs 注入 urls（模拟 CLI 里
        # _apply_locked_refs 在 render_chained 之前跑）。旧 bug：第2段因为
        # seg.get("urls") 非空就完全跳过尾帧串联；新逻辑应把尾帧 + 锁定图合并。
        segs = ve._apply_locked_refs(
            [{"id": "s1", "text": "first", "video_type": 1, "duration": 3},
             {"id": "s2", "text": "second", "duration": 3}],
            locked_refs=["cast.png"])

        created_calls = []

        def fake_create_video(api_key, text, **kw):
            created_calls.append(kw)
            return "task-%d" % len(created_calls)

        with patch.object(ve.key_setup, "load_key", return_value="k"), \
             patch.object(ve.br_client, "create_video", side_effect=fake_create_video), \
             patch.object(ve.br_client, "wait_video", return_value="http://x/seg.mp4"), \
             patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve.br_client, "list_models", return_value=[]), \
             patch.object(ve, "_available_models_set", return_value=set()), \
             patch.object(ve.br_client, "to_image_ref", side_effect=lambda u, **kw: u), \
             patch.object(ve.br_client, "download", return_value=None), \
             patch.object(ve, "_extract_last_frame", return_value="/tmp/tail1.png"):
            results = ve.render_chained(segs, verbose=False, draft=True)

        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        # 第2段应该同时拿到锁定参考图(cast.png)和上段尾帧(tail1.png)，而不是
        # locked_refs 的存在导致尾帧串联被完全跳过（旧 bug 会让 urls=["cast.png"]
        # 走 "own explicit urls" 分支，tail1.png 完全进不去 urls）
        seg2_urls = created_calls[1]["urls"]
        self.assertIn("cast.png", seg2_urls)
        self.assertIn("/tmp/tail1.png", seg2_urls)

    def test_10_render_chained_respects_own_explicit_urls(self):
        # 段自己显式给 urls（未经过 _apply_locked_refs 标记）→ 不应该被尾帧串联覆盖
        segs = [{"id": "s1", "text": "first", "video_type": 1, "duration": 3},
                {"id": "s2", "text": "second", "urls": ["own_explicit.png"],
                 "video_type": 4, "duration": 3}]

        created_calls = []

        def fake_create_video(api_key, text, **kw):
            created_calls.append(kw)
            return "task-%d" % len(created_calls)

        with patch.object(ve.key_setup, "load_key", return_value="k"), \
             patch.object(ve.br_client, "create_video", side_effect=fake_create_video), \
             patch.object(ve.br_client, "wait_video", return_value="http://x/seg.mp4"), \
             patch.object(ve, "_model_catalog", return_value={"records": {}, "aliases": {}}), \
             patch.object(ve.br_client, "list_models", return_value=[]), \
             patch.object(ve, "_available_models_set", return_value=set()), \
             patch.object(ve.br_client, "to_image_ref", side_effect=lambda u, **kw: u), \
             patch.object(ve.br_client, "download", return_value=None), \
             patch.object(ve, "_extract_last_frame", return_value="/tmp/tail1.png"):
            results = ve.render_chained(segs, verbose=False, draft=True)

        seg2_urls = created_calls[1]["urls"]
        self.assertEqual(seg2_urls, ["own_explicit.png"])  # 未混入尾帧
        self.assertEqual(created_calls[1]["video_type"], 4)  # 段自身设置被尊重


class TestDigitalHumanEmptyPersonaGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19dh_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_11_all_empty_is_generic(self):
        prompt, is_generic = dh.compose_portrait_prompt()
        self.assertTrue(is_generic)
        self.assertEqual(prompt, "专业商业人像，正面半身，纯色背景，柔和布光，高清写实")

    def test_12_any_nonempty_not_generic(self):
        prompt, is_generic = dh.compose_portrait_prompt(persona={"profession": "医生"})
        self.assertFalse(is_generic)
        self.assertIn("医生", prompt)

        prompt2, is_generic2 = dh.compose_portrait_prompt(gender="female")
        self.assertFalse(is_generic2)

        prompt3, is_generic3 = dh.compose_portrait_prompt(style="韩系")
        self.assertFalse(is_generic3)

    def test_13_create_actor_blocks_empty_persona(self):
        with patch.object(dh, "_actor_dir",
                          return_value=os.path.join(self.tmp, "client1", "actor1")):
            with self.assertRaises(SystemExit) as ctx:
                dh.create_actor("client1", "actor1")
        self.assertIn("EMPTY_PERSONA", str(ctx.exception))

    def test_14_create_actor_allow_generic_bypasses(self):
        adir = os.path.join(self.tmp, "client1", "actor2")
        def fake_download(_url, path, **_kwargs):
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")
        with patch.object(dh, "_actor_dir", return_value=adir), \
             patch.object(dh.key_setup, "load_key", return_value="k"), \
             patch.object(dh.br_client, "create_image",
                          side_effect=AssertionError("legacy sync path used")), \
             patch.object(dh.br_client, "create_image_generation",
                          return_value="img_actor"), \
             patch.object(dh.br_client, "wait_image_generation",
                          return_value=["https://x/p.png"]), \
             patch.object(dh.br_client, "download", side_effect=fake_download):
            res = dh.create_actor("client1", "actor2", allow_generic=True)
        self.assertIn("portrait", res)


class TestAssetPrepConfirmedGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19ap_")
        self.client = "testclient19"
        self._orig_assets = ap.ASSETS
        ap.ASSETS = self.tmp

    def tearDown(self):
        ap.ASSETS = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_brief(self, images):
        d = ap._client_dir(self.client)
        normalized = []
        for item in images:
            item = dict(item)
            path = os.path.join(d, "images", os.path.basename(item["path"]))
            with open(path, "wb") as handle:
                handle.write(bytes.fromhex("89504e470d0a1a0a"))
            item["path"] = path
            normalized.append(item)
        brief = {"images": normalized, "specs": {}, "product_type": None,
                 "render_profile": None, "render_plan": None, "ppt_files": []}
        ap._save_brief(self.client, brief)

    def test_15_pending_is_not_confirmed(self):
        self._seed_brief([{"path": "assets/%s/images/a.png" % self.client,
                          "tag": "hero", "status": "pending"}])
        self.assertFalse(ap.is_confirmed(self.client,
                         os.path.join(ap.ROOT, "assets", self.client, "images", "a.png")))

    def test_16_confirmed_is_confirmed(self):
        self._seed_brief([{"path": "assets/%s/images/b.png" % self.client,
                          "tag": "hero", "status": "confirmed"}])
        self.assertTrue(ap.is_confirmed(self.client,
                         os.path.join(ap._client_dir(self.client), "images", "b.png")))

    def test_17_no_status_field_is_unknown_and_rejected(self):
        self._seed_brief([{"path": "assets/%s/images/c.png" % self.client, "tag": "hero"}])
        self.assertFalse(ap.is_confirmed(self.client,
                          os.path.join(ap.ROOT, "assets", self.client, "images", "c.png")))

    def test_18_untracked_or_remote_require_explicit_allow(self):
        self._seed_brief([])
        self.assertFalse(ap.is_confirmed(self.client, "https://cdn.example.com/x.png"))
        self.assertFalse(ap.is_confirmed(self.client, "/some/untracked/path.png"))
        self.assertTrue(ap.is_confirmed(
            self.client, "https://cdn.example.com/x.png", allow_remote=True))
        self.assertTrue(ap.is_confirmed(
            self.client, "/some/untracked/path.png", allow_untracked=True))


class TestSplitUnconfirmedGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19split_")
        self.client = "testclient19b"
        self._orig_assets = ap.ASSETS
        ap.ASSETS = self.tmp

    def tearDown(self):
        ap.ASSETS = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, ref_path):
        return {
            "aspect_ratio": "9:16", "color_mode": "bw",
            "asset_refs": {"product_images": [ref_path]},
            "shots": [{"id": "s1", "duration": 3, "dialogue": "hi"}],
        }

    def test_19_split_blocks_pending_ref(self):
        rel = os.path.join(self.tmp, "p.png")
        with open(rel, "wb") as handle:
            handle.write(b"pending")
        ap._save_brief(self.client, {"images": [{"path": rel, "tag": "hero",
                       "status": "pending"}], "specs": {}, "product_type": None,
                       "render_profile": None, "render_plan": None, "ppt_files": []})
        abspath = rel
        plan = self._plan(abspath)
        with self.assertRaises(ValueError) as ctx:
            ss.split(plan, client=self.client)
        self.assertIn("UNCONFIRMED_ASSET", str(ctx.exception))

    def test_20_split_allow_unconfirmed_bypasses(self):
        rel = os.path.join(self.tmp, "p2.png")
        with open(rel, "wb") as handle:
            handle.write(b"pending")
        ap._save_brief(self.client, {"images": [{"path": rel, "tag": "hero",
                       "status": "pending"}], "specs": {}, "product_type": None,
                       "render_profile": None, "render_plan": None, "ppt_files": []})
        abspath = rel
        plan = self._plan(abspath)
        result = ss.split(plan, client=self.client, allow_unconfirmed=True)
        self.assertEqual(len(result["segments"]), 1)
        self.assertTrue(result["unconfirmed_refs"])

    def test_21_split_without_client_is_rejected(self):
        plan = self._plan("/some/local/pending_but_unchecked.png")
        with self.assertRaisesRegex(ValueError, "CLIENT_REQUIRED"):
            ss.split(plan, client=None)


class TestMatteConfirmedGate(unittest.TestCase):
    """matte.py compose_scene 此前完全没检查 asset_prep 的 confirmed/pending 状态机——
    本轮补上，与 script_splitter.split(client=...) 同一套 is_confirmed() 校验对齐。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v19matte_")
        self.client = "testclient19c"
        self._orig_assets = ap.ASSETS
        ap.ASSETS = self.tmp

    def tearDown(self):
        ap.ASSETS = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_pending(self, rel):
        ap._save_brief(self.client, {"images": [{"path": rel, "tag": "human",
                       "status": "pending"}], "specs": {}, "product_type": None,
                       "render_profile": None, "render_plan": None, "ppt_files": []})

    def test_22_compose_scene_blocks_pending_human(self):
        rel = "assets/%s/images/human.png" % self.client
        self._seed_pending(rel)
        human_abs = os.path.join(ap.ROOT, rel)
        with patch.object(matte.key_setup, "load_key", return_value="k"):
            with self.assertRaises(SystemExit) as ctx:
                matte.compose_scene(human_abs, "scene.jpg", "fuse prompt",
                                    client=self.client)
        self.assertIn("UNCONFIRMED_ASSET", str(ctx.exception))

    def test_23_compose_scene_allow_unconfirmed_bypasses(self):
        rel = "assets/%s/images/human2.png" % self.client
        self._seed_pending(rel)
        human_abs = os.path.join(ap.ROOT, rel)
        os.makedirs(os.path.dirname(human_abs), exist_ok=True)
        with open(human_abs, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"portrait" * 8)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(matte.key_setup, "load_key", return_value="k"), \
             patch.object(matte.br_client, "create_image",
                          side_effect=AssertionError("legacy sync path used")), \
             patch.object(matte.br_client, "create_image_generation",
                          return_value="img_fused"), \
             patch.object(matte.br_client, "wait_image_generation",
                          return_value=["https://x/fused.png"]), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            res = matte.compose_scene(human_abs, "https://cdn.example.com/scene.jpg",
                                      "fuse prompt", client=self.client,
                                      allow_unconfirmed=True, verbose=False)
        self.assertTrue(res["ok"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_24_compose_scene_without_client_skips_check(self):
        # 未传 client：向后兼容，不检查（走到 human 文件不存在的正常报错，
        # 而不是 UNCONFIRMED_ASSET，证明确认闸门被跳过了）
        with patch.object(matte.key_setup, "load_key", return_value="k"):
            with self.assertRaises(matte.br_client.BRError) as ctx:
                matte.compose_scene("/no/such/human.png", "https://cdn.example.com/scene.jpg",
                                    "fuse prompt", client=None)
        self.assertNotIn("UNCONFIRMED_ASSET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
