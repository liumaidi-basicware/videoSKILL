"""
v13 零到一测试套件 — 纯本地、无网络、无 API Key
覆盖范围:
  T01  product_library: create_product
  T02  product_library: add_view
  T03  product_library: _compose_view_prompt
  T04  product_library: resolve — 无方位图 fallback hero
  T05  product_library: resolve — 有方位图 video_type=5
  T06  product_library: resolve — views 顺序参数
  T07  product_library: gen_all_views — 跳过已存在方位
  T08  product_library: confirm_view — v2 晋升 + 候选清理
  T09  product_library: CLI list / create / resolve
  T10  storyboard: _collect_image_urls — 本地路径转 base64
  T11  storyboard: render_storyboard — product_sku 展开逻辑（mock API）
  T12  storyboard: cast_prompt — digital_human_portraits 注入块
  T13  storyboard: shot_prompt — product_images 注入块
  T14  video_engine: render_batch — product_sku 展开（mock br_client）
  T15  video_engine: render_batch — client 参数透传
  T16  fuse: overlay 签名 mix_audio=True 默认值
  T17  fuse: _slot_geometry 各 slot
  T18  br_client: host_image 进程级缓存
  T19  guide_scaffold: compile_shots — has_digital_human=False 无 humanSlot
  T20  guide_scaffold: compile_shots — width/height 输出格式
"""
import os, sys, json, shutil, tempfile, importlib, unittest
import io
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import product_library as pl
import fuse
import guide_scaffold


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _make_png(path, size=8):
    """Write a tiny valid 8x8 white PNG without Pillow."""
    import struct, zlib
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"\x00" + b"\xFF\xFF\xFF" * size  # filter byte + RGB pixels
    compressed = zlib.compress(raw * size)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", compressed)
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)


# ─────────────────────────────────────────────────────────────
# T01-T09  product_library
# ─────────────────────────────────────────────────────────────
class TestProductLibrary(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pl_test_")
        # 重定向 ASSETS_DIR 到临时目录
        self._orig_assets = pl.ASSETS_DIR
        pl.ASSETS_DIR = self.tmp

    def tearDown(self):
        pl.ASSETS_DIR = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T01 create_product — 无 from_file
    def test_01_create_empty(self):
        r = pl.create_product("acme", "chair")
        self.assertIsNone(r["hero"])
        self.assertEqual(r["meta"]["sku"], "chair")
        self.assertEqual(r["meta"]["client"], "acme")

    # T02 create_product + add_view
    def test_02_add_view(self):
        src = os.path.join(self.tmp, "src.png")
        _make_png(src)
        pl.create_product("acme", "chair")
        r = pl.add_view("acme", "chair", "front", src)
        self.assertTrue(os.path.isfile(r["path"]))
        # hero 应自动被设为 front（首张 view）
        hero = os.path.join(pl._sku_dir("acme", "chair"), "hero.png")
        self.assertTrue(os.path.isfile(hero))

    # T03 _compose_view_prompt
    def test_03_compose_prompt(self):
        p = pl._compose_view_prompt("x5", "人体工学椅", "科技感", "side")
        self.assertIn("人体工学椅", p)
        self.assertIn("侧面", p)
        self.assertIn("科技感", p)
        # extra_prompt
        p2 = pl._compose_view_prompt("x5", "", "", "detail", "微距细节")
        self.assertIn("微距细节", p2)

    # T04 resolve — 无方位图，fallback hero.png，video_type=2
    def test_04_resolve_no_views(self):
        src = os.path.join(self.tmp, "h.png")
        _make_png(src)
        pl.create_product("acme", "chair", from_file=src)
        r = pl.resolve("acme", "chair")
        self.assertIsNotNone(r["hero"])
        self.assertEqual(r["refs"], [])
        self.assertEqual(r["video_type"], 2)

    # T05 resolve — 有多方位图，video_type=5
    def test_05_resolve_with_views(self):
        pl.create_product("acme", "chair")
        for v in ["front", "side", "detail"]:
            p = pl._view_path("acme", "chair", v)
            _make_png(p)
        r = pl.resolve("acme", "chair")
        self.assertEqual(r["hero"], pl._view_path("acme", "chair", "front"))
        self.assertIn(pl._view_path("acme", "chair", "side"), r["refs"])
        self.assertIn(pl._view_path("acme", "chair", "detail"), r["refs"])
        self.assertEqual(r["video_type"], 5)

    # T06 resolve — views 顺序参数
    def test_06_resolve_view_order(self):
        pl.create_product("acme", "chair")
        for v in ["front", "detail", "scene"]:
            _make_png(pl._view_path("acme", "chair", v))
        # 传 detail 优先
        r = pl.resolve("acme", "chair", views=["detail", "scene", "front"])
        self.assertEqual(r["hero"], pl._view_path("acme", "chair", "detail"))

    # T07 gen_all_views — 跳过已存在方位
    def test_07_gen_all_views_skip_existing(self):
        pl.create_product("acme", "chair")
        # 预先放一张正式 front
        fp = pl._view_path("acme", "chair", "front")
        _make_png(fp)
        # mock gen_view，不真的调 API
        called = []
        def fake_gen(client, sku, view, **kw):
            called.append(view)
            return {"view": view, "pass1": "/fake/v1.png", "pass2": None,
                    "needs_confirmation": True}
        with patch.object(pl, "gen_view", side_effect=fake_gen):
            results = pl.gen_all_views("acme", "chair", views=["front", "side"])
        self.assertIn("front", results)
        self.assertTrue(results["front"].get("skipped"))
        self.assertNotIn("front", called)
        self.assertIn("side", called)

    def test_07b_gen_view_uses_async_image_generation(self):
        pl.create_product("acme", "chair")

        def fake_download(_url, dest, **_kwargs):
            _make_png(dest)

        with patch.object(pl.key_setup, "load_key", return_value="sk-test"), \
             patch.object(pl.br_client, "create_image",
                          side_effect=AssertionError("legacy sync path used")), \
             patch.object(pl.br_client, "create_image_generation",
                          side_effect=["img_v1", "img_v2"]) as create, \
             patch.object(pl.br_client, "wait_image_generation",
                          return_value=["https://x/view.png"]), \
             patch.object(pl.br_client, "download", side_effect=fake_download):
            result = pl.gen_view("acme", "chair", "side", refine=True)

        self.assertTrue(os.path.isfile(result["pass1"]))
        self.assertTrue(os.path.isfile(result["pass2"]))
        self.assertEqual(create.call_count, 2)

    # T08 confirm_view — v2 晋升，v1 清除
    def test_08_confirm_view(self):
        pl.create_product("acme", "chair")
        vdir = os.path.join(pl._sku_dir("acme", "chair"), "views")
        os.makedirs(vdir, exist_ok=True)
        v1 = os.path.join(vdir, "side_v1.png")
        v2 = os.path.join(vdir, "side_v2.png")
        _make_png(v1); _make_png(v2)
        r = pl.confirm_view("acme", "chair", "side", use_v2=True)
        final = pl._view_path("acme", "chair", "side")
        self.assertTrue(os.path.isfile(final))
        self.assertFalse(os.path.isfile(v1))   # v1 被清理
        self.assertFalse(os.path.isfile(v2))   # v2 已移走

    # T09 CLI: list / create / resolve
    def test_09_cli(self):
        # create
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            out = pl.main(["create", "--client", "acme", "--sku", "sofa",
                           "--product-type", "布艺沙发"])
        self.assertEqual(out, 0)
        self.assertEqual(json.loads(stdout.getvalue())["meta"]["sku"], "sofa")
        self.assertEqual(stderr.getvalue(), "")
        # list
        lst = pl.list_products("acme")
        skus = [p["sku"] for p in lst]
        self.assertIn("sofa", skus)
        # resolve
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            out2 = pl.main(["resolve", "--client", "acme", "--sku", "sofa"])
        self.assertEqual(out2, 0)
        self.assertEqual(json.loads(stdout.getvalue())["sku"], "sofa")
        self.assertEqual(stderr.getvalue(), "")


# ─────────────────────────────────────────────────────────────
# T10-T13  storyboard
# ─────────────────────────────────────────────────────────────
import storyboard as sb

class TestStoryboard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb_test_")
        self._orig_assets = pl.ASSETS_DIR
        pl.ASSETS_DIR = self.tmp

    def tearDown(self):
        pl.ASSETS_DIR = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    # T10 _collect_image_urls — 本地 PNG → base64 data URL
    def test_10_collect_image_urls(self):
        png = os.path.join(self.tmp, "ref.png")
        _make_png(png)
        with patch("br_client.to_image_ref", return_value="data:image/png;base64,ABC"):
            urls = sb._collect_image_urls([png], api_key="sk-test")
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], "data:image/png;base64,ABC")

    # T10b _collect_image_urls — 转换失败时跳过不崩溃
    def test_10b_collect_skip_on_error(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("br_client.to_image_ref", side_effect=Exception("fail")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            urls = sb._collect_image_urls(["/nonexistent.png"], api_key="sk-test")
        self.assertEqual(urls, [])
        self.assertIn("[asset_ref] 跳过参考图 /nonexistent.png：fail", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_10c_collect_fails_closed_for_generation(self):
        with patch("br_client.to_image_ref", side_effect=Exception("invalid bytes")):
            with self.assertRaises(sb.br_client.BRError) as ctx:
                sb._collect_image_urls(
                    ["/broken/wearing.png"], api_key="sk-test",
                    fail_on_invalid=True, label="人物/佩戴参考图")
        self.assertIn("纯文字生成", str(ctx.exception))

    # T11 render_storyboard product_sku 展开逻辑
    def test_11_product_sku_expand(self):
        # 建立多方位图
        for v in ["front", "side"]:
            _make_png(pl._view_path("acme", "chair", v))
        pl.create_product("acme", "chair")

        plan = {
            "client": "acme",
            "title": "椅子广告",
            "aspect_ratio": "9:16",
            "asset_refs": {"product_sku": "chair"},
            "shots": []
        }
        plan_path = os.path.join(self.tmp, "plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f)

        collected = []
        def fake_collect(refs_list, api_key, **kwargs):
            collected.extend(refs_list or [])
            return []

        with patch("key_setup.load_key", return_value="sk-fake"), \
             patch.object(sb, "_collect_image_urls", side_effect=fake_collect), \
             patch.object(sb, "cast_prompt", return_value=None):
            try:
                sb.render_storyboard(plan_path, self.tmp)
            except Exception:
                pass  # 没有真实 API，允许后续失败

        # 关键：product_sku 展开后，front/side 路径出现在 collect 的 refs 里
        expanded = [r for r in collected if "front" in r or "side" in r]
        self.assertTrue(len(expanded) >= 1,
                        "product_sku 应展开方位图路径，got collected=%s" % collected)

    # T12 cast_prompt — digital_human_portraits 注入块
    def test_12_cast_prompt_portraits(self):
        plan = {
            "title": "测试", "cast": "主播",
            "characters": [{"id": "host", "name": "主播", "role": "presenter",
                            "gender": "female", "appearance": "专业形象"}],
            "asset_refs": {"digital_human_portraits": ["actors/acme/host/portrait.png"]}
        }
        result = sb.cast_prompt(plan)
        self.assertIsNotNone(result)
        self.assertIn("CRITICAL", result)
        self.assertIn("portrait.png", result)

    # T13 shot_prompt — product_images 注入块
    def test_13_shot_prompt_product(self):
        plan = {
            "title": "椅子广告", "cast": "主播",
            "aspect_ratio": "9:16",
            "asset_refs": {
                "product_images": ["assets/acme/product/chair/views/front.png"],
                "scene_images": ["assets/acme/images/store.jpg"]
            }
        }
        shot = {"id": "s1", "scene": "开场", "visual": "产品正面", "voiceover": "介绍"}
        result = sb.shot_prompt(plan, shot, 1)
        self.assertIn("CRITICAL", result)
        self.assertIn("front.png", result)


# ─────────────────────────────────────────────────────────────
# T14-T15  video_engine render_batch product_sku
# ─────────────────────────────────────────────────────────────
import video_engine as ve
import br_client

class TestVideoEngine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ve_test_")
        self._orig_assets = pl.ASSETS_DIR
        pl.ASSETS_DIR = self.tmp

    def tearDown(self):
        pl.ASSETS_DIR = self._orig_assets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_product(self, views=("front", "side")):
        pl.create_product("acme", "chair")
        for v in views:
            _make_png(pl._view_path("acme", "chair", v))

    # T14 render_batch product_sku 展开 — 验证 create_video 收到 ref_urls
    def test_14_render_batch_product_sku(self):
        self._make_product(["front", "side"])
        segs = [{"text": "产品展示", "product_sku": "chair",
                 "ratio": "9:16", "duration": 5,
                 "out_path": os.path.join(self.tmp, "out.mp4")}]

        submitted_types = []
        submitted_urls  = []

        def fake_create_video(api_key, text, model, video_type, urls, **kw):
            submitted_types.append(video_type)
            submitted_urls.append(urls)
            return "fake-task-id"

        def fake_get_video(api_key, tid):
            return {"status": "succeed",
                    "videoUrl": "https://fake/video.mp4"}

        fake_dl = MagicMock()

        with patch("key_setup.load_key", return_value="sk-fake"), \
             patch.object(br_client, "create_video", side_effect=fake_create_video), \
             patch.object(br_client, "get_video",    side_effect=fake_get_video), \
             patch.object(br_client, "download",     fake_dl), \
             patch.object(br_client, "to_image_ref", side_effect=lambda u, **kw: "ref:"+str(u)), \
             patch("time.sleep"):
            results = ve.render_batch(segs, client="acme", verbose=False, draft=True)

        self.assertEqual(len(results), 1)
        # video_type 应被展开为 5（front + side = refs >= 1）
        self.assertEqual(submitted_types[0], 5,
                         "product_sku 有2方位图，应升级为 videoType=5，got %s" % submitted_types)
        # ref_urls 应含2个
        self.assertEqual(len(submitted_urls[0]), 2,
                         "应传入 2 个方位图 ref，got %s" % submitted_urls[0])

    # T15 render_batch — client 参数透传，seg 里也能指定 client 覆盖
    def test_15_render_batch_client_override(self):
        # 只给 acme 建产品，seg.client=acme 能正确找到
        self._make_product(["front"])
        segs = [{"text": "T", "product_sku": "chair", "client": "acme",
                 "ratio": "9:16", "duration": 5,
                 "out_path": os.path.join(self.tmp, "out.mp4")}]
        submitted_urls = []
        def fake_cv(api_key, text, model, video_type, urls, **kw):
            submitted_urls.append(urls)
            return "tid"
        def fake_gv(api_key, tid):
            return {"status": "succeed", "videoUrl": "https://fake/v.mp4"}
        with patch("key_setup.load_key", return_value="sk-fake"), \
             patch.object(br_client, "create_video", side_effect=fake_cv), \
             patch.object(br_client, "get_video",    side_effect=fake_gv), \
             patch.object(br_client, "download",     MagicMock()), \
             patch.object(br_client, "to_image_ref", side_effect=lambda u, **kw: "ref:"+str(u)), \
             patch("time.sleep"):
            # 这里 render_batch 级 client=None（不传），seg 里有 client=acme
            ve.render_batch(segs, client=None, verbose=False, draft=True)
        self.assertEqual(len(submitted_urls[0]), 1)  # front 图


# ─────────────────────────────────────────────────────────────
# T16-T17  fuse
# ─────────────────────────────────────────────────────────────
class TestFuse(unittest.TestCase):

    # T16 overlay 签名 mix_audio 默认 True
    def test_16_overlay_default_mix_audio(self):
        import inspect
        sig = inspect.signature(fuse.overlay)
        self.assertIn("mix_audio", sig.parameters)
        self.assertTrue(sig.parameters["mix_audio"].default,
                        "mix_audio 默认值应为 True")

    # T17 _slot_geometry 各 slot 坐标合理
    def test_17_slot_geometry(self):
        W, H = 1080, 1920
        for slot in ("left", "right", "corner", "full"):
            tw, th, ox, oy = fuse._slot_geometry(slot, W, H, scale=0.9)
            # th 不应超过画布高
            self.assertLess(th, H, "slot=%s th=%s 超过画布高" % (slot, th))
            # ox/oy 是 ffmpeg 表达式字符串或数字
            self.assertIsNotNone(ox)
            self.assertIsNotNone(oy)


# ─────────────────────────────────────────────────────────────
# T18  br_client host_image 进程级缓存
# ─────────────────────────────────────────────────────────────
import br_client as brc

class TestBrClient(unittest.TestCase):

    def setUp(self):
        brc._HOST_IMAGE_CACHE.clear()

    def tearDown(self):
        brc._HOST_IMAGE_CACHE.clear()

    # T18 同一路径第二次调用不走 API
    def test_18_host_image_cache(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        _make_png(tmp.name); tmp.close()
        try:
            call_count = [0]
            def fake_create_image_generation(api_key, prompt, **kw):
                call_count[0] += 1
                return "img-host"
            def fake_dl(url, dest):
                shutil.copyfile(tmp.name, dest)

            with patch.object(brc, "create_image",
                              side_effect=AssertionError("legacy sync path used")), \
                 patch.object(brc, "create_image_generation",
                              side_effect=fake_create_image_generation), \
                 patch.object(brc, "wait_image_generation",
                              return_value=["https://fake/hosted.png"]), \
                 patch.object(brc, "download", side_effect=fake_dl):
                r1 = brc.host_image("sk-test", tmp.name)
                r2 = brc.host_image("sk-test", tmp.name)
            self.assertEqual(call_count[0], 1, "缓存失效：API 被调用了 %d 次" % call_count[0])
            self.assertEqual(r1, r2)
        finally:
            os.unlink(tmp.name)

    def test_invalid_local_image_is_rejected_before_upload(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(b"fake portrait bytes")
        tmp.close()
        try:
            with self.assertRaises(brc.BRError) as ctx:
                brc.to_image_ref(tmp.name)
            self.assertIn("invalid image file", str(ctx.exception))
        finally:
            os.unlink(tmp.name)


# ─────────────────────────────────────────────────────────────
# T19-T20  guide_scaffold
# ─────────────────────────────────────────────────────────────
class TestGuideScaffold(unittest.TestCase):

    def _guide(self, **extra):
        base = {
            "title": "测试广告",
            "aspect_ratio": "9:16",
            "rows": [
                {"id": "s1", "role": "product_hero", "duration": 8,
                 "visual": "产品正面", "voiceover": "欢迎"}
            ]
        }
        base.update(extra)
        return base

    # T19 has_digital_human=False → shots 里无 humanSlot
    def test_19_no_human_slot(self):
        guide = self._guide(has_digital_human=False)
        result = guide_scaffold.compile_shots(guide)
        self.assertIsInstance(result, dict)
        for s in result.get("shots", []):
            self.assertNotIn("humanSlot", s,
                             "has_digital_human=False 时不应有 humanSlot")

    # T20 compile_shots 输出顶层 width/height（不是 resolution 数组）
    def test_20_width_height_output(self):
        guide = self._guide()
        result = guide_scaffold.compile_shots(guide)
        # compile_shots 返回顶层 dict，width/height 在顶层
        self.assertIsInstance(result, dict,
                              "compile_shots 应返回 dict，got %s" % type(result))
        self.assertIn("width", result)
        self.assertIn("height", result)
        self.assertIsInstance(result["width"], int)
        self.assertIsInstance(result["height"], int)
        self.assertNotIn("resolution", result,
                         "不应有 resolution 数组，应改用 width/height")


if __name__ == "__main__":
    unittest.main(verbosity=2)
