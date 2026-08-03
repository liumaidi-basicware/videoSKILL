#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run_manifest  # noqa: E402
import script_splitter  # noqa: E402
import storyboard_validator  # noqa: E402
import storyboard  # noqa: E402
import asset_prep  # noqa: E402


class StoryboardSpecTests(unittest.TestCase):
    def test_product_board_is_conditional_on_product_references(self):
        self.assertFalse(storyboard.needs_product_board({"shots": [{"id": "s1"}]}))
        self.assertTrue(storyboard.needs_product_board({"asset_refs": {"product_images": ["hero.png"]}}))
        self.assertTrue(storyboard.needs_product_board({"shots": [{"product_sku": "coffee"}]}))

    def test_product_usage_board_requires_product_and_then_character(self):
        self.assertFalse(storyboard.needs_product_usage_image({"characters": [{"id": "host"}]}))
        self.assertTrue(storyboard.needs_product_usage_image({
            "asset_refs": {"product_images": ["hero.png"]}}))
        self.assertTrue(storyboard.needs_product_usage_image({
            "characters": [{"id": "host"}],
            "asset_refs": {"product_images": ["hero.png"]},
        }))

    def test_product_usage_prompt_requires_real_interaction_details(self):
        prompt = storyboard.product_usage_prompt({
            "characters": [{"id": "host"}],
            "product_facts": {"product_name": "AeroClip S1"},
            "shots": [{"character_action": "host places the earbud on the ear"}],
        })
        for term in ("PRODUCT-IN-USE", "ACTIVELY AND CORRECTLY USING", "finger placement",
                     "contact points", "AeroClip S1", "extra fingers"):
            self.assertIn(term, prompt)

    def test_board_approval_expires_when_source_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = storyboard._approval_path(directory, "product")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"status": "confirmed", "source_fingerprint": "old"}, handle)
            self.assertTrue(storyboard._approval_current(directory, "product", "old"))
            self.assertFalse(storyboard._approval_current(directory, "product", "new"))
    def test_prompt_requires_16x9_twelve_panel_annotated_bw_preview(self):
        prompt = storyboard.shot_prompt({"characters": []}, {"id": "s1"}, 1)
        for term in ("16:9", "TWELVE", "4x3", "BLACK-AND-WHITE",
                     "RED arrows", "BLUE arrows", "GREEN marks", "ORANGE marks",
                     "PURPLE marks", "visible movement"):
            self.assertIn(term, prompt)

    def test_product_prompt_combines_reference_images_and_verified_brief_facts(self):
        plan = {
            "product_facts": {
                "product_name": "AeroClip S1",
                "product_type": "开放式耳夹耳机",
                "color": "珍珠白",
                "usps": ["C 型双 pods，开放式不入耳"],
                "specs": {"weight_per_ear": "约5g"},
            },
            "asset_refs": {
                "product_images": ["hero.jpg", "detail.png"],
                "scene_images": ["scene.jpg"],
            },
        }
        prompt = storyboard.shot_prompt(
            plan,
            {"id": "s1", "props": "AeroClip S1 耳夹耳机", "visual": "展示产品"},
            1,
        )
        self.assertIn("hero.jpg", prompt)
        self.assertIn("detail.png", prompt)
        self.assertIn("AeroClip S1", prompt)
        self.assertIn("开放式耳夹耳机", prompt)
        self.assertIn("约5g", prompt)

    def test_reference_merge_reserves_product_slot_before_scene_context(self):
        refs = storyboard._merge_reference_urls(
            ["portrait.png"],
            ["hero.png", "detail.png"],
            ["scene-a.png", "scene-b.png"],
        )
        self.assertEqual(refs, ["portrait.png", "hero.png", "detail.png", "scene-a.png"])

    def test_hydration_normalizes_relative_product_paths_and_keeps_brief_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            product = os.path.join(directory, "hero.png")
            with open(product, "wb") as handle:
                handle.write(b"image")
            brief = {
                "product_name": "Test Product",
                "product_type": "耳机",
                "color": "白色",
                "usps": ["不入耳"],
                "specs": {"weight": "5g"},
                "images": [{"path": product, "tag": "hero", "status": "confirmed"}],
            }
            with mock.patch.object(asset_prep, "_load_brief", return_value=brief):
                hydrated = storyboard._hydrate_plan_asset_refs({"client": "acme"})
            self.assertEqual(hydrated["asset_refs"]["product_images"], [product])
            self.assertEqual(hydrated["product_facts"]["product_name"], "Test Product")

    def test_validator_rejects_legacy_plan(self):
        result = storyboard_validator.validate_plan({"aspect_ratio": "9:16", "shots": []})
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_validator_accepts_twelve_panel_plan(self):
        shot = {"id": "s1", "panel_plan": [str(i) for i in range(12)],
                "shot_size": "wide", "camera_movement": "handheld",
                "composition": "center", "lighting": "side",
                "character_action": "turns and lunges"}
        self.assertTrue(storyboard_validator.validate_plan({"aspect_ratio": "16:9", "shots": [shot]})["ok"])

    def test_normalize_moves_text_animation_out_of_image_prompts(self):
        plan = {"aspect_ratio": "16:9", "shots": [{
            "id": "s6",
            "visual": "Luna turns toward camera, 浮现价格并显示标签",
            "scene_prompt": "clean studio with no extra props",
            "prop_prompts": ["product remains stable; 价格快闪效果"],
        }]}
        normalized, moved = storyboard_validator.normalize_plan_motion_elements(plan)
        shot = normalized["shots"][0]
        self.assertTrue(moved)
        self.assertIn("motion_elements", shot)
        self.assertTrue(any("浮现价格" in item for item in shot["motion_elements"]))
        self.assertTrue(any("价格快闪效果" in item for item in shot["motion_elements"]))
        self.assertNotRegex(shot["visual"], storyboard_validator.TEXT_IN_FRAME)
        self.assertNotRegex(shot["prop_prompts"][0], storyboard_validator.TEXT_IN_FRAME)

    def test_normalize_preserves_existing_motion_elements(self):
        plan = {"aspect_ratio": "16:9", "shots": [{
            "id": "s1", "visual": "host presents product",
            "motion_elements": ["品牌片尾淡出"],
        }]}
        normalized, moved = storyboard_validator.normalize_plan_motion_elements(plan)
        self.assertFalse(moved)
        self.assertEqual(normalized["shots"][0]["motion_elements"], ["品牌片尾淡出"])

    def test_derived_motion_plan_keeps_postproduction_elements(self):
        derived = script_splitter.derive_captions({"segments": [{
            "id": "s6", "duration": 3, "dialogue": "介绍产品",
            "motion_elements": ["价格浮现：后期叠加", "价格标签快闪"],
        }]})
        self.assertEqual(derived["motion_plan"][0]["motion_elements"], [
            "价格浮现：后期叠加", "价格标签快闪",
        ])

    def test_validator_rejects_legacy_nine_panel_plan(self):
        shot = {"id": "s1", "nine_panel_plan": [str(i) for i in range(9)]}
        result = storyboard_validator.validate_plan({"aspect_ratio": "16:9", "shots": [shot]})
        self.assertFalse(result["ok"])
        self.assertTrue(any("nine_panel_plan" in error for error in result["errors"]))

    def test_validator_rejects_duplicate_ids_invalid_duration_and_unknown_character(self):
        shot = {"id": "s1", "duration": 0, "panel_plan": [str(i) for i in range(12)],
                "shot_size": "wide", "camera_movement": "handheld",
                "composition": "center", "lighting": "side",
                "character_action": "turns", "characters": ["missing"]}
        plan = {"aspect_ratio": "16:9",
                "characters": [{"id": "host", "appearance": "clear"},
                               {"id": "host", "appearance": "duplicate"}],
                "shots": [shot, dict(shot)]}
        result = storyboard_validator.validate_plan(plan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("duration" in error for error in result["errors"]))
        self.assertTrue(any("角色 id 重复" in error for error in result["errors"]))
        self.assertTrue(any("不存在的角色" in error for error in result["errors"]))


class ManifestTests(unittest.TestCase):
    def test_manifest_approval_and_atomic_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            manifest = run_manifest.create_manifest("acme", "run-1")
            run_manifest.approve(manifest, "script")
            run_manifest.save_manifest(manifest, path)
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.assertTrue(loaded["approvals"]["script"])
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_strict_cli_approval_requires_previous_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = run_manifest.create_manifest("acme", "run-1")
            brief = os.path.join(directory, "brief.json")
            script = os.path.join(directory, "script.json")
            for path in (brief, script):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({}, handle)
            run_manifest.bootstrap_pending_approval(manifest, "script", [script])
            with self.assertRaises(ValueError):
                run_manifest.approve(manifest, "script", strict=True)
            run_manifest.bootstrap_pending_approval(manifest, "brief", [brief])
            run_manifest.approve(manifest, "brief", strict=True)
            run_manifest.approve(manifest, "script", strict=True)
            self.assertTrue(manifest["approvals"]["script"])

    def test_upstream_approval_invalidates_downstream_and_hash_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = run_manifest.create_manifest("acme", "run-1")
            paths = {}
            for stage in ("brief", "script"):
                paths[stage] = os.path.join(directory, "%s.json" % stage)
                with open(paths[stage], "w", encoding="utf-8") as handle:
                    json.dump({"stage": stage}, handle)
                run_manifest.bootstrap_pending_approval(manifest, stage, [paths[stage]])
                run_manifest.approve(manifest, stage, strict=True)
            self.assertTrue(run_manifest.approval_is_current(manifest, "script"))
            with open(paths["script"], "w", encoding="utf-8") as handle:
                json.dump({"changed": True}, handle)
            self.assertFalse(run_manifest.approval_is_current(manifest, "script"))
            run_manifest.bootstrap_pending_approval(manifest, "brief", [paths["brief"]])
            run_manifest.approve(manifest, "brief", strict=True)
            self.assertFalse(manifest["approvals"]["script"])

    def test_create_command_does_not_overwrite_existing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            with self.assertRaises(SystemExit) as context:
                run_manifest.main(["create", "--client", "acme", "--run-id", "run-1", "--out", path])
            self.assertIn("RUN_EXISTS", str(context.exception))


class AudioAlignmentTests(unittest.TestCase):
    def test_duration_alignment_marks_source_and_scales_timeline(self):
        derived = {"total_seconds": 10.0, "lines": [{"text": "a", "start": 0, "end": 5}],
                   "motion_plan": [{"start": 0, "end": 10}], "srt": ""}
        with mock.patch.object(script_splitter, "_probe_duration", return_value=12.0):
            result = script_splitter.align_captions_to_audio(derived, "/tmp/video.mp4")
        self.assertEqual(result["total_seconds"], 12.0)
        self.assertEqual(result["lines"][0]["end"], 6.0)
        self.assertIn("audio_duration_aligned", result["timeline_source"])
        self.assertTrue(result["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
