import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import storyboard  # noqa: E402
import asset_prep  # noqa: E402


class StoryboardResumeUXTests(unittest.TestCase):
    def plan(self, dialogue="original"):
        return {
            "client": "acme",
            "project_title": "Demo",
            "aspect_ratio": "16:9",
            "shots": [{"id": "s1", "dialogue": dialogue}],
        }

    def write_result(self, directory, plan, fingerprint=True, shots=None, run_id=None):
        os.makedirs(directory, exist_ok=True)
        result = {
            "ok": True,
            "model": "gpt-image-2",
            "run_id": run_id or os.path.basename(directory),
            "shots": shots if shots is not None else [],
        }
        if fingerprint:
            result["plan_fingerprint"] = storyboard.plan_fingerprint(plan)
        with open(os.path.join(directory, "storyboard_result.json"), "w", encoding="utf-8") as handle:
            json.dump(result, handle)

    def test_same_plan_reuses_explicit_run_id(self):
        with tempfile.TemporaryDirectory() as root:
            plan = self.plan()
            base = os.path.join(root, "promo")
            self.write_result(base, plan)
            self.assertEqual(
                storyboard.resolve_run_output_dir(root, plan, run_id="promo"), base
            )

    def test_modified_plan_creates_new_revision_and_preserves_old(self):
        with tempfile.TemporaryDirectory() as root:
            old_plan = self.plan()
            old_dir = os.path.join(root, "promo")
            self.write_result(old_dir, old_plan)
            new_plan = self.plan(dialogue="customer requested a different opening")
            new_dir = storyboard.resolve_run_output_dir(root, new_plan, run_id="promo")
            self.assertEqual(new_dir, os.path.join(root, "promo__r02"))
            self.assertTrue(os.path.isfile(os.path.join(old_dir, "storyboard_result.json")))

    def test_existing_matching_revision_is_reused(self):
        with tempfile.TemporaryDirectory() as root:
            old_plan = self.plan()
            self.write_result(os.path.join(root, "promo"), old_plan)
            new_plan = self.plan(dialogue="revised")
            revision = os.path.join(root, "promo__r02")
            self.write_result(revision, new_plan, run_id="promo")
            self.assertEqual(
                storyboard.resolve_run_output_dir(root, new_plan, run_id="promo"), revision
            )

    def test_run_pointer_resolves_current_revision(self):
        with tempfile.TemporaryDirectory() as root:
            old_plan = self.plan()
            old_dir = os.path.join(root, "promo")
            self.write_result(old_dir, old_plan)
            new_plan = self.plan(dialogue="revised")
            revision = os.path.join(root, "promo__r02")
            self.write_result(revision, new_plan, run_id="promo")
            pointer = storyboard._write_run_pointer(
                root, "promo", storyboard.canonical_storyboard_plan(new_plan),
                revision, os.path.join(revision, "storyboard_result.json"),
                stage="product_usage_image")
            self.assertTrue(os.path.isfile(pointer))
            self.assertEqual(
                storyboard.resolve_current_storyboard_dir(root, "promo", new_plan),
                os.path.abspath(revision))

    def test_run_pointer_rejects_stale_plan(self):
        with tempfile.TemporaryDirectory() as root:
            old_plan = self.plan()
            old_dir = os.path.join(root, "promo")
            self.write_result(old_dir, old_plan)
            pointer = storyboard._write_run_pointer(
                root, "promo", storyboard.canonical_storyboard_plan(old_plan),
                old_dir, os.path.join(old_dir, "storyboard_result.json"),
                stage="storyboard")
            self.assertTrue(os.path.isfile(pointer))
            new_plan = self.plan(dialogue="revised")
            self.assertIsNone(
                storyboard.resolve_current_storyboard_dir(root, "promo", new_plan))

    def test_runtime_prompt_fields_do_not_change_plan_identity(self):
        plan = self.plan()
        runtime = self.plan()
        runtime["_asset_composition_briefs"] = {
            "product_usage_image": {"composition_strategy": "runtime only"}
        }
        runtime["shots"][0]["approved_prompt_zh"] = "confirmed prompt"
        runtime["shots"][0]["approved_submission_prompt_zh"] = "model prompt"
        runtime["shots"][0]["references"] = [{"tag": "@product", "url": "x"}]
        runtime["shots"][0]["panel_plan"] = list(storyboard.DEFAULT_PANEL_PLAN)
        self.assertEqual(storyboard.plan_fingerprint(plan),
                         storyboard.plan_fingerprint(runtime))
        self.assertEqual(storyboard.visual_plan_fingerprint(plan),
                         storyboard.visual_plan_fingerprint(runtime))

    def test_legacy_partial_checkpoint_can_resume(self):
        with tempfile.TemporaryDirectory() as root:
            plan = self.plan()
            old_shot = {"id": "s1", "dialogue": "original"}
            self.write_result(
                os.path.join(root, "promo"), plan, fingerprint=False,
                shots=[{"shot": old_shot, "path": "shot_01.jpg"}],
            )
            self.assertEqual(
                storyboard.resolve_run_output_dir(root, plan, run_id="promo"),
                os.path.join(root, "promo"),
            )

    def test_timeout_does_not_submit_a_second_billable_task(self):
        calls = []

        def submit(*args, **kwargs):
            calls.append("submit")
            return "task-original"

        with mock.patch.object(storyboard.br_client, "create_image_generation", side_effect=submit), \
             mock.patch.object(storyboard.br_client, "wait_image_generation",
                               side_effect=RuntimeError("timeout after 900s")):
            with self.assertRaises(RuntimeError):
                storyboard.download_first_image("key", "prompt", "/tmp/storyboard-test.jpg")
        self.assertEqual(calls, ["submit"])

    def test_sync_img2img_flag_still_uses_documented_async_retrieve(self):
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "storyboard-test.jpg")
            with mock.patch.object(
                    storyboard.br_client, "create_image",
                    side_effect=AssertionError("legacy sync path used")), \
                    mock.patch.object(
                        storyboard.br_client, "create_image_generation",
                        return_value="img_async"), \
                    mock.patch.object(
                        storyboard.br_client, "wait_image_generation",
                        return_value=["https://example.test/image.jpg"]), \
                    mock.patch.object(
                        storyboard.br_client, "download",
                        side_effect=lambda _url, path, **_kwargs:
                        self._write_minimal_png(path)):
                result = storyboard.download_first_image(
                    "key", "prompt", out, image_urls=["data:image/png;base64,AAA"],
                    sync_img2img=True)
        self.assertEqual(result["task_id"], "img_async")
        self.assertEqual(result["abspath"], out)

    def test_download_force_regenerates_existing_image(self):
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "storyboard-test.jpg")
            with open(out, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")
            with mock.patch.object(
                    storyboard.br_client, "create_image_generation",
                    return_value="img_force"), \
                    mock.patch.object(
                        storyboard.br_client, "wait_image_generation",
                        return_value=["https://example.test/image.jpg"]), \
                    mock.patch.object(
                        storyboard.br_client, "download",
                        side_effect=lambda _url, path, **_kwargs:
                        self._write_minimal_png(path)):
                result = storyboard.download_first_image(
                    "key", "prompt", out, force=True)
        self.assertFalse(result.get("skipped"))
        self.assertEqual(result["task_id"], "img_force")

    def test_confirm_board_persists_retrieve_url_for_video_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            board = os.path.join(directory, "cast_board.jpg")
            self._write_minimal_png(board)
            result = {
                "client": "acme",
                "run_id": "run1",
                "plan_fingerprint": "plan",
                "model": "gpt-image-2",
                "cast_board": {
                    "path": board,
                    "abspath": board,
                    "source_fingerprint": "src",
                    "url": "https://cdn.example/cast.png",
                    "task_id": "img-cast",
                    "request_id": "req-cast",
                },
            }
            result_path = os.path.join(directory, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            record = storyboard.confirm_board(result_path, "cast")
            with open(os.path.join(directory, ".cast_confirmed.json"),
                      encoding="utf-8") as handle:
                saved = json.load(handle)
        self.assertEqual(record["url"], "https://cdn.example/cast.png")
        self.assertEqual(saved["task_id"], "img-cast")

    def test_confirm_board_requires_retrieve_url_for_video_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            board = os.path.join(directory, "cast_board.jpg")
            self._write_minimal_png(board)
            result_path = os.path.join(directory, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "client": "acme",
                    "run_id": "run1",
                    "plan_fingerprint": "plan",
                    "model": "gpt-image-2",
                    "cast_board": {
                        "path": board,
                        "source_fingerprint": "src",
                    },
                }, handle)
            with self.assertRaisesRegex(Exception, "retrieve URL"):
                storyboard.confirm_board(result_path, "cast")

    def test_force_cast_board_regenerates_existing_file_and_clears_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            run_root = os.path.join(root, "storyboard")
            run_dir = os.path.join(run_root, "run-1")
            os.makedirs(run_dir)
            cast = os.path.join(run_dir, "cast_board.jpg")
            self._write_minimal_png(cast)
            plan_path = os.path.join(root, "plan.json")
            plan = {
                "client": "acme",
                "project_title": "Force cast",
                "aspect_ratio": "16:9",
                "characters": [{"id": "mina", "name": "Mina"}],
                "shots": [{"id": "s1", "visual": "host close-up",
                           "characters": ["mina"]}],
            }
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump(plan, handle)
            review_plan = storyboard.expand_product_sku_refs(
                storyboard.canonical_storyboard_plan(plan))
            asset_prompts = storyboard.asset_prompt_review_items(review_plan)
            review_path = os.path.join(root, "review.json")
            with open(review_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "status": "confirmed",
                    "stage": "storyboard",
                    "visual_plan_fingerprint": storyboard.visual_plan_fingerprint(review_plan),
                    "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}],
                    "asset_prompts": asset_prompts,
                }, handle)
            result_path = os.path.join(run_dir, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "client": "acme",
                    "run_id": "run-1",
                    "plan_fingerprint": storyboard.plan_fingerprint(plan),
                    "model": "gpt-image-2",
                    "out_dir": run_dir,
                    "cast_board": {
                        "path": cast,
                        "abspath": cast,
                        "source_fingerprint": storyboard._source_refs_fingerprint([]),
                        "url": "https://cdn.example/old-cast.png",
                    },
                }, handle)
            storyboard.confirm_board(result_path, "cast")
            self.assertTrue(os.path.exists(storyboard._approval_path(run_dir, "cast")))

            generated = []

            def fake_download(_api_key, _prompt, out_path, **kwargs):
                generated.append((os.path.basename(out_path), kwargs.get("force")))
                self._write_minimal_png(out_path)
                return {"url": "https://cdn.example/new-cast.png",
                        "path": out_path, "abspath": out_path,
                        "task_id": "img-new-cast"}

            with mock.patch.object(storyboard.key_setup, "ensure_session_id",
                                   return_value="sid"), \
                    mock.patch.object(storyboard.key_setup, "load_key",
                                      return_value="sk-test"), \
                    mock.patch.object(asset_prep, "_load_brief",
                                      return_value={"client": "acme", "images": []}), \
                    mock.patch.object(storyboard, "download_first_image",
                                      side_effect=fake_download):
                result = storyboard.render_storyboard(
                    plan_path, run_dir, run_id="run-1", flat=True, stage="cast",
                    prompt_review=review_path, force_boards=["cast"])

            self.assertEqual(generated, [("cast_board.jpg", True)])
            self.assertFalse(os.path.exists(storyboard._approval_path(run_dir, "cast")))
            self.assertEqual(result["cast_board"]["url"],
                             "https://cdn.example/new-cast.png")
            self.assertEqual(result["cast_board"]["status"], "pending")
            backup = result["cast_board"]["forced_regeneration"]["previous_backup"]
            self.assertTrue(os.path.isfile(backup))

    def test_force_usage_clears_stale_result_before_async_submission_finishes(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run-1")
            os.makedirs(run_dir)
            product = os.path.join(run_dir, "product_board.jpg")
            cast = os.path.join(run_dir, "cast_board.jpg")
            usage = os.path.join(run_dir, "product_usage_board.jpg")
            portrait = os.path.join(root, "mina.png")
            product_hero = os.path.join(root, "hero.png")
            for path, content in (
                    (product, b"product"),
                    (cast, b"cast"),
                    (usage, b"old-usage"),
                    (portrait, b"portrait"),
                    (product_hero, b"hero")):
                with open(path, "wb") as handle:
                    handle.write(content)
            plan = {
                "client": "acme",
                "project_title": "Force usage",
                "aspect_ratio": "16:9",
                "asset_refs": {
                    "product_images": [product_hero],
                    "digital_human_portraits": [portrait],
                },
                "characters": [{"id": "mina", "name": "Mina"}],
                "shots": [{
                    "id": "s1",
                    "visual": "Mina magnetically attaches the product bottom to the phone back",
                    "characters": ["mina"],
                    "character_action": "attach the product bottom surface to the phone back",
                    "props": "product and phone",
                }],
            }
            plan_path = os.path.join(root, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump(plan, handle)
            review_plan = storyboard.expand_product_sku_refs(
                storyboard.canonical_storyboard_plan(plan))
            asset_prompts = storyboard.asset_prompt_review_items(review_plan)
            review_path = os.path.join(root, "review.json")
            with open(review_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "status": "confirmed",
                    "stage": "storyboard",
                    "visual_plan_fingerprint": storyboard.visual_plan_fingerprint(review_plan),
                    "prompts": [{"shot_id": "s1", "prompt_zh": "P1"}],
                    "asset_prompts": asset_prompts,
                }, handle)
            result_path = os.path.join(run_dir, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "client": "acme",
                    "run_id": "run-1",
                    "plan_fingerprint": storyboard.plan_fingerprint(plan),
                    "model": "gpt-image-2",
                    "out_dir": run_dir,
                    "product_usage_image": {
                        "path": usage,
                        "abspath": usage,
                        "source_fingerprint": "old-usage-fp",
                        "url": "https://cdn.example/old-usage.png",
                        "task_id": "old-task",
                        "request_id": "old-request",
                        "sha256": "old-sha",
                        "status": "confirmed",
                    },
                }, handle)
            with open(storyboard._approval_path(run_dir, "usage"), "w",
                      encoding="utf-8") as handle:
                json.dump({"status": "confirmed"}, handle)

            def fake_registry(_client, kind, _fingerprint):
                if kind == "product":
                    return {"path": product, "abspath": product,
                            "url": "https://cdn.example/product.png",
                            "source_fingerprint": "product-fp"}
                if kind == "cast":
                    return {"path": cast, "abspath": cast,
                            "url": "https://cdn.example/cast.png",
                            "source_fingerprint": "cast-fp"}
                return None

            def fake_collect(refs, *_args, **_kwargs):
                values = refs if isinstance(refs, list) else [refs]
                return ["https://cdn.example/%s.png" %
                        os.path.splitext(os.path.basename(str(value)))[0]
                        for value in values if value]

            def interrupted_download(_api_key, _prompt, _out_path, **kwargs):
                kwargs["on_progress"]({
                    "status": "submitted",
                    "task_id": "new-task",
                    "waited": 0,
                })
                raise RuntimeError("interrupted after submit")

            with mock.patch.object(storyboard.key_setup, "ensure_session_id",
                                   return_value="sid"), \
                    mock.patch.object(storyboard.key_setup, "load_key",
                                      return_value="sk-test"), \
                    mock.patch.object(asset_prep, "_load_brief",
                                      return_value={"client": "acme", "images": [
                                          {"path": product_hero}
                                      ]}), \
                    mock.patch.object(asset_prep, "is_product_asset_ready",
                                      return_value=True), \
                    mock.patch.object(storyboard, "_registered_board",
                                      side_effect=fake_registry), \
                    mock.patch.object(storyboard, "_confirmed_product_identity_paths",
                                      return_value=[product, product_hero]), \
                    mock.patch.object(storyboard, "_collect_image_urls",
                                      side_effect=fake_collect), \
                    mock.patch.object(storyboard, "download_first_image",
                                      side_effect=interrupted_download):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    storyboard.render_storyboard(
                        plan_path, run_dir, run_id="run-1", flat=True,
                        stage="usage", prompt_review=review_path,
                        force_boards=["usage"])

            with open(result_path, encoding="utf-8") as handle:
                updated = json.load(handle)
            board = updated["product_usage_image"]
            self.assertEqual(board["status"], "pending_regeneration")
            self.assertNotIn("url", board)
            self.assertNotIn("task_id", board)
            self.assertNotIn("request_id", board)
            self.assertNotIn("sha256", board)
            self.assertEqual(board["superseded"]["url"],
                             "https://cdn.example/old-usage.png")
            self.assertEqual(updated["in_progress"]["task_id"], "new-task")
            self.assertFalse(os.path.exists(storyboard._approval_path(run_dir, "usage")))
            self.assertFalse(os.path.exists(usage))
            self.assertTrue(os.path.isfile(board["superseded"]["previous_backup"]))

    def test_only_shot_regeneration_carries_forward_existing_confirmed_shots(self):
        with tempfile.TemporaryDirectory() as root:
            plan_path = os.path.join(root, "plan.json")
            plan = {
                "client": "acme",
                "project_title": "Only shot regen",
                "aspect_ratio": "16:9",
                "shots": [
                    {"id": "s1", "visual": "opening frame", "characters": []},
                    {"id": "s2", "visual": "second frame action", "characters": []},
                ],
            }
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump(plan, handle)
            review_path = os.path.join(root, "review.json")
            review = {
                "status": "confirmed",
                "stage": "storyboard",
                "visual_plan_fingerprint": storyboard.visual_plan_fingerprint(plan),
                "prompts": [
                    {"shot_id": "s1", "prompt_zh": "P1"},
                    {"shot_id": "s2", "prompt_zh": "P2"},
                ],
            }
            with open(review_path, "w", encoding="utf-8") as handle:
                json.dump(review, handle)
            run_dir = os.path.join(root, "run-1")
            os.makedirs(run_dir)
            s1_path = os.path.join(run_dir, "shot_01_s1.jpg")
            s2_path = os.path.join(run_dir, "shot_02_s2.jpg")
            with open(s1_path, "wb") as handle:
                handle.write(b"s1-old")
            with open(s2_path, "wb") as handle:
                handle.write(b"s2-old")
            approval = {
                "status": "confirmed",
                "kind": "storyboard",
                "client": "acme",
                "run_id": "run-1",
                "out_dir": run_dir,
                "plan_fingerprint": "old-plan",
                "shots": [
                    {"id": "s1", "path": s1_path, "sha256": "old-s1",
                     "shot_fingerprint": "old-s1-fp"},
                    {"id": "s2", "path": s2_path, "sha256": "old-s2",
                     "shot_fingerprint": "old-s2-fp"},
                ],
            }
            with open(storyboard._approval_path(run_dir, "storyboard"), "w",
                      encoding="utf-8") as handle:
                json.dump(approval, handle)
            with open(os.path.join(run_dir, "storyboard_result.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({
                    "ok": True, "model": "gpt-image-2",
                    "plan_fingerprint": "stale-plan",
                    "client": "acme", "run_id": "run-1",
                    "out_dir": run_dir, "shots": [],
                }, handle)

            generated = []

            def fake_download(_api_key, _prompt, out_path, **_kwargs):
                generated.append(os.path.basename(out_path))
                with open(out_path, "wb") as handle:
                    handle.write(b"s2-new")
                return {"url": "https://example.test/s2.jpg",
                        "path": out_path, "abspath": out_path,
                        "task_id": "img-s2"}

            with mock.patch.object(storyboard.key_setup, "ensure_session_id",
                                   return_value="sid"), \
                    mock.patch.object(storyboard.key_setup, "load_key",
                                      return_value="sk-test"), \
                    mock.patch.object(asset_prep, "_load_brief",
                                      return_value={"client": "acme", "images": []}), \
                    mock.patch.object(storyboard, "download_first_image",
                                      side_effect=fake_download):
                result = storyboard.render_storyboard(
                    plan_path, root, run_id="run-1", stage="storyboard",
                    prompt_review=review_path, only_shot_ids=["s2"])

            self.assertEqual(generated, ["shot_02_s2.jpg"])
            by_id = {item["shot"]["id"]: item for item in result["shots"]}
            self.assertTrue(by_id["s1"]["carried_forward"])
            self.assertEqual(by_id["s1"]["id"], "s1")
            self.assertEqual(by_id["s1"]["sha256"], storyboard._file_sha256(s1_path))
            self.assertFalse(by_id["s2"].get("carried_forward", False))
            self.assertEqual(by_id["s2"]["id"], "s2")
            self.assertEqual(result["expected_shot_ids"], ["s1", "s2"])

    def _write_minimal_png(self, path):
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")

    def test_complete_storyboard_confirmation_binds_plan_identity_and_files(self):
        with tempfile.TemporaryDirectory() as root:
            shot = os.path.join(root, "shot_01_s1.jpg")
            with open(shot, "wb") as handle:
                handle.write(b"storyboard-v1")
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "ok": True, "client": "acme", "run_id": "promo",
                "out_dir": root, "plan_fingerprint": "plan-fp",
                "needs_confirmation": True,
                "shots": [{"shot": {"id": "s1"}, "path": shot, "abspath": shot,
                           "url": "https://cdn.example/shot-s1.png"}],
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            record = storyboard.confirm_storyboard(result_path)
            self.assertEqual(record["shots"][0]["id"], "s1")
            self.assertTrue(storyboard.storyboard_approval_is_current(
                result_path, client="acme", run_id="promo",
                out_dir=root, plan_fingerprint_value="plan-fp"))
            with open(shot, "wb") as handle:
                handle.write(b"storyboard-v2")
            self.assertFalse(storyboard.storyboard_approval_is_current(result_path))

    def test_storyboard_confirmation_requires_retrieve_url_per_shot(self):
        with tempfile.TemporaryDirectory() as root:
            shot = os.path.join(root, "shot_01_s1.jpg")
            with open(shot, "wb") as handle:
                handle.write(b"storyboard-v1")
            result_path = os.path.join(root, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "ok": True, "client": "acme", "run_id": "promo",
                    "out_dir": root, "plan_fingerprint": "plan-fp",
                    "shots": [{"shot": {"id": "s1"}, "path": shot, "abspath": shot}],
                }, handle)
            with self.assertRaisesRegex(Exception, "retrieve URL"):
                storyboard.confirm_storyboard(result_path)

    def test_stage_all_requires_explicit_debug_flag(self):
        with self.assertRaisesRegex(Exception, "STAGE_ALL_BLOCKED"):
            storyboard.render_storyboard("unused.json", "unused", stage="all")

    def test_stage_all_cannot_bypass_confirmations_even_in_debug(self):
        with self.assertRaisesRegex(Exception, "STAGE_ALL_BLOCKED"):
                storyboard.render_storyboard(
                    "unused.json", "unused", stage="all", debug_allow_all=True)

    def test_shot_level_character_action_requires_product_usage_stage(self):
        plan = self.plan()
        plan["shots"][0].update({
            "character_action": "host picks up the product",
            "product_refs": ["assets/acme/images/product.png"],
        })
        self.assertTrue(storyboard.needs_product_usage_image(plan))

    def test_unregistered_client_image_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            client_root = os.path.join(root, "assets", "acme")
            image_dir = os.path.join(client_root, "images")
            os.makedirs(image_dir)
            image_path = os.path.join(image_dir, "product.png")
            with open(image_path, "wb") as handle:
                handle.write(bytes.fromhex("89504e470d0a1a0a"))
            with mock.patch.object(storyboard, "ROOT", root), \
                    mock.patch.object(asset_prep, "_load_brief",
                                      return_value={"client": "acme", "images": []}):
                with self.assertRaisesRegex(Exception, "UNREGISTERED_PRODUCT_IMAGE"):
                    storyboard._hydrate_plan_asset_refs({"client": "acme", "asset_refs": {}})

    def test_usage_confirmation_binds_usage_bytes_and_expires_on_change(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "product_usage.jpg")
            product = os.path.join(root, "product_board.jpg")
            with open(usage, "wb") as handle:
                handle.write(b"usage-v1")
            with open(product, "wb") as handle:
                handle.write(b"product")
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "client": "acme", "run_id": "run-1", "model": "gpt-image-2",
                "plan_fingerprint": "plan-fp",
                "product_usage_image": {
                    "path": usage,
                    "source_fingerprint": "usage-fp",
                    "identity_reference_paths": [product],
                    "url": "https://cdn.example/usage.png",
                },
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            record = storyboard.confirm_board(result_path, "usage")
            self.assertEqual(record["path"], usage)
            self.assertEqual(record["identity_reference_paths"], [product])
            self.assertTrue(storyboard._approval_current(root, "usage", "usage-fp"))
            with open(usage, "ab") as handle:
                handle.write(b"changed")
            self.assertFalse(storyboard._approval_current(root, "usage", "usage-fp"))

    def test_usage_confirmation_requires_geometry_review_for_physical_contract(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "product_usage.jpg")
            product = os.path.join(root, "product_board.jpg")
            with open(usage, "wb") as handle:
                handle.write(b"usage-v1")
            with open(product, "wb") as handle:
                handle.write(b"product")
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "client": "acme", "run_id": "run-1", "model": "gpt-image-2",
                "plan_fingerprint": "plan-fp",
                "product_usage_image": {
                    "path": usage,
                    "source_fingerprint": "usage-fp",
                    "identity_reference_paths": [product],
                    "url": "https://cdn.example/usage.png",
                    "usage_policy_version": storyboard.PRODUCT_USAGE_POLICY_VERSION,
                    "geometry_contract": "bottom_surface_magnetic_attach_to_receiver_back",
                },
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            with self.assertRaisesRegex(Exception, "USAGE_GEOMETRY_REVIEW_REQUIRED"):
                storyboard.confirm_board(result_path, "usage")
            record = storyboard.confirm_board(
                result_path, "usage", geometry_reviewed=True)
        self.assertTrue(record["geometry_reviewed"])
        self.assertEqual(
            record["geometry_contract"], "bottom_surface_magnetic_attach_to_receiver_back")

    def test_usage_approval_expires_when_identity_reference_disappears(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "product_usage.jpg")
            product = os.path.join(root, "product_board.jpg")
            for path, content in ((usage, b"usage-v1"), (product, b"product")):
                with open(path, "wb") as handle:
                    handle.write(content)
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "client": "acme", "run_id": "run-1", "model": "gpt-image-2",
                "plan_fingerprint": "plan-fp",
                "product_usage_image": {
                    "path": usage,
                    "source_fingerprint": "usage-fp",
                    "identity_reference_paths": [product],
                    "url": "https://cdn.example/usage.png",
                },
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            storyboard.confirm_board(result_path, "usage")
            self.assertTrue(storyboard._approval_current(root, "usage", "usage-fp"))
            os.remove(product)
            self.assertFalse(storyboard._approval_current(root, "usage", "usage-fp"))

    def test_usage_confirmation_requires_identity_reference_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "product_usage.jpg")
            with open(usage, "wb") as handle:
                handle.write(b"usage-v1")
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "client": "acme", "run_id": "run-1", "model": "gpt-image-2",
                "plan_fingerprint": "plan-fp",
                "product_usage_image": {"path": usage, "source_fingerprint": "usage-fp"},
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            with self.assertRaisesRegex(Exception, "USAGE_IDENTITY_REFERENCES_REQUIRED"):
                storyboard.confirm_board(result_path, "usage")

    def test_refine_usage_board_replaces_fixed_file_and_requires_reconfirmation(self):
        with tempfile.TemporaryDirectory() as root:
            usage = os.path.join(root, "product_usage_board.jpg")
            cast = os.path.join(root, "cast_board.jpg")
            product = os.path.join(root, "product_board.jpg")
            product_hero = os.path.join(root, "product_hero.png")
            feedback_a = os.path.join(root, "feedback_wearing.png")
            feedback_b = os.path.join(root, "feedback_angle.png")
            for path, content in (
                    (usage, b"usage-v1"),
                    (cast, b"cast"),
                    (product, b"product"),
                    (product_hero, b"product-hero"),
                    (feedback_a, b"feedback-a"),
                    (feedback_b, b"feedback-b")):
                with open(path, "wb") as handle:
                    handle.write(content)
            plan_path = os.path.join(root, "storyboard_plan.json")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump({"client": "acme",
                           "asset_refs": {"product_images": [product_hero]}}, handle)
            result_path = os.path.join(root, "storyboard_result.json")
            result = {
                "client": "acme", "run_id": "run-1", "model": "gpt-image-2",
                "plan_fingerprint": "plan-fp", "out_dir": root,
                "plan_source": plan_path,
                "product_usage_image": {"path": usage, "source_fingerprint": "usage-fp",
                                        "url": "https://cdn.example/usage.png",
                                        "sha256": "old-sha",
                                        "identity_reference_paths": [product]},
                "cast_board": {"path": cast},
                "product_board": {"path": product},
            }
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            storyboard.confirm_board(result_path, "usage")
            self.assertTrue(os.path.exists(storyboard._approval_path(root, "usage")))

            collected_refs = []

            def fake_collect(paths, *_args, **_kwargs):
                collected_refs.extend(os.path.abspath(p) for p in paths)
                return ["ref:%s" % os.path.basename(p) for p in paths]

            progress_events = []
            prompt_seen = []

            def fake_download(_api_key, prompt, out_path, **kwargs):
                prompt_seen.append(prompt)
                kwargs["on_progress"]({"status": "submitted", "waited": 0})
                progress_events.append("submitted")
                with open(out_path, "wb") as handle:
                    handle.write(b"usage-v2")
                return {"url": "https://example.test/refined.png", "path": out_path,
                        "abspath": out_path, "task_id": "img_refined"}

            with mock.patch.object(storyboard.key_setup, "ensure_session_id",
                                   return_value="br-test") as ensure_sid, \
                    mock.patch.object(storyboard.key_setup, "load_key", return_value="k"), \
                    mock.patch.object(storyboard.asset_prep, "is_product_asset_ready",
                                      return_value=True), \
                    mock.patch.object(storyboard, "_collect_image_urls",
                                      side_effect=fake_collect), \
                    mock.patch.object(storyboard, "download_first_image",
                                      side_effect=fake_download):
                refined = storyboard.refine_board(
                    result_path, "usage", "修正手指接触点和产品角度",
                    feedback_refs=[feedback_a, feedback_b])

            self.assertEqual(refined["status"], "pending")
            ensure_sid.assert_called_once_with()
            self.assertEqual(progress_events, ["submitted"])
            self.assertIn("confirmed product identity anchors", prompt_seen[0])
            self.assertIn("do not inherit any product-shape drift", prompt_seen[0])
            self.assertEqual(refined["path"], usage)
            self.assertFalse(os.path.exists(storyboard._approval_path(root, "usage")))
            self.assertTrue(os.path.isfile(refined["backup_path"]))
            with open(usage, "rb") as handle:
                self.assertEqual(handle.read(), b"usage-v2")
            with open(result_path, encoding="utf-8") as handle:
                updated = json.load(handle)
            self.assertTrue(updated["needs_confirmation"])
            self.assertEqual(updated["product_usage_image"]["status"], "pending")
            self.assertEqual(updated["product_usage_image"]["refinement"]["feedback_refs"],
                             [os.path.abspath(feedback_a), os.path.abspath(feedback_b)])
            self.assertEqual(
                updated["product_usage_image"]["refinement"]["identity_reference_paths"],
                [os.path.abspath(product), os.path.abspath(product_hero)])
            self.assertEqual(
                updated["product_usage_image"]["refinement"]["generation_reference_paths"],
                [os.path.abspath(product), os.path.abspath(product_hero),
                 os.path.abspath(usage), os.path.abspath(feedback_a)])
            self.assertEqual(collected_refs, [
                os.path.abspath(product),
                os.path.abspath(product_hero),
                os.path.abspath(usage),
                os.path.abspath(feedback_a),
            ])


if __name__ == "__main__":
    unittest.main()
