import json
import os
import sys
import base64
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import storyboard  # noqa: E402
import video_engine  # noqa: E402
import seedance_prompt  # noqa: E402


class StoryboardPanelBindingTests(unittest.TestCase):
    def _write_png(self, path):
        data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
        with open(path, "wb") as handle:
            handle.write(data)

    def test_protocol_docs_describe_seedance_native_and_kling_panel_fallback(self):
        docs = []
        for relpath in (
                "README.md",
                "AGENTS.md",
                "CUSTOMER_GUIDE.md",
                "START_HERE_CODEX_DESKTOP.md",
                "references/professional-storyboard-enrichment.md",
                "skills/story-video-orchestrator/SKILL.md",
        ):
            with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
                docs.append(handle.read())
        combined = "\n".join(docs)
        self.assertIn("Seedance", combined)
        self.assertIn("Kling", combined)
        self.assertIn("原生故事板", combined)
        self.assertIn("单格展开", combined)
        self.assertNotIn("12 格故事板转视频说明", combined)
        self.assertNotIn("必须把最终 16:9、4x3、12 格故事板作为主要视觉参考", combined)
        self.assertNotIn("16:9、4x3、12格故事板", combined)
        self.assertNotIn("按 1→12 格顺序生成连续视频", combined)

    def test_basicrouter_docs_use_async_v1_generation_endpoints(self):
        docs = []
        for relpath in (
                "AGENTS.md",
                "skills/basicrouter-multimodal-api/SKILL.md",
                "skills/basicrouter-multimodal-api/references/api-reference.md",
        ):
            with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
                docs.append(handle.read())
        combined = "\n".join(docs)
        self.assertIn("/v1/image-generations", combined)
        self.assertIn("/v1/video-generations", combined)
        self.assertNotIn("Image gen / img2img | POST | `/ai/createImage`", combined)
        self.assertNotIn("Video gen | POST | `/ai/createVideo`", combined)

    def test_storyboard_ref_help_mentions_model_aware_routing(self):
        with open(os.path.join(ROOT, "scripts", "video_engine.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("Seedance 优先原生 storyboarding", source)
        self.assertIn("Kling fallback 才生成单格", source)

    def test_expansion_uses_sheet_and_only_declared_tagged_assets(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            host = os.path.join(root, "host.jpg")
            product = os.path.join(root, "product.jpg")
            for path in (sheet, host, product):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {
                "id": "s2", "storyboard_path": sheet, "storyboard_panel_index": 2,
                "ref_tags": ["@host"], "text": "主持人拿起产品",
                "references": [{"tag": "@host", "url": host},
                               {"tag": "@product", "url": product}],
            }
            with mock.patch.object(storyboard._br, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(storyboard, "download_first_image", return_value={"path": "x"}) as generate:
                result = storyboard.expand_storyboard_panel("sk-test", segment, out_dir=root)
            self.assertIn("shot_s2_keyframe.jpg", result["abspath"])
            self.assertEqual(generate.call_args.kwargs["image_urls"], [sheet, host])
            self.assertIn("THIS CURRENT SHOT", generate.call_args.args[1])
            self.assertIn("@host", generate.call_args.args[1])
            self.assertNotIn("@product", generate.call_args.args[1])

    def test_expansion_cache_requires_same_recipe(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            host = os.path.join(root, "host.jpg")
            for path in (sheet, host):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {"id": "s1", "storyboard_path": sheet, "storyboard_panel_index": 1,
                       "ref_tags": ["@host"], "references": [{"tag": "@host", "url": host}]}
            panel = os.path.join(root, "expanded_panels", "shot_s1_keyframe.jpg")
            os.makedirs(os.path.dirname(panel))
            with open(panel, "wb") as handle:
                handle.write(b"expanded")
            recipe = storyboard.artifact_contract.build_storyboard_panel_recipe(segment)
            with open(panel + ".json", "w", encoding="utf-8") as handle:
                json.dump({"recipe_sha256": storyboard.artifact_contract.sha256_json(recipe)}, handle)
            with mock.patch.object(storyboard._br, "to_image_ref") as convert, \
                    mock.patch.object(storyboard, "download_first_image") as generate:
                result = storyboard.expand_storyboard_panel("sk-test", segment, out_dir=root)
            self.assertTrue(result["skipped"])
            convert.assert_not_called()
            generate.assert_not_called()

    def test_prompt_inlines_panel_tags_and_uses_native_seedance_storyboard_rule(self):
        prompt = seedance_prompt.compile_prompt({
            "duration": 4, "storyboard_ref": True, "storyboard_panel_index": 3,
            "storyboard_ref_mode": "native_storyboard",
            "ref_tags": ["@host", "@product"],
            "timeline": [{"start": 0, "end": 4, "action": "拿起产品", "camera": "push"}],
        })
        self.assertIn("镜头3，@host @product 拿起产品", prompt)
        self.assertIn("Seedance 原生故事板转视频规则", prompt)
        self.assertIn("最终多格故事板", prompt)
        self.assertNotIn("按1→12顺序演", prompt)

    def test_kling_prompt_inlines_panel_tags_and_uses_single_panel_rule(self):
        prompt = seedance_prompt.compile_prompt({
            "duration": 4, "storyboard_ref": True, "storyboard_panel_index": 3,
            "storyboard_ref_mode": "expanded_panel",
            "ref_tags": ["@host", "@product"],
            "timeline": [{"start": 0, "end": 4, "action": "拿起产品", "camera": "push"}],
        }, target_model="kling-v3-omni-video")
        self.assertIn("镜头3，@host @product 拿起产品", prompt)
        self.assertIn("当前镜头专属的单格展开图", prompt)
        self.assertNotIn("最终多格故事板", prompt)

    def test_video_submission_replaces_contact_sheet_with_expanded_panel(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            panel = os.path.join(root, "panel.jpg")
            host = os.path.join(root, "host.jpg")
            for path in (sheet, panel, host):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {"id": "s1", "text": "动作", "storyboard_ref": True,
                       "storyboard_path": sheet, "storyboard_dir": root,
                       "storyboard_panel_index": 1, "ref_tags": ["@host"],
                       "urls": [sheet, host], "references": [{"tag": "@host", "url": host}],
                       "video_type": 5}
            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                    mock.patch.object(video_engine, "_expanded_storyboard_reference", return_value={
                        "abspath": panel,
                        "recipe_sha256": video_engine.artifact_contract.sha256_json(
                            video_engine.artifact_contract.build_storyboard_panel_recipe(segment))}), \
                    mock.patch.object(video_engine.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(video_engine, "_pick_video_model", return_value="kling-v3-omni-video"), \
                    mock.patch.object(video_engine, "_submit_video", return_value=("task", "prompt")) as submit, \
                    mock.patch.object(video_engine.br_client, "get_video", return_value={"status": "failed"}):
                video_engine.render_batch([segment], verbose=False, draft=True, max_wait=0)
            self.assertEqual(submit.call_args.args[4], [panel, host])

    def test_seedance_native_mode_submits_contact_sheet_without_expanding_panel(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            panel = os.path.join(root, "panel.jpg")
            product = os.path.join(root, "product.jpg")
            for path in (sheet, panel, product):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {
                "id": "s1", "text": "产品单镜头特写", "storyboard_ref": True,
                "storyboard_ref_mode": "native_storyboard",
                "storyboard_path": sheet, "storyboard_dir": root,
                "storyboard_panel_index": 1, "ref_tags": ["@product"],
                "storyboard_url": "https://cdn.example/storyboard.png",
                "references": [{"tag": "@product", "url": "https://cdn.example/product.png"}],
                "video_type": 5,
            }
            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                    mock.patch.object(video_engine, "_expanded_storyboard_reference") as expand, \
                    mock.patch.object(video_engine.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(video_engine, "_pick_video_model", return_value="seedance-2.0"), \
                    mock.patch.object(video_engine, "_submit_video", return_value=("task", "prompt")) as submit, \
                    mock.patch.object(video_engine.br_client, "get_video", return_value={"status": "failed"}):
                video_engine.render_batch([segment], verbose=False, draft=True, max_wait=0)
            expand.assert_not_called()
            self.assertEqual(submit.call_args.args[4], [
                "https://cdn.example/storyboard.png",
                "https://cdn.example/product.png",
            ])
            submitted_segment = submit.call_args.args[1]
            self.assertEqual(submitted_segment["storyboard_ref_mode"], "native_storyboard")

    def test_video_submission_requires_remote_image_urls_without_host_generation(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.png")
            product = os.path.join(root, "product.png")
            self._write_png(sheet)
            self._write_png(product)
            segment = {
                "id": "s1", "text": "产品单镜头特写", "storyboard_ref": True,
                "storyboard_ref_mode": "native_storyboard",
                "storyboard_path": sheet, "storyboard_dir": root,
                "storyboard_panel_index": 1, "ref_tags": ["@product"],
                "references": [{"tag": "@product", "url": product}],
                "video_type": 5,
            }
            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                    mock.patch.object(video_engine.br_client, "host_image",
                                      side_effect=AssertionError("video refs must not use image generation hosting")), \
                    mock.patch.object(video_engine, "_pick_video_model", return_value="seedance-2.0"), \
                    mock.patch.object(video_engine, "_submit_video", return_value=("task", "prompt")) as submit, \
                    mock.patch.object(video_engine.br_client, "get_video", return_value={"status": "failed"}):
                video_engine.render_batch([segment], verbose=False, draft=True, max_wait=0)
                submit.assert_not_called()

                segment["storyboard_path"] = sheet
                segment["storyboard_url"] = "https://cdn.example/storyboard.png"
                segment["references"] = [{"tag": "@product", "url": "https://cdn.example/product.png"}]
                segment["urls"] = ["https://cdn.example/product.png"]
                video_engine.render_batch([segment], verbose=False, draft=True, max_wait=0)
                refs = submit.call_args.args[4]
                self.assertEqual(refs, [
                    "https://cdn.example/storyboard.png",
                    "https://cdn.example/product.png",
                ])
                submitted_segment = submit.call_args.args[1]
                self.assertIsNone(submitted_segment.get("storyboard_panel"))

    def test_chained_seedance_native_mode_does_not_expand_panel(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            product = os.path.join(root, "product.jpg")
            for path in (sheet, product):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {
                "id": "s1", "text": "产品单镜头特写", "duration": 4,
                "storyboard_ref": True, "storyboard_ref_mode": "native_storyboard",
                "storyboard_path": sheet, "storyboard_dir": root,
                "storyboard_panel_index": 1, "ref_tags": ["@product"],
                "storyboard_url": "https://cdn.example/storyboard.png",
                "references": [{"tag": "@product", "url": "https://cdn.example/product.png"}],
                "video_type": 5, "seedance_native": True, "out_path": None,
            }
            submissions = []

            def create(_key, text, **kwargs):
                submissions.append((text, kwargs))
                return "task"

            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                    mock.patch.object(video_engine, "_expanded_storyboard_reference") as expand, \
                    mock.patch.object(video_engine, "_pick_video_model",
                                      side_effect=lambda preferred=None, **_: preferred or "seedance-2.0"), \
                    mock.patch.object(video_engine.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(video_engine.br_client, "create_video", side_effect=create), \
                    mock.patch.object(video_engine.br_client, "wait_video", return_value="https://x/s1.mp4"):
                results = video_engine.render_chained([segment], model="seedance-2.0",
                                                      verbose=False, draft=True)
            self.assertTrue(results[0]["ok"])
            expand.assert_not_called()
            self.assertEqual(submissions[0][1]["urls"], [
                "https://cdn.example/storyboard.png",
                "https://cdn.example/product.png",
            ])
            self.assertEqual(submissions[0][1]["model"], "seedance-2.0")
            self.assertIn("Seedance-native storyboard", submissions[0][0])

    def test_chained_seedance_privacy_fallback_rebuilds_kling_panel_refs(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            panel = os.path.join(root, "panel.jpg")
            product = os.path.join(root, "product.jpg")
            for path in (sheet, panel, product):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {
                "id": "s1", "text": "产品单镜头特写", "duration": 4,
                "storyboard_ref": True, "storyboard_ref_mode": "native_storyboard",
                "storyboard_path": sheet, "storyboard_dir": root,
                "storyboard_panel_index": 1, "ref_tags": ["@product"],
                "storyboard_url": "https://cdn.example/storyboard.png",
                "references": [{"tag": "@product", "url": "https://cdn.example/product.png"}],
                "video_type": 5, "seedance_native": True, "out_path": None,
            }
            recipe_sha = video_engine.artifact_contract.sha256_json(
                video_engine.artifact_contract.build_storyboard_panel_recipe(segment))
            submissions = []

            def create(_key, text, **kwargs):
                submissions.append((text, kwargs))
                return "task-%d" % len(submissions)

            def pick(preferred=None, **_kwargs):
                return preferred or "seedance-2.0"

            with mock.patch.object(video_engine.key_setup, "load_key", return_value="sk-test"), \
                    mock.patch.object(video_engine, "_expanded_storyboard_reference", return_value={
                        "abspath": panel, "recipe_sha256": recipe_sha}) as expand, \
                    mock.patch.object(video_engine, "_pick_video_model", side_effect=pick), \
                    mock.patch.object(video_engine.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(video_engine.br_client, "create_video", side_effect=create), \
                    mock.patch.object(video_engine.br_client, "wait_video", side_effect=[
                        video_engine.br_client.BRVideoReferencePrivacyError("privacy"),
                        "https://x/s1.mp4",
                    ]):
                results = video_engine.render_chained([segment], model="seedance-2.0",
                                                      verbose=False, draft=True)
            self.assertTrue(results[0]["ok"])
            expand.assert_called_once()
            self.assertEqual(submissions[0][1]["urls"], [
                "https://cdn.example/storyboard.png",
                "https://cdn.example/product.png",
            ])
            self.assertEqual(submissions[0][1]["model"], "seedance-2.0")
            self.assertEqual(submissions[1][1]["urls"], [panel, "https://cdn.example/product.png"])
            self.assertEqual(submissions[1][1]["model"], "kling-v3-omni-video")
            self.assertIn("SINGLE 16:9 reference plate", submissions[1][0])

    def test_panel_recipe_binds_sheet_and_reference_file_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            host = os.path.join(root, "host.jpg")
            for path in (sheet, host):
                with open(path, "wb") as handle:
                    handle.write(b"v1")
            segment = {"storyboard_ref": True, "storyboard_path": sheet,
                       "storyboard_panel_index": 1, "ref_tags": ["@host"],
                       "references": [{"tag": "@host", "url": host}]}
            first = storyboard.artifact_contract.build_video_handoff(segment)["fingerprint"]
            with open(host, "wb") as handle:
                handle.write(b"v2")
            second = storyboard.artifact_contract.build_video_handoff(segment)["fingerprint"]
            self.assertNotEqual(first, second)

    def test_human_product_segment_keeps_confirmed_usage_tag(self):
        refs = [
            {"tag": "@host", "type": "character_board"},
            {"tag": "@product", "type": "product_board"},
            {"tag": "@usage", "type": "product_usage_identity"},
        ]
        tags = ["@host", "@product"]
        usage_tags = [ref["tag"] for ref in refs if ref["type"] == "product_usage_identity"]
        self.assertEqual(list(dict.fromkeys(tags + usage_tags)), ["@host", "@product", "@usage"])

    def test_reference_budget_prioritizes_usage_anchor_before_extra_angle(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = os.path.join(root, "sheet.jpg")
            mina = os.path.join(root, "mina.jpg")
            hero = os.path.join(root, "hero.jpg")
            angle = os.path.join(root, "angle.jpg")
            usage = os.path.join(root, "usage.jpg")
            for path in (sheet, mina, hero, angle, usage):
                with open(path, "wb") as handle:
                    handle.write(b"image")
            segment = {
                "id": "s5",
                "storyboard_path": sheet,
                "storyboard_panel_index": 5,
                "ref_tags": ["@mina", "@product_hero", "@product_angle", "@usage"],
                "text": "Mina 展示磁吸使用关系",
                "references": [
                    {"tag": "@mina", "url": mina, "type": "character_board"},
                    {"tag": "@product_hero", "url": hero, "type": "product_board"},
                    {"tag": "@product_angle", "url": angle, "type": "product_board"},
                    {"tag": "@usage", "url": usage, "type": "product_usage_identity"},
                ],
            }
            with mock.patch.object(storyboard._br, "to_image_ref", side_effect=lambda value, **_: value), \
                    mock.patch.object(storyboard, "download_first_image", return_value={"path": "x"}) as generate:
                storyboard.expand_storyboard_panel("sk-test", segment, out_dir=root)
            self.assertEqual(generate.call_args.kwargs["image_urls"], [sheet, usage, mina, hero])
            prompt = generate.call_args.args[1]
            self.assertIn("@usage @mina @product_hero", prompt)
            self.assertIn("@product_angle", prompt)


if __name__ == "__main__":
    unittest.main()
