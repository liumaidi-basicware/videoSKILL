import json
import os
import shutil
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline  # noqa: E402
import run_manifest as rm  # noqa: E402
import script_splitter as ss  # noqa: E402
import take_review  # noqa: E402
from artifact_contract import build_video_handoff  # noqa: E402


class OfflineFormalDeliveryE2ETests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="delivery_e2e_")
        self.manifest_path = os.path.join(self.root, "run_manifest.json")
        self.manifest = rm.create_manifest("acme", "offline-1")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name, data=b"x"):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def write_json(self, name, value):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        return path

    def finish_and_approve(self, stage, *paths):
        rm.mark_generation_finished(self.manifest, stage, paths)
        rm.approve(self.manifest, stage, strict=True)

    def build_formal_delivery(self):
        # Everything below is local fixture data. Paid rendering APIs are outside
        # this orchestration boundary and intentionally absent from the test.
        self.finish_and_approve("brief", self.write_json("brief.json", {"name": "product"}))
        self.finish_and_approve("script", self.write("script.txt", b"approved script"))
        self.finish_and_approve("cast_board", self.write("cast.jpg", b"cast"))
        self.finish_and_approve("storyboard", self.write("shot.jpg", b"storyboard"))
        self.finish_and_approve("render_plan", self.write_json("render.json", {"mode": "mock"}))

        approval = {"status": "confirmed", "client": "acme", "run_id": "offline-1",
                    "plan_fingerprint": "plan-fp", "result_json": "result.json",
                    "out_dir": self.root}
        segment = {"id": "s1", "client": "acme", "run_id": "offline-1",
                   "duration": 3, "dialogue": "hello", "storyboard_approval": approval}
        segment["video_handoff_fingerprint"] = build_video_handoff(segment)["fingerprint"]
        handoff = segment["video_handoff_fingerprint"]
        segments_spec = {"client": "acme", "run_id": "offline-1", "segments": [segment],
                         "storyboard_approval": approval, "missing_images": [], "needs_image": []}
        segments_path = self.write_json("segments.json", segments_spec)
        rm.record_video_handoff(self.manifest, segments_spec, segments_path)
        take_path = self.write("take.mp4", b"offline take")
        review = take_review.create_review({
            "segment_id": "s1", "take_id": "take-1",
            "localPath": take_path, "video_handoff_fingerprint": handoff}, segment)
        take_fp = review["artifact"]["take_fingerprint"]
        review = take_review.import_observation(review, {
            "take_fingerprint": take_fp,
            "quality": {
                "technical": {"video_integrity": True, "audio_integrity": True},
                "marketing": {"lip_sync": True, "identity_fidelity": True,
                              "product_fidelity": True, "script_fidelity": True},
                "overall_score": 95,
            },
        }, {"source_type": "human", "reviewer": "offline-fixture"})
        review = take_review.decide(review, "accepted", "customer", "approved")
        review_path = self.write_json("review.json", review)
        rm.record_manual_ocr_review(
            self.manifest, "s1", take_fp, "clear",
            reviewer="offline-qc", reason="离线逐帧检查通过",
            frame_sha256s=["%064x" % index for index in range(1, 13)])
        rm.accept_take(self.manifest, "s1", review, review_path)
        results_path = self.write_json("results.json", [{
            "ok": True, "segment_id": "s1", "localPath": take_path,
            "video_handoff_fingerprint": handoff, "take_fingerprint": take_fp,
            "ocr_warning": False, "ocr_texts": [], "review_status": "accepted",
            "media_qc": {"passed": True}}])
        reviews_path = self.write_json("reviews.json", {"s1": review})
        basecut = self.write("basecut.mp4", b"basecut")
        ss.record_video_stage_pending(
            self.manifest, self.manifest_path, client="acme",
            segments_path=segments_path, results_path=results_path,
            basecut_path=basecut, reviews_path=reviews_path)
        rm.approve(self.manifest, "video", strict=True)

        derived = ss.derive_captions({"segments": [segment]})
        lines = self.write_json("lines.json", derived["lines"])
        srt = self.write("subs.srt", derived["srt"].encode())
        motion = self.write_json("motion.json", derived["motion_plan"])
        caption_path = os.path.join(self.root, "caption_manifest.json")
        artifact = ss.persist_caption_artifact(
            self.manifest, self.manifest_path, client="acme", segments_path=segments_path,
            basecut_path=basecut, lines_path=lines, srt_path=srt, motion_path=motion,
            caption_manifest_path=caption_path)
        artifact = ss.confirm_captions(
            self.manifest, self.manifest_path, caption_path, client="acme")
        final = self.write("final.mp4", b"final release bytes")
        rm.mark_generation_finished(self.manifest, "final", [final])
        final_record = rm.file_record(final)
        self.manifest["delivery_qc"] = {
            "profile": "formal", "passed": True, "file": final_record}
        self.manifest["generation"]["final"]["media_qc"] = self.manifest["delivery_qc"]
        rm.approve(self.manifest, "final", strict=True)
        rm.save_manifest(self.manifest, self.manifest_path)
        return final, artifact

    def test_brief_to_final_approval_and_delivery_without_paid_api(self):
        final, artifact = self.build_formal_delivery()
        delivery_path = os.path.join(self.root, "delivery.json")
        delivery = pipeline.create_delivery(self.manifest, delivery_path)
        self.assertEqual(delivery["final"]["path"], os.path.abspath(final))
        self.assertEqual(delivery["caption_identity"], artifact["caption_identity"])
        self.assertTrue(pipeline.verify_delivery(self.manifest, delivery_path)["ok"])

    def test_delivery_verify_fails_after_final_file_changes(self):
        final, _ = self.build_formal_delivery()
        delivery_path = os.path.join(self.root, "delivery.json")
        pipeline.create_delivery(self.manifest, delivery_path)
        with open(final, "ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ValueError, "FINAL_APPROVAL_REQUIRED_OR_STALE"):
            pipeline.verify_delivery(self.manifest, delivery_path)

    def test_split_manifest_records_handoff_after_atomic_output(self):
        for stage in ("brief", "script", "cast_board", "storyboard", "render_plan"):
            self.finish_and_approve(stage, self.write("%s.bin" % stage, stage.encode()))
        approval = {"status": "confirmed", "client": "acme", "run_id": "offline-1",
                    "plan_fingerprint": "plan-fp", "result_json": "result.json",
                    "out_dir": self.root}
        segment = {"id": "s1", "client": "acme", "run_id": "offline-1",
                   "storyboard_approval": approval}
        segment["video_handoff_fingerprint"] = build_video_handoff(segment)["fingerprint"]
        result = {"client": "acme", "run_id": "offline-1", "segments": [segment],
                  "storyboard_approval": approval, "needs_image": [],
                  "missing_images": [], "unconfirmed_refs": [], "total_seconds": 3}
        rm.save_manifest(self.manifest, self.manifest_path)
        out = os.path.join(self.root, "segments.json")
        plan = self.write_json("plan.json", {"shots": [{"id": "s1"}]})
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(ss, "split", return_value=result), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            ss.main(["split", "--plan", plan, "--client", "acme", "--out", out,
                     "--storyboard-dir", self.root, "--manifest", self.manifest_path])
        self.assertIn("已拆分:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        updated = pipeline._load(self.manifest_path)
        self.assertTrue(rm.file_record_is_current(updated["handoffs"]["video"]["file"]))
        self.assertEqual(updated["handoffs"]["video"]["segments"],
                         {"s1": segment["video_handoff_fingerprint"]})

    def test_status_has_absolute_customer_preview(self):
        brief = self.write("brief.txt", b"brief")
        rm.mark_generation_finished(self.manifest, "brief", [brief])
        status = pipeline.pipeline_status(self.manifest)
        self.assertEqual(status["current_stage"], "brief")
        self.assertEqual(status["next_action"], "approve_brief")
        self.assertEqual(status["customer_preview"], [os.path.abspath(brief)])


if __name__ == "__main__":
    unittest.main()
