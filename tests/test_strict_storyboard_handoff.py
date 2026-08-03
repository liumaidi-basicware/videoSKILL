import json
import os
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
            "shots": [{"shot": {"id": shot_id}, "abspath": image}],
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
                "path": board, "source_fingerprint": "cast-source"}
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle)
            storyboard.confirm_board(result_path, "cast")
            storyboard.confirm_storyboard(result_path)
            split = ss.split(plan, storyboard_dir=root, client="acme")
            sources = [ref["source"] for ref in split["segments"][0]["references"]]
            self.assertIn("asset_refs.cast_boards", sources)
            self.assertIn(shot_image, split["segments"][0]["urls"])
            with open(board, "ab") as handle:
                handle.write(b"changed")
            split = ss.split(plan, storyboard_dir=root, client="acme")
            sources = [ref["source"] for ref in split["segments"][0]["references"]]
            self.assertNotIn("asset_refs.cast_boards", sources)

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
