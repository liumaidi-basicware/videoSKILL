import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ocr_check  # noqa: E402
import run_manifest  # noqa: E402
import take_review  # noqa: E402


class OcrStatusTests(unittest.TestCase):
    def test_expected_frames_are_one_per_second_with_bounds(self):
        self.assertEqual(ocr_check.expected_frame_count(3.2), 12)
        self.assertEqual(ocr_check.expected_frame_count(20.1), 21)
        self.assertEqual(ocr_check.expected_frame_count(90), 60)

    def test_extract_frames_covers_endpoints_and_fails_on_missing_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "take.mp4")
            with open(video, "wb") as handle:
                handle.write(b"video")
            completed = mock.Mock(returncode=0, stderr="")

            def fake_run(command, **kwargs):
                if command[0] == "probe":
                    return mock.Mock(returncode=0, stdout=json.dumps(
                        {"format": {"duration": "2.0"}}), stderr="")
                out = command[-1]
                with open(out, "wb") as handle:
                    handle.write(b"jpg")
                return completed

            with mock.patch.object(ocr_check, "_ffmpeg_bins", return_value=("ff", "probe")), \
                    mock.patch.object(ocr_check.subprocess, "run", side_effect=fake_run) as run:
                frames, temp, meta = ocr_check.extract_frames(video, return_metadata=True)
            try:
                self.assertEqual(len(frames), 12)
                self.assertEqual(meta["expected"], 12)
                seeks = [call.args[0][call.args[0].index("-ss") + 1]
                         for call in run.call_args_list[1:]]
                self.assertEqual(seeks[0], "0.000")
                self.assertEqual(seeks[-1], "1.833")
            finally:
                import shutil
                shutil.rmtree(temp, ignore_errors=True)

    def test_unavailable_and_errors_are_not_clear(self):
        with mock.patch.object(ocr_check.sys, "platform", "linux"):
            report = ocr_check.check_video("missing.mp4")
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["available"])
        with mock.patch.object(ocr_check.sys, "platform", "darwin"), \
                mock.patch.dict(sys.modules, {"Vision": mock.Mock()}), \
                mock.patch.object(ocr_check, "extract_frames", side_effect=RuntimeError("boom")):
            report = ocr_check.check_video("bad.mp4")
        self.assertEqual(report["status"], "error")
        self.assertNotEqual(report["status"], "clear")

    def test_manual_review_is_bound_and_attributed(self):
        frames = ["%064x" % index for index in range(1, 13)]
        record = ocr_check.manual_review(
            "take-fp", "alice", "逐帧检查", "clear", frame_sha256s=frames)
        self.assertEqual(record["take_fingerprint"], "take-fp")
        self.assertEqual(record["reviewer"], "alice")
        with self.assertRaisesRegex(ValueError, "文字"):
            ocr_check.manual_review(
                "take-fp", "alice", "review", "detected", frame_sha256s=frames)
        with self.assertRaisesRegex(ValueError, "12"):
            ocr_check.manual_review("take-fp", "alice", "review", "clear")


class FormalTakeReviewTests(unittest.TestCase):
    def _review(self, directory):
        video = os.path.join(directory, "take.mp4")
        with open(video, "wb") as handle:
            handle.write(b"video")
        take = {"segment_id": "s1", "taskId": "task", "localPath": video,
                "video_handoff_fingerprint": "handoff"}
        return take_review.create_review(take, {"id": "s1"})

    def _complete(self, review):
        fp = review["artifact"]["take_fingerprint"]
        observation = {"take_fingerprint": fp, "quality": {
            "technical": {"video_integrity": True, "audio_integrity": True},
            "marketing": {"lip_sync": 90, "identity_fidelity": 90,
                          "product_fidelity": 90, "script_fidelity": 90},
            "overall_score": 85}}
        review = take_review.import_observation(
            review, observation, {"source_type": "human", "reviewer": "alice"})
        return review

    def test_formal_acceptance_rejects_empty_review(self):
        with tempfile.TemporaryDirectory() as directory:
            review = self._review(directory)
            with self.assertRaisesRegex(take_review.ReviewGateError, "REVIEW_SOURCES"):
                take_review.decide(review, "accepted", "lead", "approved")
            accepted = take_review.decide(
                review, "accepted", "lead", "draft only", draft_acceptance=True)
            self.assertEqual(accepted["decision"]["acceptance_mode"], "draft")
            forged_formal = dict(accepted)
            forged_formal["decision"] = dict(accepted["decision"], acceptance_mode="formal")
            self.assertIn("REVIEW_SOURCES_REQUIRED",
                          take_review.validation_problems(forged_formal))

    def test_formal_acceptance_checks_score_ocr_and_current_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            review = self._complete(self._review(directory))
            accepted = take_review.decide(review, "accepted", "lead", "approved")
            self.assertTrue(take_review.is_accepted(accepted))
            with open(review["artifact"]["local_path"], "ab") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(take_review.ReviewGateError, "STALE_ARTIFACT"):
                take_review.decide(review, "accepted", "lead", "approved")

    def test_formal_acceptance_rejects_false_quality_values(self):
        with tempfile.TemporaryDirectory() as directory:
            review = self._complete(self._review(directory))
            review["quality"]["technical"]["audio_integrity"] = False
            with self.assertRaisesRegex(take_review.ReviewGateError, "TECHNICAL_REVIEW_FAILED"):
                take_review.decide(review, "accepted", "lead", "approved")
            review = self._complete(self._review(directory))
            review["quality"]["marketing"]["lip_sync"] = False
            with self.assertRaisesRegex(take_review.ReviewGateError, "MARKETING_REVIEW_FAILED"):
                take_review.decide(review, "accepted", "lead", "approved")


class ManifestTakeGateTests(unittest.TestCase):
    def test_accept_take_requires_exact_handoff_and_clear_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "take.mp4")
            with open(video, "wb") as handle:
                handle.write(b"video")
            take = {"segment_id": "s1", "taskId": "task", "localPath": video,
                    "video_handoff_fingerprint": "handoff"}
            review = take_review.create_review(take, {"id": "s1"})
            fp = review["artifact"]["take_fingerprint"]
            observation = {"take_fingerprint": fp, "quality": {
                "technical": {"video_integrity": True, "audio_integrity": True},
                "marketing": {"lip_sync": 90, "identity_fidelity": 90,
                              "product_fidelity": 90, "script_fidelity": 90},
                "overall_score": 90}}
            review = take_review.import_observation(
                review, observation, {"source_type": "human", "reviewer": "lead"})
            review = take_review.decide(review, "accepted", "lead", "approved")
            manifest = run_manifest.create_manifest("acme", "run1")
            manifest["handoffs"]["video"] = {"segments": {"s1": "handoff"}}
            run_manifest.record_ocr_result(
                manifest, "s1", fp, "clear", available=True,
                frames_checked=12, expected=12)
            item = run_manifest.accept_take(manifest, "s1", review)
            self.assertEqual(item["take_fingerprint"], fp)
            with self.assertRaisesRegex(ValueError, "SEGMENT"):
                run_manifest.accept_take(manifest, "other", review)
            forged = dict(review)
            forged["artifact"] = dict(review["artifact"], take_fingerprint="forged")
            run_manifest.record_ocr_result(
                manifest, "s1", "forged", "clear", available=True,
                frames_checked=12, expected=12)
            with self.assertRaisesRegex(ValueError, "STALE_TAKE_ARTIFACT"):
                run_manifest.accept_take(manifest, "s1", forged)

    def test_unavailable_and_legacy_false_never_pass_as_clear(self):
        manifest = run_manifest.create_manifest("acme", "run1")
        run_manifest.record_ocr_result(manifest, "s1", "fp", False)
        self.assertFalse(run_manifest.ocr_take_is_clear_or_waived(manifest, "s1", "fp"))

    def test_manifest_manual_review_is_attributed_and_bound(self):
        manifest = run_manifest.create_manifest("acme", "run1")
        record = run_manifest.record_manual_ocr_review(
            manifest, "s1", "fp", "clear", reviewer="alice", reason="逐帧复核",
            frame_sha256s=["%064x" % index for index in range(1, 13)])
        self.assertEqual(record["source"], "manual")
        self.assertTrue(run_manifest.ocr_take_is_clear_or_waived(manifest, "s1", "fp"))
        self.assertFalse(run_manifest.ocr_take_is_clear_or_waived(manifest, "s1", "other"))
        run_manifest.record_ocr_result(
            manifest, "s1", "fp", "error", available=False, error="failed")
        self.assertFalse(run_manifest.ocr_take_is_clear_or_waived(manifest, "s1", "fp"))


if __name__ == "__main__":
    unittest.main()
