import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import prompt_review
import storyboard
import video_engine
import video_prompts


class PromptReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "scene_type": "oral-broadcast",
            "product_facts": {"product_name": "灰色产品", "product_color": "灰色"},
            "continuity_contract": {"scene_id": "studio-1"},
            "shots": [{"id": "s1", "dialogue": "第一段", "visual": "主持人介绍"},
                      {"id": "s2", "dialogue": "第二段", "visual": "展示产品"}],
        }

    def test_polish_is_pending_and_confirm_requires_explicit_transition(self):
        response = '{"prompt_zh":"详细中文提示词","negative_prompt_zh":"不要文字"}'
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat", return_value=response):
            review = prompt_review.polish(self.plan, "video", model="review-model")
        self.assertEqual(review["status"], "pending")
        self.assertEqual(len(review["prompts"]), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            prompt_review.save_pending(review, path)
            confirmed = prompt_review.confirm(path)
        self.assertEqual(confirmed["status"], "confirmed")

    def test_confirm_cli_accepts_file_alias(self):
        review = {"status": "pending", "stage": "storyboard",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            prompt_review.main(["confirm", "--file", path])
            with open(path, encoding="utf-8") as handle:
                confirmed = json.load(handle)
        self.assertEqual(confirmed["status"], "confirmed")

    def test_confirm_cli_prints_short_json_only(self):
        long_prompt = "完整提交提示词" * 500
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "prompts": [{"shot_id": "s1", "prompt_zh": long_prompt}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prompt_review.main(["confirm", "--review", path])
            printed = stdout.getvalue()
            payload = json.loads(printed)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "confirmed")
        self.assertEqual(payload["prompt_count"], 1)
        self.assertLess(len(printed), 500)
        self.assertNotIn(long_prompt, printed)

    def test_confirm_revalidates_composition_brief_before_approval(self):
        review = {
            "status": "pending",
            "stage": "storyboard",
            "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}],
            "asset_prompts": [{
                "asset_id": "product_usage_image",
                "geometry_contract": "bottom_surface_magnetic_attach_to_receiver_back",
                "physical_relation_contract": {
                    "relation_type": "magnetic_attach",
                    "active_object": "1-Vibe Go Lite",
                    "receiver_object": "smartphone",
                    "product_contact_surface": "bottom/base magnetic surface",
                    "receiver_contact_surface": "receiver back plane",
                    "final_state": "product bottom/base sits flush on the receiver back, product body protrudes outward",
                },
                "composition_brief": {
                    "composition_strategy": "invalid",
                    "primary_subject_scope": "product and phone",
                    "camera_scope": "side",
                    "range_limits": "none",
                    "panel_plan": [
                        {
                            "shot_size": "close-up",
                            "composition": "side rear contact-proof angle",
                            "must_show": "1-Vibe Go Lite smartphone bottom/base magnetic surface receiver back plane flush protrudes outward",
                            "proof_goal": "contact proof",
                            "forbidden": "",
                        }
                        for _ in range(9)
                    ],
                    "outcome_panels": ["8", "9"],
                    "must_include": ["bottom/base magnetic surface", "receiver back plane"],
                    "must_exclude": [],
                },
            }],
        }
        review["asset_prompts"][0]["composition_brief"]["panel_plan"][7]["composition"] = (
            "smartphone propped up by attached speaker on a desk")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            with self.assertRaisesRegex(ValueError, "ATTACHMENT_AMBIGUOUS"):
                prompt_review.confirm(path)
            with open(path, encoding="utf-8") as handle:
                still_pending = json.load(handle)
        self.assertEqual(still_pending["status"], "pending")

    def test_invalidate_pending_review_blocks_later_confirm(self):
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            invalidated = prompt_review.invalidate(path, reason="superseded by v14")
            with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_CONFIRM_BLOCKED"):
                prompt_review.confirm(path)
        self.assertEqual(invalidated["status"], "invalidated")
        self.assertEqual(invalidated["invalidation_reason"], "superseded by v14")

    def test_invalidate_cli_prints_short_json_only(self):
        long_prompt = "完整提交提示词" * 500
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "prompts": [{"shot_id": "s1", "prompt_zh": long_prompt}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prompt_review.main([
                    "invalidate", "--review", path,
                    "--reason", "failed preconfirm validation",
                ])
            printed = stdout.getvalue()
            payload = json.loads(printed)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "invalidated")
        self.assertLess(len(printed), 500)
        self.assertNotIn(long_prompt, printed)

    def test_preview_hides_confirm_command_for_invalidated_review(self):
        review = {"status": "invalidated", "stage": "storyboard", "model": "m",
                  "invalidation_reason": "superseded by v14",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            text = prompt_review.preview(path)
        self.assertIn("已作废", text)
        self.assertIn("superseded by v14", text)
        self.assertNotIn("prompt_review.py confirm", text)

    def test_preview_shows_confirm_command_only_for_pending_review(self):
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            text = prompt_review.preview(path)
        self.assertIn("prompt_review.py confirm", text)

    def test_preview_writes_markdown_without_confirming(self):
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "review_fingerprint": "abc123",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "正向提示",
                               "negative_prompt_zh": "不要文字",
                               "continuity_notes": ["保持一致"]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            out = os.path.join(directory, "preview.md")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            text = prompt_review.preview(path, out=out)
            with open(path, encoding="utf-8") as handle:
                still_pending = json.load(handle)
            with open(out, encoding="utf-8") as handle:
                written = handle.read()
        self.assertEqual(still_pending["status"], "pending")
        self.assertIn("提示词审核预览", text)
        self.assertIn("python3 scripts/prompt_review.py confirm --review", written)
        self.assertIn("正向提示", written)

    def test_preview_cli_with_out_prints_short_json_only(self):
        long_prompt = "正向提示" * 500
        review = {"status": "pending", "stage": "storyboard", "model": "m",
                  "review_fingerprint": "abc123",
                  "prompts": [{"shot_id": "s1", "prompt_zh": long_prompt,
                               "negative_prompt_zh": "不要文字"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            out = os.path.join(directory, "preview.md")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prompt_review.main(["preview", "--review", path, "--out", out])
            printed = stdout.getvalue()
            payload = json.loads(printed)
            with open(out, encoding="utf-8") as handle:
                written = handle.read()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["preview"], out)
        self.assertLess(len(printed), 300)
        self.assertNotIn(long_prompt, printed)
        self.assertIn(long_prompt[:700], written)

    def test_capture_storyboard_records_model_ready_submission_prompt(self):
        review = prompt_review.capture_storyboard_prompts(self.plan, model="gpt-image-2")
        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["stage"], "storyboard")
        self.assertEqual(review["target_model"], "gpt-image-2")
        self.assertEqual(len(review["prompts"]), 2)
        first = review["prompts"][0]
        self.assertIn("submission_prompt_zh", first)
        self.assertIn("STRICT BLACK-AND-WHITE", first["submission_prompt_zh"])
        self.assertIn("非付费捕获", first["prompt_zh"])

    def test_storyboard_director_skill_is_reviewed_and_injected(self):
        review = prompt_review.capture_storyboard_prompts(self.plan, model="gpt-image-2")
        brief = {
            "narrative_function": "开场建立产品卖点和可信场景",
            "scene_design": "干净桌面，产品和主持人形成前后层次",
            "shot_size": "medium close-up",
            "camera_movement": "轻微推近",
            "composition": "产品在右前三分线，人物在左后方",
            "lighting": "柔和商业棚拍光",
            "action_beats": ["产品入画", "主持人看向产品", "镜头推近"],
            "transition_in": "从纯产品开场承接",
            "transition_out": "交给下一段功能展示",
            "product_value_proof": "通过清晰产品外观和真实互动证明卖点",
            "emotional_intent": "轻松可信",
            "continuity_hooks": ["同一产品", "同一桌面", "同一灯光"],
            "reference_scope": "@product 控制产品，@mina 控制人物",
            "must_preserve": ["产品事实", "人物身份", "无画面文字"],
            "must_exclude": ["字幕", "水印", "新增角色"],
        }
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value=json.dumps(brief, ensure_ascii=False)):
            enriched = prompt_review.add_director_briefs(
                review, self.plan, model="qwen3.6-plus")
        first = enriched["prompts"][0]
        self.assertEqual(enriched["director_model"], "qwen3.6-plus")
        self.assertEqual(first["director_brief"]["narrative_function"],
                         "开场建立产品卖点和可信场景")
        self.assertIn("MODEL-GENERATED STORYBOARD DIRECTOR BRIEF",
                      first["submission_prompt_zh"])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(enriched, handle, ensure_ascii=False)
            preview = prompt_review.preview(path)
        self.assertIn("模型导演 brief", preview)
        self.assertIn("开场建立产品卖点", preview)

    def test_director_skill_augments_cil_surface_locks_before_validation(self):
        item = {
            "shot_id": "s2",
            "submission_prompt_zh": (
                "product contact surface bottom/base magnetic surface attaches to "
                "receiver back plane smartphone back"),
        }
        brief = {
            "narrative_function": "展示磁吸关系",
            "scene_design": "手部与手机背面互动",
            "shot_size": "close-up",
            "camera_movement": "push-in",
            "composition": "手机背面为视觉焦点",
            "lighting": "soft light",
            "action_beats": ["接近", "贴合", "定格"],
            "transition_in": "承接产品静物",
            "transition_out": "交给支架结果",
            "product_value_proof": "证明磁吸稳固",
            "emotional_intent": "轻松可信",
            "continuity_hooks": ["手机背面一致"],
            "reference_scope": "@product",
            "must_preserve": ["产品身份"],
            "must_exclude": ["字幕"],
        }
        augmented = prompt_review._augment_director_key_relations(item, brief)
        prompt_review._validate_director_brief(item, augmented, "storyboard")
        text = json.dumps(augmented, ensure_ascii=False)
        self.assertIn("bottom/base magnetic surface", text)
        self.assertIn("receiver back plane", text)

    def test_capture_storyboard_includes_asset_level_product_cil_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            product = os.path.join(directory, "product.png")
            with open(product, "wb") as handle:
                handle.write(b"img")
            plan = dict(self.plan)
            plan["asset_refs"] = {"product_images": [product]}
            plan["shots"] = [
                {"id": "s1", "dialogue": "第一段", "visual": "主持人介绍"},
                {
                    "id": "s2",
                    "dialogue": "第二段",
                    "visual": "手将产品底部磁吸面贴到手机背面",
                    "character_action": "拇指和食指拿住产品边缘并贴合手机背面",
                    "props": "product and smartphone back",
                },
            ]
            review = prompt_review.capture_storyboard_prompts(plan, model="gpt-image-2")
        asset_ids = [item["asset_id"] for item in review["asset_prompts"]]
        self.assertIn("product_board", asset_ids)
        self.assertIn("product_usage_image", asset_ids)
        text = "\n".join(item["submission_prompt_zh"] for item in review["asset_prompts"])
        self.assertIn("PRODUCT IDENTITY CONTRACT", text)
        self.assertIn("PRODUCT-USE PHYSICAL RELATION CONTRACT", text)

    def test_asset_composition_skill_is_scoped_and_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            product = os.path.join(directory, "product.png")
            with open(product, "wb") as handle:
                handle.write(b"img")
            plan = dict(self.plan)
            plan["asset_refs"] = {"product_images": [product]}
            plan["shots"] = [{
                "id": "s1",
                "visual": "手将产品底部磁吸面贴到手机背面",
                "character_action": "拇指和食指拿住产品边缘并贴合手机背面",
                "props": "product and smartphone back",
            }]
            review = prompt_review.capture_storyboard_prompts(plan, model="gpt-image-2")
        brief = {
            "composition_strategy": "用手部近景和侧边接触线证明磁吸关系",
            "primary_subject_scope": "active product 和 smartphone 是主语，人物只保留裁切手部",
            "camera_scope": "近景、宏观接触线、45度侧边",
            "range_limits": "不出现手机正面 app，不出现人物生活方式展示",
            "panel_plan": [
                {
                    "shot_size": "close-up",
                    "composition": "side rear three-quarter angle: active product left, smartphone right",
                    "must_show": "灰色产品 smartphone bottom/base magnetic surface receiver back plane protrudes outward",
                    "proof_goal": "contact-line proof",
                    "forbidden": "handheld posing",
                }
                for _ in range(9)
            ],
            "outcome_panels": ["panel 8 usage outcome", "panel 9 usage context"],
            "must_include": ["smartphone back plane", "bottom/base magnetic surface"],
            "must_exclude": ["phone front app", "generic lifestyle portrait"],
        }
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value=json.dumps(brief, ensure_ascii=False)):
            enriched = prompt_review.add_asset_composition_briefs(
                review, plan, model="qwen3.6-plus")
        usage = next(item for item in enriched["asset_prompts"]
                     if item["asset_id"] == "product_usage_image")
        self.assertEqual(usage["composition_model"], "qwen3.6-plus")
        self.assertEqual(len(usage["composition_brief"]["panel_plan"]), 9)
        text = prompt_review.preview_dict(enriched) if hasattr(prompt_review, "preview_dict") else json.dumps(enriched, ensure_ascii=False)
        self.assertIn("bottom/base magnetic surface", text)

    def test_asset_composition_skill_rejects_ambiguous_attachment_result_panels(self):
        asset_item = {
            "asset_id": "product_usage_image",
            "geometry_contract": "bottom_surface_magnetic_attach_to_receiver_back",
            "physical_relation_contract": {
                "relation_type": "magnetic_attach",
                "active_object": "1-Vibe Go Lite",
                "receiver_object": "smartphone",
                "product_contact_surface": "bottom/base magnetic surface",
                "receiver_contact_surface": "receiver back plane",
                "outward_surface": "non-contact visible/operable product surface",
                "final_state": "product bottom/base sits flush on the receiver back, product body protrudes outward",
            },
        }
        ambiguous = {
            "composition_strategy": "show product attached",
            "primary_subject_scope": "product and phone",
            "camera_scope": "close-up",
            "range_limits": "none",
            "panel_plan": [
                {
                    "shot_size": "close-up",
                    "composition": "attached product in wider context",
                    "must_show": "product on phone",
                    "proof_goal": "attached",
                    "forbidden": "",
                }
                for _ in range(9)
            ],
            "must_include": [
                "1-Vibe Go Lite", "smartphone",
                "bottom/base magnetic surface", "receiver back plane"],
            "must_exclude": [],
        }
        with self.assertRaisesRegex(ValueError, "ATTACHMENT_PANEL_SCOPE_DRIFT"):
            prompt_review._validate_asset_composition_brief(asset_item, ambiguous)
        valid = dict(ambiguous)
        valid["panel_plan"] = [
            {
                "shot_size": "close-up",
                "composition": (
                    "side rear three-quarter angle: product contact surface bottom/base magnetic surface is flush to "
                    "receiver contact surface receiver back plane; product body protrudes outward"),
                "must_show": (
                    "bottom/base magnetic surface, receiver back plane, flush, protrudes outward"),
                "proof_goal": "prove phone stand outcome while contact surfaces remain correct",
                "forbidden": "ambiguous standing on geometry",
            }
            for _ in range(9)
        ]
        valid["outcome_panels"] = ["panel 8 stand outcome", "panel 9 viewing outcome"]
        prompt_review._validate_asset_composition_brief(asset_item, valid)
        invalid_positive = dict(valid)
        invalid_positive["panel_plan"] = list(valid["panel_plan"])
        invalid_positive["panel_plan"][7] = dict(invalid_positive["panel_plan"][7])
        invalid_positive["panel_plan"][7]["composition"] = (
            "product standing on smartphone edge while acting as stand")
        with self.assertRaisesRegex(ValueError, "ATTACHMENT_AMBIGUOUS"):
            prompt_review._validate_asset_composition_brief(asset_item, invalid_positive)
        front_outcome = dict(valid)
        front_outcome["panel_plan"] = list(valid["panel_plan"])
        front_outcome["panel_plan"][8] = dict(front_outcome["panel_plan"][8])
        front_outcome["panel_plan"][8]["composition"] = (
            "front screen view showing the phone playing video while product is attached")
        with self.assertRaisesRegex(ValueError, "OUTCOME_ANGLE_AMBIGUOUS"):
            prompt_review._validate_asset_composition_brief(asset_item, front_outcome)
        leaning_outcome = dict(valid)
        leaning_outcome["panel_plan"] = list(valid["panel_plan"])
        leaning_outcome["panel_plan"][7] = dict(leaning_outcome["panel_plan"][7])
        leaning_outcome["panel_plan"][7]["composition"] = (
            "side rear angle where phone rests against speaker while acting as stand")
        with self.assertRaisesRegex(ValueError, "ATTACHMENT_AMBIGUOUS"):
            prompt_review._validate_asset_composition_brief(asset_item, leaning_outcome)
        rear_context = dict(valid)
        rear_context["panel_plan"] = list(valid["panel_plan"])
        rear_context["panel_plan"][8] = dict(rear_context["panel_plan"][8])
        rear_context["panel_plan"][8]["composition"] = (
            "rear three-quarter wider context: product contact surface bottom/base magnetic surface "
            "is flush to receiver contact surface receiver back plane; product body protrudes outward")
        prompt_review._validate_asset_composition_brief(asset_item, rear_context)
        propped_outcome = dict(valid)
        propped_outcome["panel_plan"] = list(valid["panel_plan"])
        propped_outcome["panel_plan"][7] = dict(propped_outcome["panel_plan"][7])
        propped_outcome["panel_plan"][7]["composition"] = (
            "side rear angle: smartphone propped up by attached speaker on a desk")
        with self.assertRaisesRegex(ValueError, "ATTACHMENT_AMBIGUOUS"):
            prompt_review._validate_asset_composition_brief(asset_item, propped_outcome)
        missing_rear_angle = dict(valid)
        missing_rear_angle["panel_plan"] = list(valid["panel_plan"])
        missing_rear_angle["panel_plan"][8] = dict(missing_rear_angle["panel_plan"][8])
        missing_rear_angle["panel_plan"][8]["composition"] = (
            "phone propped at viewing angle with contact surfaces visible")
        with self.assertRaisesRegex(ValueError, "OUTCOME_ANGLE_MISSING"):
            prompt_review._validate_asset_composition_brief(asset_item, missing_rear_angle)
        forbidden_only = dict(valid)
        forbidden_only["panel_plan"] = [
            {
                "shot_size": "close-up",
                "composition": "attached product in use context",
                "must_show": "stable product and phone",
                "proof_goal": "show outcome",
                "forbidden": (
                    "must not hide bottom/base magnetic surface, receiver back plane, "
                    "flush, protrudes outward"),
            }
            for _ in range(9)
        ]
        forbidden_only["outcome_panels"] = ["panel 8 stand outcome", "panel 9 viewing outcome"]
        with self.assertRaisesRegex(ValueError, "ATTACHMENT_PANEL_SCOPE_DRIFT"):
            prompt_review._validate_asset_composition_brief(asset_item, forbidden_only)

    def test_asset_composition_skill_fails_after_invalid_retry_responses(self):
        asset_item = {
            "asset_id": "product_usage_image",
            "submission_prompt_zh": "base",
            "geometry_contract": "bottom_surface_magnetic_attach_to_receiver_back",
            "physical_relation_contract": {
                "relation_type": "magnetic_attach",
                "active_object": "1-Vibe Go Lite",
                "receiver_object": "smartphone",
                "product_contact_surface": "bottom/base magnetic surface",
                "receiver_contact_surface": "receiver back plane",
                "final_state": "product bottom/base sits flush on the receiver back, product body protrudes outward",
            },
        }
        review = {"status": "pending", "stage": "storyboard",
                  "asset_prompts": [asset_item], "prompts": []}
        invalid = {
            "composition_strategy": "generic",
            "primary_subject_scope": "product phone",
            "camera_scope": "wide",
            "range_limits": "none",
            "panel_plan": [
                {"shot_size": "wide", "composition": "phone on product",
                 "must_show": "product phone", "proof_goal": "generic", "forbidden": ""}
                for _ in range(9)
            ],
            "outcome_panels": ["8", "9"],
            "must_include": ["1-Vibe Go Lite", "smartphone"],
            "must_exclude": [],
        }
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value=json.dumps(invalid, ensure_ascii=False)):
            with self.assertRaisesRegex(ValueError, "ASSET_COMPOSITION_SKILL_FAILED"):
                prompt_review.add_asset_composition_briefs(
                    review, self.plan, model="qwen3.6-plus")

    def test_storyboard_generation_requires_current_asset_prompt_review(self):
        with tempfile.TemporaryDirectory() as directory:
            product = os.path.join(directory, "product.png")
            with open(product, "wb") as handle:
                handle.write(b"img")
            plan = dict(self.plan)
            plan["asset_refs"] = {"product_images": [product]}
            review = prompt_review.capture_storyboard_prompts(plan, model="gpt-image-2")
            review["status"] = "confirmed"
            review["asset_prompts"][0]["prompt_fingerprint"] = "stale"
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            canonical = storyboard.expand_product_sku_refs(
                storyboard.canonical_storyboard_plan(plan))
            with self.assertRaisesRegex(Exception, "资产级提示词"):
                storyboard._load_prompt_review_for_shots(path, canonical)

    def test_capture_storyboard_cli_with_preview_out_prints_short_json_only(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = os.path.join(directory, "plan.json")
            review_path = os.path.join(directory, "storyboard_review.json")
            preview_path = os.path.join(directory, "storyboard_review.md")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump(self.plan, handle, ensure_ascii=False)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prompt_review.main([
                    "capture-storyboard",
                    "--plan", plan_path,
                    "--out", review_path,
                    "--preview-out", preview_path,
                ])
            printed = stdout.getvalue()
            payload = json.loads(printed)
            with open(review_path, encoding="utf-8") as handle:
                review = json.load(handle)
            with open(preview_path, encoding="utf-8") as handle:
                preview_text = handle.read()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["review"], os.path.abspath(review_path))
        self.assertEqual(payload["preview"], os.path.abspath(preview_path))
        self.assertEqual(payload["prompt_count"], 2)
        self.assertEqual(review["status"], "pending")
        self.assertIn("提示词审核预览", preview_text)
        self.assertIn("STRICT BLACK-AND-WHITE", preview_text)
        self.assertLess(len(printed), 500)
        self.assertNotIn("STRICT BLACK-AND-WHITE", printed)

    def test_storyboard_gate_injects_confirmed_submission_prompt(self):
        review = {"status": "confirmed", "stage": "storyboard",
                  "visual_plan_fingerprint": storyboard.visual_plan_fingerprint(self.plan),
                  "asset_prompts": storyboard.asset_prompt_review_items(self.plan),
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1",
                               "submission_prompt_zh": "FULL-S1"},
                              {"shot_id": "s2", "prompt_zh": "P2",
                               "submission_prompt_zh": "FULL-S2"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            plan = json.loads(json.dumps(self.plan))
            storyboard._load_prompt_review_for_shots(path, plan)
        self.assertEqual(plan["shots"][0]["approved_prompt_zh"], "P1")
        self.assertEqual(plan["shots"][0]["approved_submission_prompt_zh"], "FULL-S1")
        self.assertEqual(
            storyboard.contact_sheet_prompt({"shots": [plan["shots"][0]]}),
            "FULL-S1")

    def test_video_gate_injects_only_confirmed_prompts(self):
        review = {"status": "confirmed", "stage": "video",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1",
                               "submission_prompt_zh": "S1", "model": "seedance-2.0",
                               "model_submission_prompts": {
                                   "seedance-2.0": "S1",
                                   "kling-v3-omni-video": "K1"}},
                              {"shot_id": "s2", "prompt_zh": "P2",
                               "submission_prompt_zh": "S2", "model": "seedance-2.0"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(review, handle, ensure_ascii=False)
            path = handle.name
        try:
            segments = [dict(shot) for shot in self.plan["shots"]]
            video_engine._require_confirmed_prompt_review(path, "video", segments)
            self.assertEqual([s["approved_prompt_zh"] for s in segments], ["P1", "P2"])
            self.assertEqual([s["approved_submission_prompt_zh"] for s in segments], ["S1", "S2"])
            self.assertEqual([s["approved_prompt_model"] for s in segments],
                             ["seedance-2.0", "seedance-2.0"])
            self.assertEqual(
                segments[0]["approved_submission_prompts_by_model"]["kling-v3-omni-video"],
                "K1")
        finally:
            os.remove(path)

    def test_submission_text_uses_confirmed_full_submission_prompt(self):
        segment = {
            "id": "s1",
            "text": "base prompt should not be used",
            "storyboard_ref": True,
            "storyboard_ref_mode": "native_storyboard",
            "approved_prompt_zh": "approved base",
            "approved_submission_prompt_zh": "APPROVED FULL SUBMISSION",
            "approved_prompt_model": "seedance-2.0",
        }
        self.assertEqual(
            video_engine._submission_text(segment, "seedance-2.0", storyboard_ref=True),
            "APPROVED FULL SUBMISSION")

    def test_submission_text_blocks_unconfirmed_fallback_model(self):
        segment = {
            "id": "s1",
            "text": "黄色产品贴到手机背面",
            "storyboard_ref": True,
            "storyboard_ref_mode": "expanded_panel",
            "approved_prompt_zh": "approved base",
            "approved_submission_prompt_zh": "Seedance-only full prompt",
            "approved_prompt_model": "seedance-2.0",
        }
        with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_REQUIRED_FOR_MODEL"):
            video_engine._submission_text(
                segment, "kling-v3-omni-video", storyboard_ref=True)

    def test_submission_text_uses_confirmed_fallback_model_prompt(self):
        segment = {
            "id": "s1",
            "text": "黄色产品贴到手机背面",
            "storyboard_ref": True,
            "storyboard_ref_mode": "expanded_panel",
            "approved_prompt_zh": "approved base",
            "approved_submission_prompt_zh": "Seedance full prompt",
            "approved_prompt_model": "seedance-2.0",
            "approved_submission_prompts_by_model": {
                "seedance": "Seedance family full prompt",
                "kling": "Kling family full prompt",
            },
        }
        self.assertEqual(
            video_engine._submission_text(
                segment, "kling-v3-omni-video", storyboard_ref=True),
            "Kling family full prompt")

    def test_capture_video_segments_includes_kling_fallback_prompt(self):
        shot = dict(self.plan["shots"][0], storyboard_ref=True,
                    storyboard_ref_mode="native_storyboard",
                    storyboard_panel_index=1,
                    urls=["/tmp/product.png", "/tmp/storyboard.jpg"],
                    references=[
                        {"tag": "@product", "type": "product_identity",
                         "url": "/tmp/product.png", "label": "产品图",
                         "role": "锁定产品", "intent": "保持产品造型"},
                        {"tag": "@storyboard", "type": "storyboard_composition",
                         "url": "/tmp/storyboard.jpg", "label": "分镜图",
                         "role": "锁定构图", "intent": "保持机位动作"},
                    ],
                    audio_contract={
                        "track": "required",
                        "dialogue": "第一段",
                        "voice_continuity": "保持同一女声",
                        "bgm_continuity": "后期统一混音",
                        "sfx_continuity": "音效贴动作",
                        "voice_continuity_method": "text_contract_and_human_qc",
                        "bgm_continuity_method": "post_mix_preferred",
                        "media_reference_method": "basicrouter_video_v1_has_no_public_audio_reference_field",
                    })
        review = prompt_review.capture_video_segments(
            self.plan, [shot],
            model="seedance-2.0")
        item = review["prompts"][0]
        self.assertIn("seedance", item["model_submission_prompts"])
        self.assertIn("kling", item["model_submission_prompts"])
        self.assertTrue(item["fallback_submission_prompts"])
        self.assertIn("Seedance-native storyboard guidance",
                      item["model_submission_prompts"]["seedance"])
        self.assertIn("SINGLE 16:9 reference plate",
                      item["model_submission_prompts"]["kling"])
        self.assertIn("post_mix_preferred", item["model_submission_prompts"]["seedance"])
        self.assertIn("basicrouter_video_v1_has_no_public_audio_reference_field",
                      item["model_submission_prompts"]["kling"])
        self.assertEqual(["@product", "@storyboard"],
                         [ref["tag"] for ref in item["submission_references"]])

    def test_video_director_skill_is_injected_into_seedance_and_kling_prompts(self):
        shot = dict(self.plan["shots"][0], storyboard_ref=True,
                    storyboard_ref_mode="native_storyboard",
                    audio_contract={
                        "track": "required",
                        "dialogue": "第一段",
                        "voice_continuity": "保持同一女声",
                        "bgm_continuity": "后期统一混音",
                        "sfx_continuity": "动作点轻音效",
                    })
        review = prompt_review.capture_video_segments(self.plan, [shot], model="seedance-2.0")
        brief = {
            "narrative_function": "把产品卖点从静态图推进为可感知使用体验",
            "start_state": "产品和人物保持上一镜同一桌面方位",
            "timeline_beats": ["0-1秒承接姿态", "1-3秒展示动作", "3-5秒停在结果"],
            "end_state": "产品清晰稳定地停留，方便切下一段",
            "dialogue_delivery": "中文普通话，自然轻快，关键词略停顿，口型同步",
            "camera_motion": "轻微推进后稳定",
            "action_continuity": "动作方向与上一段一致，不重演前段",
            "audio_continuity": {
                "voice": "同一中文普通话年轻女声",
                "bgm": "同一轻快电子 BGM，后期统一混音优先",
                "sfx": "动作点轻音效",
                "method": "模型提示词约束声音人设，BGM 以 post_mix_preferred 保连续",
            },
            "edit_continuity": "结尾保留 0.3 秒稳定状态作为剪辑点",
            "model_strategy": {
                "seedance": "使用原生故事板理解完整镜头顺序",
                "kling": "只使用单格展开图和素材图逐段展开",
            },
            "reference_priority": "storyboard 控制构图，product 控制产品身份",
            "must_preserve": ["产品身份", "人物身份", "BGM 氛围"],
            "must_exclude": ["字幕", "水印", "新增角色"],
        }
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value=json.dumps(brief, ensure_ascii=False)):
            enriched = prompt_review.add_director_briefs(
                review, self.plan, model="qwen3.6-plus")
        item = enriched["prompts"][0]
        self.assertIn("MODEL-GENERATED VIDEO DIRECTOR BRIEF",
                      item["model_submission_prompts"]["seedance"])
        self.assertIn("MODEL-GENERATED VIDEO DIRECTOR BRIEF",
                      item["model_submission_prompts"]["kling"])
        self.assertIn("后期统一混音", item["model_submission_prompts"]["seedance"])
        gate = {"status": "confirmed", "stage": "video",
                "prompts": enriched["prompts"]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(gate, handle, ensure_ascii=False)
            path = handle.name
        try:
            segments = [{"id": "s1", "storyboard_ref": True,
                         "storyboard_ref_mode": "native_storyboard"}]
            video_engine._require_confirmed_prompt_review(path, "video", segments)
            self.assertEqual(segments[0]["director_brief"]["edit_continuity"],
                             "结尾保留 0.3 秒稳定状态作为剪辑点")
            self.assertIn("后期统一混音",
                          video_engine._submission_text(
                              segments[0], "seedance-2.0", storyboard_ref=True))
        finally:
            os.remove(path)

    def test_director_skill_fails_after_invalid_retry_responses(self):
        review = prompt_review.capture_video_segments(
            self.plan, [dict(self.plan["shots"][0])], model="seedance-2.0")
        invalid = {
            "narrative_function": "泛化",
            "timeline_beats": ["只有一拍"],
        }
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value=json.dumps(invalid, ensure_ascii=False)):
            with self.assertRaisesRegex(ValueError, "DIRECTOR_BRIEF_SKILL_FAILED"):
                prompt_review.add_director_briefs(
                    review, self.plan, model="qwen3.6-plus")

    def test_video_preview_lists_submission_image_references(self):
        review = {
            "status": "pending",
            "stage": "video",
            "model": "deterministic-segment-compiler",
            "prompts": [{
                "shot_id": "s1",
                "model": "seedance-2.0",
                "submission_prompt_zh": "Prompt text",
                "storyboard_ref_mode": "native_storyboard",
                "storyboard_panel_index": 2,
                "submission_references": [
                    {"index": 1, "tag": "@product", "type": "product_identity",
                     "label": "产品图", "role": "锁定产品",
                     "intent": "保持产品造型", "url": "/tmp/product.png"},
                    {"index": 2, "tag": "@storyboard", "type": "storyboard_composition",
                     "label": "分镜图", "role": "锁定构图",
                     "intent": "保持机位动作", "url": "/tmp/storyboard.jpg"},
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(review, handle, ensure_ascii=False)
            text = prompt_review.preview(path)
        self.assertIn("提交图片 / imageUrls", text)
        self.assertIn("storyboard_ref_mode: `native_storyboard`, panel_index: `2`", text)
        self.assertIn("@product", text)
        self.assertIn("/tmp/product.png", text)
        self.assertIn("保持机位动作", text)

    def test_capture_video_cli_with_preview_out_prints_short_json_only(self):
        shot = dict(self.plan["shots"][0], storyboard_ref=True,
                    storyboard_ref_mode="native_storyboard",
                    audio_contract={
                        "track": "required",
                        "dialogue": "第一段",
                        "voice_continuity": "保持同一女声",
                        "bgm_continuity": "后期统一混音",
                        "sfx_continuity": "音效贴动作",
                        "voice_continuity_method": "text_contract_and_human_qc",
                        "bgm_continuity_method": "post_mix_preferred",
                        "media_reference_method": "basicrouter_video_v1_has_no_public_audio_reference_field",
                    })
        with tempfile.TemporaryDirectory() as directory:
            plan_path = os.path.join(directory, "plan.json")
            segments_path = os.path.join(directory, "segments.json")
            review_path = os.path.join(directory, "video_review.json")
            preview_path = os.path.join(directory, "video_review.md")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump(self.plan, handle, ensure_ascii=False)
            with open(segments_path, "w", encoding="utf-8") as handle:
                json.dump({"segments": [shot]}, handle, ensure_ascii=False)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prompt_review.main([
                    "capture-video",
                    "--plan", plan_path,
                    "--segments", segments_path,
                    "--out", review_path,
                    "--preview-out", preview_path,
                ])
            printed = stdout.getvalue()
            payload = json.loads(printed)
            with open(preview_path, encoding="utf-8") as handle:
                preview_text = handle.read()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["prompt_count"], 1)
        self.assertIn("提示词审核预览", preview_text)
        self.assertIn("Seedance 原生故事板转视频规则", preview_text)
        self.assertLess(len(printed), 500)
        self.assertNotIn("Seedance 原生故事板转视频规则", printed)

    def test_shared_video_prompts_gate_matches_engine_submission_contract(self):
        review = {"status": "confirmed", "stage": "video",
                  "prompts": [{"shot_id": "s1", "prompt_zh": "P1",
                               "submission_prompt_zh": "S1", "model": "seedance-2.0",
                               "model_submission_prompts": {
                                   "seedance": "S1",
                                   "kling": "K1"}}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(review, handle, ensure_ascii=False)
            path = handle.name
        try:
            segments = [{"id": "s1", "storyboard_ref": True,
                         "storyboard_ref_mode": "native_storyboard"}]
            video_prompts._require_confirmed_prompt_review(path, "video", segments)
            self.assertEqual(segments[0]["approved_prompt_zh"], "P1")
            self.assertEqual(segments[0]["approved_submission_prompt_zh"], "S1")
            self.assertEqual(
                video_prompts._submission_text(
                    segments[0], "seedance-2.0", storyboard_ref=True),
                "S1")
            self.assertEqual(
                video_prompts._submission_text(
                    segments[0], "kling-v3-omni-video", storyboard_ref=True),
                "K1")
        finally:
            os.remove(path)

    def test_shared_video_prompts_wraps_approved_base_with_storyboard_rules(self):
        segment = {
            "id": "s1",
            "approved_prompt_zh": "approved base",
            "storyboard_ref": True,
            "storyboard_ref_mode": "native_storyboard",
        }
        prompt = video_prompts._submission_text(
            segment, "seedance-2.0", storyboard_ref=True)
        self.assertIn("Seedance 原生故事板转视频规则", prompt)
        self.assertIn("approved base", prompt)

    def test_video_prompt_filters_character_continuity_for_no_character_shot(self):
        plan = {
            "product_name": "1-Vibe Go Lite",
            "continuity": {
                "background": "same bright desktop",
                "character_identity": "Mina same face and hair",
                "wardrobe": "white hoodie",
                "voice": "Mandarin young female voice",
            },
            "shots": [],
        }
        no_human = {
            "id": "s1",
            "characters": [],
            "dialogue": "产品开场",
            "visual": "product hero close-up",
        }
        prompt = prompt_review._base_video_prompt(plan, no_human)
        self.assertIn("same bright desktop", prompt)
        self.assertIn("Mandarin young female voice", prompt)
        self.assertIn("本镜头没有人物出镜", prompt)
        self.assertIn("只允许裁切手部", prompt)
        self.assertNotIn("Mina same face", prompt)
        self.assertNotIn("white hoodie", prompt)

    def test_video_prompt_keeps_character_continuity_for_character_shot(self):
        plan = {
            "continuity": {
                "character_identity": "Mina same face and hair",
                "wardrobe": "white hoodie",
            },
        }
        with_human = {"id": "s5", "characters": ["mina"], "visual": "Mina presents"}
        prompt = prompt_review._base_video_prompt(plan, with_human)
        self.assertIn("Mina same face and hair", prompt)
        self.assertIn("white hoodie", prompt)
        self.assertIn("人物脸、发型、服装必须引用", prompt)

    def test_storyboard_gate_rejects_missing_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.json")
            with self.assertRaises(Exception) as error:
                storyboard._load_prompt_review_for_shots(path, self.plan)
        self.assertIn("PROMPT_REVIEW", str(error.exception))

    def _write_confirmed_storyboard_review(self, directory, plan,
                                           include_visual=True):
        review = {
            "status": "confirmed",
            "stage": "storyboard",
            "plan_fingerprint": storyboard.plan_fingerprint(plan),
            "asset_prompts": storyboard.asset_prompt_review_items(plan),
            "prompts": [{"shot_id": str(s.get("id")), "prompt_zh": "P-%s" % s.get("id")}
                        for s in plan.get("shots", [])],
        }
        if include_visual:
            review["visual_plan_fingerprint"] = storyboard.visual_plan_fingerprint(plan)
        path = os.path.join(directory, "review.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(review, handle, ensure_ascii=False)
        return path

    def test_dialogue_only_edit_keeps_storyboard_review_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_confirmed_storyboard_review(directory, self.plan)
            edited = json.loads(json.dumps(self.plan))
            edited["shots"][0]["dialogue"] = "改过的台词内容"
            storyboard._load_prompt_review_for_shots(path, edited)
            self.assertEqual(edited["shots"][0]["approved_prompt_zh"], "P-s1")

    def test_storyboard_review_injects_runtime_panel_and_global_references(self):
        plan = {
            "references": [
                {"tag": "@host", "url": "host.png", "type": "character_identity",
                 "scope": "global"},
                {"tag": "@product", "url": "product.png", "type": "product_identity",
                 "scope": "global"},
            ],
            "shots": [{"id": "s1", "visual": "产品特写", "ref_tags": ["@product"]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_confirmed_storyboard_review(directory, plan)
            storyboard._load_prompt_review_for_shots(path, plan)
        shot = plan["shots"][0]
        self.assertEqual(len(shot["panel_plan"]), 12)
        self.assertEqual([ref["tag"] for ref in shot["references"]], ["@product"])

    def test_visual_edit_forces_repolish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_confirmed_storyboard_review(directory, self.plan)
            edited = json.loads(json.dumps(self.plan))
            edited["shots"][0]["visual"] = "完全不同的画面描述"
            with self.assertRaisesRegex(Exception, "PROMPT_REVIEW_REQUIRED"):
                storyboard._load_prompt_review_for_shots(path, edited)

    def test_legacy_review_without_visual_fp_falls_back_to_full_fp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_confirmed_storyboard_review(
                directory, self.plan, include_visual=False)
            edited = json.loads(json.dumps(self.plan))
            edited["shots"][0]["dialogue"] = "改过的台词内容"
            with self.assertRaisesRegex(Exception, "PROMPT_REVIEW_REQUIRED"):
                storyboard._load_prompt_review_for_shots(path, edited)

    def test_polish_records_visual_plan_fingerprint(self):
        response = '{"prompt_zh":"提示词","negative_prompt_zh":"不要文字"}'
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat", return_value=response):
            review = prompt_review.polish(self.plan, "storyboard", model="review-model")
        self.assertIn("visual_plan_fingerprint", review)
        self.assertEqual(len(review["visual_plan_fingerprint"]), 12)
        self.assertNotEqual(review["visual_plan_fingerprint"],
                            review["plan_fingerprint"])

    def test_storyboard_polish_includes_asset_prompts_required_by_renderer(self):
        response = '{"prompt_zh":"提示词","negative_prompt_zh":"不要文字"}'
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat", return_value=response):
            review = prompt_review.polish(self.plan, "storyboard", model="review-model")
        self.assertTrue(review.get("asset_prompts"))
        self.assertEqual(
            {item["asset_id"] for item in review["asset_prompts"]},
            {item["asset_id"] for item in storyboard.asset_prompt_review_items(self.plan)},
        )

    def test_storyboard_prompt_uses_plan_product_name_and_character_scope(self):
        plan = {"product_name": "1-Vibe Go Lite 马卡龙磁吸无线音箱", "model": "BS8"}
        no_human = {"id": "s1", "dialogue": "台词", "visual": "产品特写", "characters": []}
        with_human = {"id": "s2", "dialogue": "台词", "visual": "Mina 手持产品",
                      "characters": ["mina"]}

        no_human_prompt = prompt_review._base_storyboard_prompt(plan, no_human)
        with_human_prompt = prompt_review._base_storyboard_prompt(plan, with_human)

        self.assertIn("1-Vibe Go Lite 马卡龙磁吸无线音箱", no_human_prompt)
        self.assertIn("型号=BS8", no_human_prompt)
        self.assertIn("本镜头没有人物出镜，不得凭空添加人物", no_human_prompt)
        self.assertNotIn("人物脸、发型、服装必须引用已确认人物素材", no_human_prompt)
        self.assertIn("人物脸、发型、服装必须引用已确认人物素材", with_human_prompt)
        self.assertIn("不得把产品改成耳机、充电宝、充电盒、甜点或其它物品", no_human_prompt)

    def test_polished_prompt_blocks_hands_in_product_only_hook(self):
        item = {"shot_id": "s1", "shot": {
            "id": "s1", "characters": [], "visual": "只展示产品与干净桌面",
            "character_action": "无人出镜，产品以快切节奏成为开场主角"}}
        with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_SCOPE_DRIFT"):
            prompt_review.validate_polished_prompt(
                {}, item, {"prompt_zh": "第9格：一只手拿起产品。"})

    def test_polished_prompt_allows_hands_when_source_requires_operation(self):
        item = {"shot_id": "s2", "shot": {
            "id": "s2", "characters": [], "visual": "手将产品贴近手机背面",
            "character_action": "拇指和食指拿住产品边缘"}}
        prompt_review.validate_polished_prompt(
            {}, item, {"prompt_zh": "第1格：拇指和食指捏住产品。"})

    def test_polished_prompt_ignores_no_human_negative_constraints(self):
        item = {"shot_id": "s4", "shot": {
            "id": "s4", "characters": [], "visual": "只展示两台产品",
            "character_action": "无人出镜"}}
        prompt_review.validate_polished_prompt(
            {}, item, {"prompt_zh": "第1格：两台产品并排。严禁出现手部或人物。全程无任何人物或肢体出现。"})

    def test_extract_json_repairs_unescaped_inner_quotes(self):
        # Real-world failure mode: kimi-k3 quotes spoken dialogue verbatim
        # using raw ASCII '"' instead of escaping it, e.g. 台词"出门..."响起。
        # json.loads on the raw text fails at the first stray quote; the
        # repair heuristic must recover the full structure and content.
        broken = ('{"prompt_zh":"台词"出门包包里一塞"响起，右手推入侧袋",'
                  '"negative_prompt_zh":"字幕、水印"}')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(broken)
        result = prompt_review._extract_json(broken)
        self.assertEqual(result["prompt_zh"], '台词"出门包包里一塞"响起，右手推入侧袋')
        self.assertEqual(result["negative_prompt_zh"], "字幕、水印")

    def test_extract_json_repairs_multiple_inner_quote_pairs(self):
        broken = ('{"prompt_zh":"甲说"你好"，乙答"好的"，随后离开",'
                  '"continuity_in":"上一段末帧为"微笑"状态"}')
        result = prompt_review._extract_json(broken)
        self.assertEqual(result["prompt_zh"], '甲说"你好"，乙答"好的"，随后离开')
        self.assertEqual(result["continuity_in"], '上一段末帧为"微笑"状态')

    def test_extract_json_still_raises_on_truly_invalid_json(self):
        with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_INVALID_JSON"):
            prompt_review._extract_json('{"prompt_zh": not even json')

    def test_polish_retries_and_recovers_from_bad_json_once(self):
        # First call returns unparseable JSON (even after repair); second
        # call (retry with corrective follow-up message) returns valid JSON.
        # polish() must not abort the whole batch over one bad response.
        responses = iter([
            '{"prompt_zh": not even json',
            '{"prompt_zh":"修复后的提示词","negative_prompt_zh":"不要文字"}',
        ])
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               side_effect=lambda *a, **k: next(responses)):
            review = prompt_review.polish(
                {"shots": [{"id": "s1", "dialogue": "台词"}]}, "video", model="review-model")
        self.assertEqual(review["prompts"][0]["prompt_zh"], "修复后的提示词")

    def test_polish_raises_after_exhausting_retries(self):
        with mock.patch.object(prompt_review.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(prompt_review.br_client, "chat",
                               return_value='{"prompt_zh": not even json'):
            with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_INVALID_JSON"):
                prompt_review.polish(
                    {"shots": [{"id": "s1", "dialogue": "台词"}]}, "video", model="review-model")
