import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import final_edit  # noqa: E402
import run_manifest  # noqa: E402
import script_splitter  # noqa: E402
import subtitle_overlay  # noqa: E402


class CaptionFinalManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="caption_manifest_")
        self.manifest_path = os.path.join(self.root, "run.json")
        self.manifest = run_manifest.create_manifest("acme", "run-1")
        self._approval_patch = mock.patch.object(
            run_manifest, "approval_is_current",
            side_effect=lambda manifest, stage: True)
        self._approval_patch.start()
        for stage in ("brief", "script", "cast_board", "storyboard", "render_plan"):
            path = self.write("%s.bin" % stage, stage.encode())
            run_manifest.mark_generation_finished(self.manifest, stage, [path])
            run_manifest.approve(self.manifest, stage, strict=True)
        self.storyboard_approval = {
            "status": "confirmed", "client": "acme", "run_id": "run-1",
            "plan_fingerprint": "plan-fp", "result_json": "storyboard_result.json",
            "out_dir": self.root,
        }
        self.segment = {"id": "s1", "client": "acme", "run_id": "run-1",
                        "duration": 3, "dialogue": "hello",
                        "storyboard_approval": self.storyboard_approval}
        from artifact_contract import build_video_handoff
        self.segment["video_handoff_fingerprint"] = build_video_handoff(
            self.segment)["fingerprint"]
        self.handoff = self.segment["video_handoff_fingerprint"]
        self.segments_path = self.write_json("segments.json", {
            "client": "acme", "run_id": "run-1", "segments": [self.segment],
            "storyboard_approval": self.storyboard_approval,
            "missing_images": [], "needs_image": []})
        run_manifest.record_video_handoff(
            self.manifest, {"client": "acme", "run_id": "run-1",
                            "segments": [self.segment],
                            "storyboard_approval": self.storyboard_approval,
                            "missing_images": [], "needs_image": []},
            self.segments_path)
        self.basecut = self.write("basecut.mp4", b"basecut")
        derived = script_splitter.derive_captions({"segments": [self.segment]})
        self.lines = self.write_json("lines.json", derived["lines"])
        self.srt = self.write("subs.srt", derived["srt"].encode())
        self.motion = self.write_json("motion.json", derived["motion_plan"])
        self.caption_manifest = os.path.join(self.root, "captions.manifest.json")
        self.artifact = script_splitter.persist_caption_artifact(
            self.manifest, self.manifest_path, client="acme",
            segments_path=self.segments_path, basecut_path=self.basecut,
            lines_path=self.lines, srt_path=self.srt, motion_path=self.motion,
            caption_manifest_path=self.caption_manifest)

    def tearDown(self):
        self._approval_patch.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name, data):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def write_json(self, name, value):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        return path

    def approve_captions(self):
        self.artifact = script_splitter.confirm_captions(
            self.manifest, self.manifest_path, self.caption_manifest, client="acme")

    def test_caption_confirmation_is_content_bound(self):
        self.approve_captions()
        self.assertTrue(script_splitter.caption_artifact_is_current(
            self.manifest, self.artifact, client="acme"))
        with open(self.lines, "w", encoding="utf-8") as handle:
            json.dump([{"text": "changed", "start": 0, "end": 3}], handle)
        with self.assertRaisesRegex(ValueError, "STALE_CAPTION_ARTIFACT"):
            script_splitter.caption_artifact_is_current(
                self.manifest, self.artifact, client="acme")

    def test_subtitle_render_is_separate_from_timeline_approval(self):
        self.approve_captions()
        out = self.write("captioned.mp4", b"captioned")
        subtitle_overlay.formal_caption_gate(
            self.manifest, self.artifact, client="acme",
            video_path=self.basecut, lines_path=self.lines)
        record = subtitle_overlay.record_caption_render(
            self.manifest, self.manifest_path, self.artifact, out)
        self.assertEqual(record["status"], "pending_final_approval")
        self.assertTrue(run_manifest.approval_is_current(self.manifest, "captions"))

    def test_ocr_waiver_cannot_transfer_to_another_take_or_text(self):
        run_manifest.record_ocr_result(self.manifest, "s1", "take-a", True, ["logo"])
        run_manifest.grant_ocr_waiver(
            self.manifest, "s1", "take-a", ["logo"], actor="client", reason="包装原字")
        self.assertTrue(run_manifest.ocr_take_is_clear_or_waived(
            self.manifest, "s1", "take-a"))
        self.assertFalse(run_manifest.ocr_take_is_clear_or_waived(
            self.manifest, "s1", "take-b"))
        run_manifest.record_ocr_result(self.manifest, "s1", "take-a", True, ["other"])
        self.assertFalse(run_manifest.ocr_take_is_clear_or_waived(
            self.manifest, "s1", "take-a"))

    def test_formal_assemble_persists_clean_take_ocr(self):
        result_video = self.write("take.mp4", b"take")
        results = [{"ok": True, "segment_id": "s1", "localPath": result_video,
                    "video_handoff_fingerprint": self.handoff, "take_fingerprint": "take-a",
                    "ocr_warning": False, "ocr_texts": [], "ocr_status": "clear",
                    "ocr_available": True, "ocr_frames_checked": 12, "ocr_expected": 12}]
        self.assertTrue(script_splitter.persist_and_gate_assemble_ocr(
            self.manifest, self.manifest_path,
            {"segments": [self.segment]}, results, client="acme"))
        self.assertTrue(run_manifest.ocr_take_is_clear_or_waived(
            self.manifest, "s1", "take-a"))

    def test_formal_assemble_requires_exact_warning_waiver(self):
        result_video = self.write("take.mp4", b"take")
        results = [{"ok": True, "segment_id": "s1", "localPath": result_video,
                    "video_handoff_fingerprint": self.handoff, "take_fingerprint": "take-a",
                    "ocr_warning": True, "ocr_texts": ["logo"]}]
        with self.assertRaisesRegex(ValueError, "OCR_EXACT_WAIVER_REQUIRED"):
            script_splitter.persist_and_gate_assemble_ocr(
                self.manifest, self.manifest_path,
                {"segments": [self.segment]}, results, client="acme")
        run_manifest.grant_ocr_waiver(
            self.manifest, "s1", "take-a", ["logo"], actor="client", reason="包装原字")
        self.assertTrue(script_splitter.persist_and_gate_assemble_ocr(
            self.manifest, self.manifest_path,
            {"segments": [self.segment]}, results, client="acme"))

    def test_final_gate_requires_exact_take_ocr_and_caption_render(self):
        self.approve_captions()
        captioned = self.write("captioned.mp4", b"captioned")
        subtitle_overlay.record_caption_render(
            self.manifest, self.manifest_path, self.artifact, captioned)
        self.manifest["accepted_takes"]["s1"] = {
            "take_fingerprint": "take-a", "video_handoff_fingerprint": self.handoff}
        with self.assertRaisesRegex(ValueError, "OCR_CLEAN"):
            final_edit.formal_final_gate(
                self.manifest, self.artifact, client="acme", basecut_path=captioned)
        run_manifest.record_ocr_result(
            self.manifest, "s1", "take-a", "clear", [], available=True,
            frames_checked=12, expected=12)
        self.assertTrue(final_edit.formal_final_gate(
            self.manifest, self.artifact, client="acme", basecut_path=captioned))

    def test_final_generation_finishes_pending_approval(self):
        self.approve_captions()
        captioned = self.write("captioned.mp4", b"captioned")
        scheme = self.write_json("scheme.json", {"shots": []})
        output = self.write("final.mp4", b"final")
        qc = {"passed": True, "file": {"path": output,
              "sha256": run_manifest.file_record(output)["sha256"]},
              "media": {"actual_duration": 3.0}, "report_path": output + ".qc.json"}
        record = final_edit.record_final_generation(
            self.manifest, self.manifest_path, scheme_path=scheme,
            basecut_path=captioned, caption_artifact=self.artifact, out_path=output,
            media_qc_report=qc)
        self.assertEqual(record["status"], "pending_approval")
        self.assertFalse(self.manifest["approvals"]["final"])
        self.assertEqual(record["media_qc"]["file"]["sha256"], qc["file"]["sha256"])
        self.assertEqual(self.manifest["delivery_qc"], qc)

    def test_final_generation_does_not_finish_when_qc_fails(self):
        captioned = self.write("captioned.mp4", b"captioned")
        scheme = self.write_json("scheme.json", {"shots": []})
        output = self.write("final.mp4", b"invalid")
        failed = {"passed": False, "errors": ["COMPLETE_DECODE_FAILED"],
                  "file": run_manifest.file_record(output)}
        before = self.manifest.get("generation", {}).get("final")
        with self.assertRaisesRegex(ValueError, "MEDIA_QC_FAILED"):
            final_edit.record_final_generation(
                self.manifest, self.manifest_path, scheme_path=scheme,
                basecut_path=captioned, caption_artifact=self.artifact, out_path=output,
                media_qc_report=failed)
        self.assertEqual(self.manifest.get("generation", {}).get("final"), before)

    def test_captioned_basecut_rejects_scheme_subtitles(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SUBTITLES"):
            final_edit.reject_duplicate_subtitles({
                "subtitles": [{"start_sec": 0, "end_sec": 1, "text": "duplicate"}]})
        self.assertIsNone(final_edit.reject_duplicate_subtitles({"shots": []}))

    def test_strict_timeline_cannot_exceed_probed_basecut(self):
        basecut = self.write("real.mp4", b"placeholder")
        scheme = {"fps": 30, "shots": [{"start_sec": 0.5, "end_sec": 2.1}]}
        from unittest.mock import patch
        with patch.object(final_edit, "_probe_duration", return_value=2.0):
            with self.assertRaisesRegex(ValueError, "SHOT_EXCEEDS_BASECUT"):
                final_edit.compile_shotlist(
                    scheme, basecut, require_basecut_duration=True)


if __name__ == "__main__":
    unittest.main()
