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

    def write_result(self, directory, plan, fingerprint=True, shots=None):
        os.makedirs(directory, exist_ok=True)
        result = {
            "ok": True,
            "model": "gpt-image-2",
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
            self.write_result(revision, new_plan)
            self.assertEqual(
                storyboard.resolve_run_output_dir(root, new_plan, run_id="promo"), revision
            )

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
                "shots": [{"shot": {"id": "s1"}, "path": shot, "abspath": shot}],
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
            record = storyboard.confirm_board(result_path, "usage")
            self.assertEqual(record["path"], usage)
            self.assertTrue(storyboard._approval_current(root, "usage", "usage-fp"))
            with open(usage, "ab") as handle:
                handle.write(b"changed")
            self.assertFalse(storyboard._approval_current(root, "usage", "usage-fp"))


if __name__ == "__main__":
    unittest.main()
