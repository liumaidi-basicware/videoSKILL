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

    def test_product_usage_prompt_reads_top_level_product_name_and_identity_lock(self):
        prompt = storyboard.product_usage_prompt({
            "characters": [{"id": "host"}],
            "product_name": "1-Vibe Go Lite 马卡龙磁吸无线音箱",
            "product_color": "yellow",
            "shots": [{"character_action": "host magnetically attaches the speaker to the phone back"}],
        })
        self.assertIn("1-Vibe Go Lite 马卡龙磁吸无线音箱", prompt)
        self.assertIn("Product identity lock", prompt)
        self.assertIn("confirmed product board is the highest-priority", prompt)
        self.assertIn("Preserve genuine logos", prompt)
        self.assertIn("non-product text overlays", prompt)
        self.assertNotIn("Luna", prompt)
        self.assertNotIn("generated text, logo, watermark", prompt)

    def test_magnetic_speaker_usage_prompt_locks_bottom_to_phone_back_geometry(self):
        plan = {
            "characters": [{"id": "mina"}],
            "product_name": "1-Vibe Go Lite 马卡龙磁吸无线音箱",
            "shots": [{
                "id": "s2_magnetic_snap",
                "visual": "手将音响底部磁吸面贴到手机背面",
                "character_action": "Mina 手捏住产品边缘，将音响底部磁吸面贴向手机背面",
                "props": "speaker and smartphone back",
            }],
        }
        prompt = storyboard.product_usage_prompt(plan)
        self.assertEqual(
            storyboard.product_usage_geometry_contract(plan),
            "bottom_surface_magnetic_attach_to_receiver_back")
        for term in (
                "PRODUCT-USE PHYSICAL RELATION CONTRACT",
                "MANDATORY SURFACE ATTACHMENT GEOMETRY",
                "bottom/base magnetic surface",
                "ONLY surface allowed to touch",
                "receiver back plane",
                "product body protrudes outward",
                "non-contact visible/operable product surface",
                "side/edge view after attachment",
                "At least seven of nine panels",
                "Do not replace this with handheld posing",
                "Never show the active product standing",
                "禁止画成站在",
                "Forbidden mistakes",
                "通用表面连接合同"):
            self.assertIn(term, prompt)

    def test_product_usage_prompt_accepts_reviewed_composition_skill_brief(self):
        plan = {
            "characters": [{"id": "mina"}],
            "product_name": "1-Vibe Go Lite 马卡龙磁吸无线音箱",
            "_asset_composition_briefs": {
                "product_usage_image": {
                    "composition_strategy": "用侧边接触线证明底部磁吸到手机背面",
                    "primary_subject_scope": "产品和手机背面为主语，人物只保留裁切手部",
                    "panel_plan": [
                        {"shot_size": "macro",
                         "composition": "产品底部对准手机背面",
                         "must_show": "bottom/base magnetic surface touches smartphone back plane",
                         "proof_goal": "contact-line proof",
                         "forbidden": "generic handheld portrait"}
                        for _ in range(9)
                    ],
                    "must_exclude": ["phone front app", "standalone presenter lifestyle scene"],
                }
            },
            "shots": [{
                "id": "s2_magnetic_snap",
                "visual": "手将音响底部磁吸面贴到手机背面",
                "character_action": "Mina 手捏住产品边缘，将音响底部磁吸面贴向手机背面",
                "props": "speaker and smartphone back",
            }],
        }
        prompt = storyboard.product_usage_prompt(plan)
        self.assertIn("MODEL-GENERATED COMPOSITION BRIEF", prompt)
        self.assertIn("用侧边接触线证明底部磁吸到手机背面", prompt)
        self.assertIn("generic handheld portrait", prompt)

    def test_usage_outcome_context_includes_brief_features_and_script_results(self):
        plan = {
            "product_facts": {
                "product_name": "1-Vibe Go Lite",
                "features": ["IPX4防水，户外不怕", "TWS串联技术，两台配对沉浸式立体声"],
            },
            "shots": [
                {
                    "id": "s2",
                    "visual": "手将产品底部磁吸面贴到手机背面",
                    "character_action": "贴合手机背面",
                    "props": "product and smartphone back",
                },
                {
                    "id": "s3",
                    "visual": "产品支撑手机横放在桌面形成观看角度",
                    "character_action": "手指轻放手机确认稳定，随后移开",
                    "props": "phone landscape on desktop, product as stand",
                },
            ],
        }
        context = storyboard.product_usage_outcome_context(
            plan, storyboard._usage_action_shot(plan))
        text = "\n".join(context)
        self.assertIn("IPX4防水", text)
        self.assertIn("TWS串联", text)
        self.assertIn("手机横放在桌面形成观看角度", text)

    def test_structured_use_relation_generalizes_beyond_speaker(self):
        plan = {
            "product_name": "FoldGo magnetic stand",
            "shots": [{
                "id": "s1",
                "visual": "用户把支架底座贴到平板背面并展开支撑",
                "use_relation": {
                    "relation_type": "magnetic_mount_and_stand",
                    "active_object": "FoldGo stand",
                    "receiver_object": "tablet",
                    "product_contact_surface": "flat magnetic base",
                    "receiver_contact_surface": "tablet back",
                    "outward_surface": "folding hinge and stand arm",
                    "final_state": "base flush on tablet back, hinge visible and stand arm unfolded",
                    "forbidden": ["hinge face glued to tablet", "stand becomes a flat sticker"],
                },
            }],
        }
        prompt = storyboard.product_usage_prompt(plan)
        self.assertIn("PRODUCT-USE PHYSICAL RELATION CONTRACT", prompt)
        self.assertIn("flat magnetic base", prompt)
        self.assertIn("tablet back", prompt)
        self.assertIn("folding hinge and stand arm", prompt)
        self.assertIn("hinge face glued to tablet", prompt)

    def test_product_usage_reference_order_keeps_product_board_first(self):
        refs = storyboard._usage_reference_urls(
            product_refs=["product-board"],
            cast_refs=["cast-board"],
            pose_refs=["pose-guide"],
            limit=3,
        )
        self.assertEqual(refs, ["product-board", "cast-board", "pose-guide"])

    def test_confirmed_product_identity_paths_include_board_and_single_product_ref(self):
        with tempfile.TemporaryDirectory() as root:
            board = os.path.join(root, "product_board.jpg")
            hero = os.path.join(root, "hero.png")
            cast = os.path.join(root, "cast.jpg")
            for path in (board, hero, cast):
                with open(path, "wb") as handle:
                    handle.write(b"img")
            plan = {"asset_refs": {"product_images": [hero]}}
            results = {"product_board": {"path": board}, "cast_board": {"path": cast}}
            with mock.patch.object(storyboard.asset_prep, "is_product_asset_ready",
                                   return_value=True):
                paths = storyboard._confirmed_product_identity_paths(
                    plan, results, client="acme", limit=2)
        self.assertEqual(paths, [os.path.abspath(board), os.path.abspath(hero)])

    def test_reference_registry_includes_usage_and_used_ref_tags(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "usage.jpg")
            product = os.path.join(root, "product.jpg")
            host = os.path.join(root, "host.jpg")
            for path in (usage, product, host):
                with open(path, "wb") as handle:
                    handle.write(b"img")
            plan = {
                "asset_refs": {"product_usage_images": [usage]},
                "references": [
                    {"tag": "@product_hero", "url": product,
                     "type": "product_board", "label": "confirmed product board"},
                    {"tag": "@mina", "url": host,
                     "type": "character_board", "label": "Mina cast board"},
                ],
                "shots": [{"id": "s1", "ref_tags": ["@product_hero", "@mina"]}],
            }
            registry = storyboard.build_reference_registry(plan)
            storyboard._validate_reference_registry(plan, registry)
        self.assertEqual([item["tag"] for item in registry],
                         ["@usage", "@product_hero", "@mina"])
        self.assertTrue(all(os.path.isabs(item["url"]) for item in registry))
        self.assertEqual(registry[0]["type"], "product_usage_identity")

    def test_reference_registry_rejects_missing_shot_tag(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "usage.jpg")
            with open(usage, "wb") as handle:
                handle.write(b"img")
            plan = {
                "asset_refs": {"product_usage_images": [usage]},
                "shots": [{"id": "s1", "ref_tags": ["@missing"]}],
            }
            registry = storyboard.build_reference_registry(plan)
            with self.assertRaisesRegex(Exception, "REFERENCE_REGISTRY_MISSING_TAGS"):
                storyboard._validate_reference_registry(plan, registry)

    def test_contact_sheet_prompt_includes_reference_registry(self):
        prompt = storyboard.contact_sheet_prompt(
            {"shots": [{"id": "s1", "panel_plan": ["hero product close-up"],
                        "ref_tags": ["@usage", "@product_hero"]}]},
            reference_registry=[
                {"tag": "@usage", "url": "/tmp/usage.jpg",
                 "type": "product_usage_identity", "source": "confirmed usage board"},
                {"tag": "@product_hero", "url": "/tmp/product.jpg",
                 "type": "product_board", "source": "confirmed product board"},
            ],
        )
        self.assertIn("CONTACT SHEET REFERENCE REGISTRY", prompt)
        self.assertIn("@usage = confirmed usage board", prompt)
        self.assertIn("identity-locked visual anchor", prompt)
        self.assertIn("refs=@usage @product_hero", prompt)

    def test_shot_reference_registry_filters_unmentioned_character_refs(self):
        registry = [
            {"tag": "@usage", "url": "/tmp/usage.jpg", "type": "product_usage_identity"},
            {"tag": "@product_hero", "url": "/tmp/product.jpg", "type": "product_identity"},
            {"tag": "@mina", "url": "/tmp/mina.jpg", "type": "character_identity"},
        ]
        product_only = {
            "id": "s1",
            "visual": "hero product close-up on a clean desktop",
            "props": "compact magnetic wireless speaker",
            "ref_tags": ["@product_hero"],
            "characters": [],
        }
        self.assertEqual(
            [item["tag"] for item in storyboard.shot_reference_registry(registry, product_only)],
            ["@product_hero"])
        usage_shot = {
            "id": "s2",
            "visual": "hand magnetically attaches the speaker to the phone back",
            "ref_tags": ["@product_hero"],
            "characters": [],
        }
        self.assertEqual(
            [item["tag"] for item in storyboard.shot_reference_registry(registry, usage_shot)],
            ["@usage", "@product_hero"])
        mina_shot = {
            "id": "s3",
            "visual": "Mina presents product",
            "ref_tags": ["@mina", "@product_hero"],
            "characters": ["mina"],
        }
        self.assertEqual(
            [item["tag"] for item in storyboard.shot_reference_registry(registry, mina_shot)],
            ["@mina", "@product_hero"])

    def test_product_usage_cil_does_not_infer_from_presenter_or_earrings(self):
        plan = {
            "product_facts": {"product_name": "1-Vibe Go Lite"},
            "characters": [{"id": "mina", "costume": "business casual with simple earrings"}],
            "shots": [],
        }
        presenter_shot = {
            "id": "s5_mina_cta",
            "visual": "Mina presents product and recommends it to the audience",
            "character_action": "Mina holds the product beside her chest while speaking",
            "character_prompt": "same hairstyle, simple earrings, calm smile",
            "ref_tags": ["@mina", "@product_hero"],
            "characters": ["mina"],
        }
        prompt = storyboard.shot_prompt(plan, presenter_shot, 5)
        self.assertFalse(storyboard._shot_needs_usage_reference(presenter_shot))
        self.assertEqual(storyboard.product_usage_physical_relation(plan, presenter_shot), {})
        self.assertNotIn("PRODUCT-USE PHYSICAL RELATION CONTRACT", prompt)
        self.assertNotIn("outer ear / ear area", prompt)
        self.assertNotIn("CONFIRMED PRODUCT-IN-USE reference", prompt)

    def test_shot_prompt_uses_usage_board_only_for_actual_use_shots(self):
        plan = {
            "product_facts": {"product_name": "1-Vibe Go Lite"},
            "asset_refs": {
                "product_images": ["product_board.jpg"],
                "product_usage_images": ["usage_board.jpg"],
            },
            "shots": [],
        }
        product_only = {
            "id": "s1",
            "visual": "只展示产品静物和干净桌面",
            "props": "1-Vibe Go Lite speaker",
            "characters": [],
        }
        usage_shot = {
            "id": "s2",
            "visual": "手将产品底部磁吸面贴到手机背面",
            "character_action": "拇指和食指拿住产品边缘并贴合手机背面",
            "props": "1-Vibe Go Lite speaker and smartphone back",
            "characters": [],
        }

        product_prompt = storyboard.shot_prompt(plan, product_only, 1)
        usage_prompt = storyboard.shot_prompt(plan, usage_shot, 2)

        self.assertIn("product_board.jpg", product_prompt)
        self.assertNotIn("usage_board.jpg", product_prompt)
        self.assertNotIn("PRODUCT-IN-USE NINE-PANEL BOARD", product_prompt)
        self.assertIn("usage_board.jpg", usage_prompt)
        self.assertIn("CONFIRMED PRODUCT-IN-USE reference", usage_prompt)
        self.assertIn("PRODUCT-IN-USE NINE-PANEL BOARD", usage_prompt)

    def test_default_panel_plan_does_not_trigger_usage_reference(self):
        shot = {
            "id": "s1",
            "visual": "product-only clean desktop beauty shot",
            "character_action": "no person, product is the hero object",
            "panel_plan": list(storyboard.DEFAULT_PANEL_PLAN),
            "ref_tags": ["@product"],
        }
        registry = [
            {"tag": "@usage", "url": "/tmp/usage.jpg", "type": "product_usage_identity"},
            {"tag": "@product", "url": "/tmp/product.jpg", "type": "product_identity"},
        ]
        refs = storyboard.shot_reference_registry(registry, shot)
        self.assertEqual([item["tag"] for item in refs], ["@product"])

    def test_existing_shot_rejected_when_reference_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as root:
            image = os.path.join(root, "shot.jpg")
            with open(image, "wb") as handle:
                handle.write(b"img")
            shot = {"id": "s1", "visual": "product close-up"}
            original_refs = [{"tag": "@product_hero", "url": "/tmp/product.jpg",
                              "type": "product_identity"}]
            updated_refs = original_refs + [
                {"tag": "@mina", "url": "/tmp/mina.jpg", "type": "character_identity"}]
            existing = {
                "shot_fingerprint": storyboard.shot_fingerprint(shot),
                "reference_fingerprint": storyboard.reference_fingerprint(original_refs),
                "sha256": storyboard._file_sha256(image),
            }
            self.assertTrue(storyboard._existing_shot_matches_plan(
                existing, shot, "plan", image,
                expected_reference_fingerprint=storyboard.reference_fingerprint(original_refs)))
            self.assertFalse(storyboard._existing_shot_matches_plan(
                existing, shot, "plan", image,
                expected_reference_fingerprint=storyboard.reference_fingerprint(updated_refs)))

    def test_board_approval_expires_when_source_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = storyboard._approval_path(directory, "product")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"status": "confirmed", "source_fingerprint": "old"}, handle)
            self.assertTrue(storyboard._approval_current(directory, "product", "old"))
            self.assertFalse(storyboard._approval_current(directory, "product", "new"))
    def test_prompt_requires_16x9_twelve_panel_annotated_bw_preview(self):
        # Default shot (no panel_plan) falls back to the legacy 12-beat plan,
        # which should still render as a 4x3 grid with an explicit panel
        # count (2026-08-05: panel count is script-driven, no hardcoded
        # "TWELVE" literal any more — assert the dynamic "12" instead).
        prompt = storyboard.shot_prompt({"characters": []}, {"id": "s1"}, 1)
        for term in ("16:9", "12 movie-style panels", "4x3", "BLACK-AND-WHITE",
                     "RED arrows", "BLUE arrows", "GREEN marks", "ORANGE marks",
                     "PURPLE marks", "visible movement"):
            self.assertIn(term, prompt)

    def test_no_character_prompt_hard_lock_comes_after_approved_notes(self):
        prompt = storyboard.shot_prompt(
            {},
            {"id": "s1", "characters": [],
             "approved_prompt_zh": "年轻人桌面生活场景，可以有人坐在旁边"},
            1,
        )
        self.assertIn("NO-CHARACTER SHOT HARD LOCK", prompt)
        self.assertIn("Do NOT draw any human face", prompt)
        self.assertGreater(
            prompt.index("NO-CHARACTER SHOT HARD LOCK"),
            prompt.index("用户确认的中文导演提示词"))

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

    def test_derived_motion_plan_deduplicates_spoken_cta(self):
        derived = script_splitter.derive_captions({"segments": [{
            "id": "s7", "duration": 4,
            "dialogue": "Momax 1-Vibe Go Lite，小小一颗，玩味十足。",
            "motion_elements": [
                "CTA 后期动效：Momax 1-Vibe Go Lite，2.0s-3.4s",
                "品牌 Logo 后期右上角轻入",
            ],
        }]})
        self.assertEqual(derived["motion_plan"][0]["motion_elements"], [
            "品牌 Logo 后期右上角轻入",
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
