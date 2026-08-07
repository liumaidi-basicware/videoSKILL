import json
import os
import hashlib
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import guide_scaffold
import script_splitter as ss
import storyboard
import video_segmentation
import run_manifest
import take_review


class StrictStoryboardHandoffTests(unittest.TestCase):
    def _approved_storyboard(self, root, plan, shot_id="s1"):
        canonical = storyboard.canonical_storyboard_plan(plan)
        image = os.path.join(root, "board.jpg")
        with open(image, "wb") as handle:
            handle.write(b"board")
        result_path = os.path.join(root, "storyboard_result.json")
        result = {
            "client": "acme", "run_id": "run-1", "out_dir": root,
            "plan_fingerprint": storyboard.plan_fingerprint(plan),
            "shots": [{"shot": {"id": shot_id}, "abspath": image,
                       "url": "https://cdn.example/%s.png" % shot_id}],
        }
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        storyboard.confirm_storyboard(result_path)
        return canonical, image

    def test_split_requires_client(self):
        with self.assertRaisesRegex(ValueError, "CLIENT_REQUIRED"):
            ss.split({"shots": [{"id": "s1", "duration": 3}]}, allow_text2video=True)

    def test_canonical_fingerprint_is_idempotent_after_partition(self):
        plan = {"client": "acme", "shots": [{"id": "s1", "duration": 20}]}
        canonical = storyboard.canonical_storyboard_plan(plan)
        self.assertEqual(storyboard.plan_fingerprint(plan), storyboard.plan_fingerprint(canonical))

    def test_storyboard_canonical_preserves_short_script_shots(self):
        plan = {"client": "acme", "scene_type": "product-showcase", "shots": [
            {"id": "s1", "duration": 4},
            {"id": "s2", "duration": 5},
            {"id": "s3", "duration": 5},
            {"id": "s4", "duration": 7},
            {"id": "s5", "duration": 4},
        ]}
        canonical = storyboard.canonical_storyboard_plan(plan)
        self.assertEqual([shot["id"] for shot in canonical["shots"]],
                         ["s1", "s2", "s3", "s4", "s5"])
        self.assertEqual([shot["panel_index"] for shot in canonical["shots"]],
                         [1, 2, 3, 4, 5])

    def test_split_requires_current_approval_and_carries_identity(self):
        plan = {"client": "acme", "shots": [{"id": "s1", "duration": 3}]}
        with tempfile.TemporaryDirectory() as root:
            _, image = self._approved_storyboard(root, plan)
            result = ss.split(plan, storyboard_dir=root, client="acme")
            self.assertEqual(result["run_id"], "run-1")
            self.assertEqual(result["storyboard_approval"]["status"], "confirmed")
            self.assertEqual(result["segments"][0]["storyboard_path"], image)
            self.assertEqual(result["segments"][0]["client"], "acme")
            self.assertIn(os.path.join("acme", "run-1"), result["segments"][0]["out_path"])
            with open(image, "ab") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(ValueError, "STORYBOARD_APPROVAL_REQUIRED"):
                ss.split(plan, storyboard_dir=root, client="acme")

    def test_runtime_storyboard_enrichment_does_not_make_source_stale(self):
        source = {"id": "s1", "duration": 3, "visual": "产品特写",
                  "characters": [], "ref_tags": ["@product"]}
        rendered = dict(source, approved_prompt_zh="已确认导演提示",
                        panel_plan=["wide"] * 12,
                        references=[{"tag": "@product"}])
        result = {"shots": [{"shot": rendered, "shot_fingerprint": "old",
                             "path": __file__}]}
        self.assertFalse(ss._stale_storyboard_shot_ids(
            result, [source], storyboard))

    def test_authored_visual_edit_still_makes_storyboard_stale(self):
        source = {"id": "s1", "duration": 3, "visual": "新的产品特写",
                  "characters": [], "ref_tags": ["@product"]}
        rendered = dict(source, visual="旧的产品特写",
                        approved_prompt_zh="已确认导演提示")
        result = {"shots": [{"shot": rendered, "shot_fingerprint": "old",
                             "path": __file__}]}
        self.assertEqual(ss._stale_storyboard_shot_ids(
            result, [source], storyboard), {"s1"})

    def test_duration_only_edit_does_not_make_storyboard_stale(self):
        source = {"id": "s1", "duration": 7, "visual": "产品特写",
                  "characters": [], "ref_tags": ["@product"]}
        rendered = dict(source, duration=4, approved_prompt_zh="已确认导演提示")
        result = {"shots": [{"shot": rendered, "shot_fingerprint": "old",
                             "path": __file__}]}
        self.assertFalse(ss._stale_storyboard_shot_ids(
            result, [source], storyboard))

    def test_split_preserves_storyboard_shots_instead_of_packing_segments(self):
        plan = {"client": "acme", "scene_type": "product-showcase", "shots": [
            {"id": "s1", "duration": 4, "dialogue": "第一句。"},
            {"id": "s2", "duration": 5, "dialogue": "第二句。"},
            {"id": "s3", "duration": 5, "dialogue": "第三句。"},
            {"id": "s4", "duration": 7, "dialogue": "两台 TWS 串联就是立体声。"},
            {"id": "s5", "duration": 4, "dialogue": "CTA。"},
        ]}
        with tempfile.TemporaryDirectory() as root:
            self._approved_storyboard(root, plan)
            result = ss.split(plan, storyboard_dir=root, client="acme",
                              allow_text2video=True)
        self.assertEqual([segment["id"] for segment in result["segments"]],
                         ["s1", "s2", "s3", "s4", "s5"])
        self.assertEqual(result["segments"][3]["dialogue"], "两台 TWS 串联就是立体声。")

    def test_split_injects_non_empty_continuity_for_independent_storyboard_segments(self):
        plan = {"client": "acme", "scene_type": "product-showcase",
                "language": "中文普通话", "voice_type": "年轻女声",
                "audio": {"bgm": "轻快电子节拍", "sfx": "轻微磁吸咔哒声"},
                "shots": [
            {"id": "s1", "duration": 4, "visual": "产品正面出现",
             "shot_size": "近景", "camera_movement": "push", "composition": "产品居中",
             "dialogue": "第一段介绍。",
             "ref_tags": ["@product"]},
            {"id": "s2", "duration": 4, "visual": "产品磁吸到手机背面",
             "shot_size": "手部近景", "camera_movement": "pan", "composition": "手机在左",
             "dialogue": "第二段延续。",
             "ref_tags": ["@product"]},
        ]}
        with tempfile.TemporaryDirectory() as root:
            self._approved_storyboard(root, plan)
            result = ss.split(plan, storyboard_dir=root, client="acme",
                              allow_text2video=True)
        first, second = result["segments"]
        self.assertNotIn(first["continuity_in"], (None, "", "{}"))
        self.assertNotIn(first["continuity_out"], (None, "", "{}"))
        self.assertIn("下一段", first["continuity_out"])
        self.assertIn("承接上一段", second["continuity_in"])
        self.assertEqual(first["storyboard_ref_mode"], "native_storyboard")
        self.assertEqual(second["storyboard_ref_mode"], "native_storyboard")
        for segment in (first, second):
            audio = segment["audio_contract"]
            self.assertIn("voice_continuity", audio)
            self.assertIn("bgm_continuity", audio)
            self.assertIn("sfx_continuity", audio)
            self.assertEqual(audio["voice_continuity_method"], "text_contract_and_human_qc")
            self.assertEqual(audio["bgm_continuity_method"], "post_mix_preferred")
        self.assertIn("承接上一段", second["audio_contract"]["voice_continuity"])
        self.assertIn("BGM 从上一段", second["audio_contract"]["bgm_continuity"])

    def test_result_mapping_uses_exact_ids_and_aggregate_source_ids(self):
        mapping = {"s1": "/tmp/s1.jpg", "s10": "/tmp/s10.jpg"}
        self.assertEqual(ss._find_shot_image(mapping, "segment_01", ["s1", "s2"]),
                         "/tmp/s1.jpg")
        self.assertIsNone(ss._find_shot_image(mapping, "s", []))

    def test_draft_flag_is_the_only_unapproved_storyboard_bypass(self):
        plan = {"client": "acme", "shots": [{"id": "s1", "duration": 3}]}
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "STORYBOARD_APPROVAL_REQUIRED"):
                ss.split(plan, storyboard_dir=root, client="acme", allow_text2video=True)
            result = ss.split(plan, storyboard_dir=root, client="acme",
                              allow_text2video=True,
                              draft_allow_unapproved_storyboard=True)
            self.assertEqual(result["storyboard_approval"]["status"], "draft")

    def test_aggregation_merges_render_metadata(self):
        result = video_segmentation.partition_shots([
            {"id": "a", "duration": 5, "asset_refs": {"product_images": ["a.png"]},
             "characters": ["host"], "motion_elements": ["one"]},
            {"id": "b", "duration": 5, "asset_refs": {"scene_images": ["b.png"]},
             "characters": ["guest"], "motion_elements": ["two"]},
        ])[0]
        self.assertEqual(result["asset_refs"]["product_images"], ["a.png"])
        self.assertEqual(result["asset_refs"]["scene_images"], ["b.png"])
        self.assertEqual(result["characters"], ["host", "guest"])
        self.assertEqual(result["motion_elements"], ["one", "two"])

    def test_assemble_requires_structured_complete_results(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source.mp4")
            with open(source, "wb") as handle:
                handle.write(b"video")
            segment = {"id": "s1", "out_path": source, "video_handoff_fingerprint": "fp"}
            with self.assertRaisesRegex(TypeError, "STRUCTURED_RESULTS_REQUIRED"):
                ss.assemble([segment], [source], os.path.join(root, "out.mp4"))
            result = {"ok": True, "segment_id": "s1", "localPath": source,
                      "ocr_warning": False, "video_handoff_fingerprint": "fp"}
            result["take_fingerprint"] = take_review.take_fingerprint(result)
            assembled = ss.assemble([segment], [result], os.path.join(root, "out.mp4"))
            self.assertTrue(assembled["ok"])

    def test_derive_captions_uses_each_result_duration(self):
        spec = {"segments": [{"id": "a", "duration": 3, "dialogue": "A"},
                             {"id": "b", "duration": 3, "dialogue": "B"}]}
        results = [{"segment_id": "a", "actual_duration": 4.25},
                   {"segment_id": "b", "duration": 5.75}]
        derived = ss.derive_captions(spec, per_sentence=False, results=results)
        self.assertEqual(derived["lines"][1]["start"], 4.25)
        self.assertEqual(derived["total_seconds"], 10.0)

    def test_manifest_rejects_handoff_without_confirmed_storyboard_identity(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        segment = {"id": "s1", "client": "acme", "run_id": "run-1",
                   "video_handoff_fingerprint": "forged"}
        with self.assertRaisesRegex(ValueError, "STORYBOARD_APPROVAL_REQUIRED"):
            run_manifest.record_video_handoff(
                manifest, {"client": "acme", "run_id": "run-1",
                           "segments": [segment]})

    def test_manifest_rejects_empty_handoff_fingerprint(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        approval = {"status": "confirmed", "client": "acme", "run_id": "run-1"}
        segment = {"id": "s1", "client": "acme", "run_id": "run-1",
                   "storyboard_approval": approval}
        with self.assertRaisesRegex(ValueError, "STALE_VIDEO_HANDOFF"):
            run_manifest.record_video_handoff(manifest, {
                "client": "acme", "run_id": "run-1", "segments": [segment],
                "storyboard_approval": approval, "missing_images": [], "needs_image": []})

    def test_split_includes_only_current_confirmed_generated_boards(self):
        plan = {"client": "acme", "characters": [{"id": "host"}],
                "shots": [{"id": "s1", "duration": 3}]}
        with tempfile.TemporaryDirectory() as root:
            _, shot_image = self._approved_storyboard(root, plan)
            board = os.path.join(root, "cast_board.jpg")
            with open(board, "wb") as handle:
                handle.write(b"cast-v1")
            result_path = os.path.join(root, "storyboard_result.json")
            with open(result_path, encoding="utf-8") as handle:
                result = json.load(handle)
            result["model"] = "gpt-image-2"
            result["cast_board"] = {
                "path": board, "source_fingerprint": "cast-source",
                "url": "https://cdn.example/cast.png"}
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            storyboard.confirm_board(result_path, "cast")
            storyboard.confirm_storyboard(result_path)
            split = ss.split(plan, storyboard_dir=root, client="acme")
            sources = [ref["source"] for ref in split["segments"][0]["references"]]
            self.assertIn("asset_refs.cast_boards", sources)
            self.assertIn("https://cdn.example/s1.png", split["segments"][0]["urls"])
            with open(board, "ab") as handle:
                handle.write(b"changed")
            split = ss.split(plan, storyboard_dir=root, client="acme")
            sources = [ref["source"] for ref in split["segments"][0]["references"]]
            self.assertNotIn("asset_refs.cast_boards", sources)

    def test_split_remaps_registry_to_registry_reused_confirmed_cast_board(self):
        plan = {"client": "acme", "characters": [{"id": "host"}],
                "asset_refs": {"digital_human_portraits": ["actors/shared/host/portrait.png"]},
                "references": [{"tag": "@host", "type": "character_identity",
                                "url": "actors/shared/host/portrait.png"}],
                "shots": [{"id": "s1", "duration": 3, "characters": ["host"],
                           "ref_tags": ["@host"]}]}
        with tempfile.TemporaryDirectory() as root:
            _, _shot_image = self._approved_storyboard(root, plan)
            registry_board_dir = os.path.join(root, "registry")
            os.makedirs(registry_board_dir)
            board = os.path.join(registry_board_dir, "cast_board.jpg")
            with open(board, "wb") as handle:
                handle.write(b"cast-confirmed")
            board_sha = hashlib.sha256(b"cast-confirmed").hexdigest()
            result_path = os.path.join(root, "storyboard_result.json")
            with open(result_path, encoding="utf-8") as handle:
                result = json.load(handle)
            result["model"] = "gpt-image-2"
            result["reference_registry"] = [{
                "tag": "@host",
                "type": "character_identity",
                "source": "Mina confirmed portrait",
                "url": "actors/shared/host/portrait.png",
            }]
            result["cast_board"] = {
                "status": "confirmed",
                "path": board,
                "abspath": board,
                "source_fingerprint": "cast-source",
                "board_sha256": board_sha,
                "url": "https://cdn.example/cast-board.png",
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            storyboard.confirm_storyboard(result_path)
            split = ss.split(plan, storyboard_dir=root, client="acme")
            refs = split["segments"][0]["references"]
            self.assertEqual(refs[0]["tag"], "@host")
            self.assertEqual(refs[0]["source"], "asset_refs.cast_boards")
            self.assertEqual(refs[0]["type"], "character_board")
            self.assertEqual(refs[0]["url"], "https://cdn.example/cast-board.png")

    def test_split_usage_reference_requires_actual_physical_use_action(self):
        registry = [
            {"tag": "@usage", "type": "product_usage_identity",
             "url": "https://cdn.example/usage.png"},
            {"tag": "@product", "type": "product_board",
             "url": "https://cdn.example/product.png"},
            {"tag": "@host", "type": "character_board",
             "url": "https://cdn.example/host.png"},
        ]
        physical = {
            "id": "s2",
            "ref_tags": ["@product"],
            "visual": "hand attaches the product bottom magnetic surface to the phone back",
            "character_action": "attach product bottom to phone back and release",
        }
        refs, _ = ss._collect_typed_references(
            physical, {"reference_registry": registry},
            "https://cdn.example/storyboard-s2.png")
        self.assertEqual([ref["tag"] for ref in refs],
                         ["@usage", "@product", "@storyboard"])

        presenter = {
            "id": "s5",
            "characters": ["host"],
            "ref_tags": ["@host", "@product"],
            "visual": "host smiles and holds the product toward camera",
            "character_action": "host recommends the product",
        }
        refs, _ = ss._collect_typed_references(
            presenter, {"reference_registry": registry},
            "https://cdn.example/storyboard-s5.png")
        self.assertEqual([ref["tag"] for ref in refs],
                         ["@host", "@product", "@storyboard"])

    def test_split_required_reference_types_follow_actual_usage_refs(self):
        plan = {"client": "acme", "characters": [{"id": "host"}],
                "asset_refs": {
                    "product_usage_images": ["https://cdn.example/usage.png"],
                    "product_boards": ["https://cdn.example/product.png"],
                    "cast_boards": ["https://cdn.example/host.png"],
                },
                "shots": [
                    {"id": "use", "duration": 3, "characters": [],
                     "ref_tags": ["@product"],
                     "visual": "hand attaches product bottom magnetic surface to phone back",
                     "character_action": "attach product bottom to phone back"},
                    {"id": "cta", "duration": 3, "characters": ["host"],
                     "ref_tags": ["@host", "@product"],
                     "visual": "host smiles and holds the product toward camera",
                     "character_action": "host recommends the product"},
                ]}
        split = ss.split(plan, client="acme")
        by_id = {segment["id"]: segment for segment in split["segments"]}
        self.assertIn("product_usage_identity", by_id["use"]["required_reference_types"])
        self.assertNotIn("product_usage_identity", by_id["cta"]["required_reference_types"])

    def test_guide_emits_primary_schema_and_keeps_metadata(self):
        guide = {"client": "acme", "run_id": "run-2", "kind": "product",
                 "theme": "demo", "has_digital_human": False,
                 "rows": [{"id": "r1", "talk": "hello", "seconds": 3,
                           "image": "hero.png", "image_role": "hero"},
                          {"id": "r2", "talk": "missing", "seconds": 3}]}
        result = guide_scaffold.compile_segments(guide)
        self.assertEqual(result["needs_image"], ["r2"])
        self.assertEqual(result["segments"][0]["video_type"], 5)
        self.assertEqual(result["segments"][0]["client"], "acme")
        self.assertEqual(result["storyboard_approval"]["status"], "not_applicable")
        self.assertEqual(result["segments"][0]["storyboard_approval"],
                         result["storyboard_approval"])
        self.assertEqual(result["guide_metadata"]["theme"], "demo")


if __name__ == "__main__":
    unittest.main()
