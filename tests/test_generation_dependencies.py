import os
import sys
import json
import tempfile
import shutil
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_manifest  # noqa: E402


class GenerationDependencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="manifest_artifacts_")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def approve(self, manifest, stage):
        path = os.path.join(self.directory, "%s.json" % stage)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stage": stage}, handle)
        run_manifest.mark_generation_finished(manifest, stage, [path])
        run_manifest.approve(manifest, stage, strict=True)

    def test_storyboard_requires_confirmed_cast_board(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        self.approve(manifest, "brief")
        self.approve(manifest, "script")
        with self.assertRaisesRegex(ValueError, "GENERATION_BLOCKED"):
            run_manifest.generation_gate(manifest, "storyboard")
        self.approve(manifest, "cast_board")
        self.assertTrue(run_manifest.generation_gate(manifest, "storyboard"))

    def test_product_board_is_required_only_when_flagged(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        manifest["requires_product_board"] = True
        self.approve(manifest, "brief")
        self.approve(manifest, "script")
        self.approve(manifest, "cast_board")
        with self.assertRaisesRegex(ValueError, "product_board"):
            run_manifest.generation_gate(manifest, "storyboard")
        self.approve(manifest, "product_board")
        self.assertTrue(run_manifest.generation_gate(manifest, "storyboard"))

    def test_video_also_requires_confirmed_product_board(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        manifest["requires_product_board"] = True
        for stage in ("brief", "script", "cast_board", "product_board", "storyboard", "render_plan"):
            self.approve(manifest, stage)
        # Revoke the product-board approval to prove the video gate is wired to it.
        manifest["approvals"]["product_board"] = False
        manifest["approval_hashes"].pop("product_board", None)
        with self.assertRaisesRegex(ValueError, "product_board"):
            run_manifest.generation_gate(manifest, "video")
        self.approve(manifest, "product_board")
        self.assertTrue(run_manifest.generation_gate(manifest, "video"))

    def test_manifest_infers_product_board_requirement_from_plan(self):
        directory = tempfile.mkdtemp(prefix="manifest_plan_")
        try:
            plan_path = os.path.join(directory, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump({"shots": [{"id": "s1", "asset_refs": {
                    "product_images": ["assets/acme/hero.png"]}}]}, handle)
            manifest = run_manifest.create_manifest("acme", "run-1", plan_path=plan_path)
            self.assertTrue(manifest["requires_product_board"])
            self.assertFalse(manifest["requires_product_usage"])
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_human_and_product_requires_confirmed_usage_image(self):
        directory = tempfile.mkdtemp(prefix="manifest_usage_plan_")
        try:
            plan_path = os.path.join(directory, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump({"characters": [{"id": "host"}],
                           "asset_refs": {"product_images": ["hero.png"]},
                           "shots": [{"id": "s1"}]}, handle)
            manifest = run_manifest.create_manifest("acme", "run-1", plan_path=plan_path)
            self.assertTrue(manifest["requires_product_usage"])
            for stage in ("brief", "script", "product_board", "cast_board"):
                self.approve(manifest, stage)
            with self.assertRaisesRegex(ValueError, "product_usage"):
                run_manifest.generation_gate(manifest, "storyboard")
            self.approve(manifest, "product_usage")
            self.assertTrue(run_manifest.generation_gate(manifest, "storyboard"))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_product_regeneration_revokes_dynamic_storyboard_descendants(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        manifest["requires_product_board"] = True
        manifest["requires_product_usage"] = True
        for stage in ("brief", "script", "product_board", "cast_board",
                      "product_usage", "storyboard", "render_plan"):
            self.approve(manifest, stage)
        run_manifest.mark_generation_started(manifest, "product_board")
        self.assertFalse(manifest["approvals"]["product_usage"])
        self.assertFalse(manifest["approvals"]["storyboard"])
        self.assertFalse(manifest["approvals"]["render_plan"])

    def test_manifest_detects_shot_level_product_sku(self):
        plan_path = os.path.join(self.directory, "sku-plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"characters": [{"id": "host"}],
                       "shots": [{"id": "s1", "product_sku": "chair"}]}, handle)
        manifest = run_manifest.create_manifest("acme", "run-1", plan_path=plan_path)
        self.assertTrue(manifest["requires_product_board"])
        self.assertTrue(manifest["requires_product_usage"])

    def test_finished_generation_remains_pending_until_approval(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        self.approve(manifest, "brief")
        self.approve(manifest, "script")
        self.approve(manifest, "cast_board")
        run_manifest.mark_generation_started(manifest, "storyboard")
        shot = os.path.join(self.directory, "shot.jpg")
        with open(shot, "wb") as handle:
            handle.write(b"shot")
        run_manifest.mark_generation_finished(manifest, "storyboard", [shot])
        self.assertEqual(manifest["generation"]["storyboard"]["status"], "pending_approval")
        self.assertFalse(manifest["approvals"]["storyboard"])

    def test_strict_approval_requires_pending_generation_and_existing_output(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        with self.assertRaisesRegex(ValueError, "pending_approval"):
            run_manifest.approve(manifest, "brief", strict=True)
        with self.assertRaisesRegex(ValueError, "GENERATION_OUTPUT_MISSING"):
            run_manifest.bootstrap_pending_approval(
                manifest, "brief", [os.path.join(self.directory, "missing.json")])

    def test_approval_current_rehashes_artifact_from_disk(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        self.approve(manifest, "brief")
        self.assertTrue(run_manifest.approval_is_current(manifest, "brief"))
        path = manifest["generation"]["brief"]["outputs"][0]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stage": "brief", "changed": True}, handle)
        self.assertFalse(run_manifest.approval_is_current(manifest, "brief"))

    def test_regeneration_revokes_stage_and_downstream_approvals(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        for stage in ("brief", "script"):
            self.approve(manifest, stage)
        self.assertTrue(run_manifest.approval_is_current(manifest, "script"))
        run_manifest.mark_generation_started(manifest, "brief")
        self.assertFalse(manifest["approvals"]["brief"])
        self.assertFalse(manifest["approvals"]["script"])

    def test_identity_gate_is_reusable(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        self.assertTrue(run_manifest.identity_gate(manifest, client="acme"))
        with self.assertRaisesRegex(ValueError, "RUN_IDENTITY_MISMATCH"):
            run_manifest.identity_gate(manifest, client="other")


if __name__ == "__main__":
    unittest.main()
