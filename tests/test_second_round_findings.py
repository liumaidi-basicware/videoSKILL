import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import artifact_contract as ac  # noqa: E402
import run_manifest as rm  # noqa: E402
import seedance_prompt  # noqa: E402
import script_splitter as ss  # noqa: E402
import take_review  # noqa: E402
import video_engine as ve  # noqa: E402


class ControlledAttemptTests(unittest.TestCase):
    def test_new_attempt_invalidates_accepted_take_and_old_success(self):
        manifest = rm.create_manifest("acme", "run1")
        manifest["handoffs"]["video"] = {"segments": {"s1": "h1"}}
        manifest["accepted_takes"]["s1"] = {"take_fingerprint": "old"}
        rm.upsert_task(manifest, {"stage": "video", "unit_id": "s1",
                                 "handoff_fingerprint": "h1", "attempt": 1,
                                 "task_id": "old-task", "status": "succeeded",
                                 "video_url": "https://x/old.mp4"})
        item = rm.request_video_attempt(
            manifest, "s1", actor="lead", reason="customer requested another take")
        self.assertEqual(item["attempt"], 2)
        self.assertNotIn("s1", manifest["accepted_takes"])
        self.assertIsNone(ve._completed_task(manifest, "s1", "h1"))
        first = ve._submission_request_id({"id": "s1"}, "m", 1, "h1", 1)
        second = ve._submission_request_id({"id": "s1"}, "m", 1, "h1", 2)
        self.assertNotEqual(first, second)

    def test_attempt_requires_terminal_review_or_acceptance(self):
        manifest = rm.create_manifest("acme", "run1")
        manifest["handoffs"]["video"] = {"segments": {"s1": "h1"}}
        with self.assertRaisesRegex(ValueError, "ACCEPTED_OR_REJECTED"):
            rm.request_video_attempt(manifest, "s1", actor="lead", reason="retry")


class DependencyAndPlanTests(unittest.TestCase):
    def test_chain_identity_binds_task_take_tail_and_extend_source(self):
        with tempfile.TemporaryDirectory() as directory:
            tail = os.path.join(directory, "tail.png")
            with open(tail, "wb") as handle:
                handle.write(b"tail")
            first = ac.build_generation_dependency(
                {"segment_id": "s1", "taskId": "task-1", "take_fingerprint": "take-1"},
                tail_path=tail, extend_source="https://x/one.mp4")
            second = ac.build_generation_dependency(
                {"segment_id": "s1", "taskId": "task-2", "take_fingerprint": "take-1"},
                tail_path=tail, extend_source="https://x/one.mp4")
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])
            self.assertEqual(first["payload"]["tail_sha256"], ac.file_sha256(tail))

    def test_render_plan_bytes_and_content_are_handoff_bound_and_prompted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "render.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"mode": "cinematic", "fusion": "type5"}, handle)
            record = rm.file_record(path)
            segment = ss.split(
                {"shots": [{"id": "s1", "duration": 3, "visual": "product",
                             "asset_refs": {"product_images": ["product.png"]}}]},
                client="acme", allow_unconfirmed=True,
                render_plan_artifact=record)["segments"][0]
            self.assertEqual(segment["render_plan"]["sha256"], record["sha256"])
            prompt = seedance_prompt.compile_prompt(segment)
            self.assertIn("cinematic", prompt)
            old_handoff = segment["video_handoff_fingerprint"]
            segment["render_plan"]["content"]["mode"] = "flat"
            self.assertNotEqual(old_handoff, ac.build_video_handoff(segment)["fingerprint"])


class ApprovalAndAudioTests(unittest.TestCase):
    def test_generic_video_finish_and_incomplete_approval_are_blocked(self):
        manifest = rm.create_manifest("acme", "run1")
        with self.assertRaisesRegex(ValueError, "SPECIALIZED_FINISH"):
            rm.mark_generation_finished(manifest, "video", [__file__])
        with self.assertRaisesRegex(ValueError, "VIDEO_APPROVAL"):
            rm.approve(manifest, "video", approved=True, strict=False)

    def test_full_audio_contract_is_in_prompt_and_qc_marks_human_validation(self):
        contract = {"track": "required", "speech": True, "dialogue": "hello",
                    "language": "en-GB", "voice": "warm", "bgm": "light",
                    "sfx": "click", "lip_sync": True}
        prompt = seedance_prompt.compile_prompt(
            {"duration": 3, "dialogue": "hello", "audio_contract": contract})
        for value in ("en-GB", "warm", "light", "click"):
            self.assertIn(value, prompt)
        with mock.patch.object(ve.media_qc, "check",
                               return_value={"passed": True, "media": {}}):
            report = ve._media_qc_guard("/tmp/take.mp4", {"audio_contract": contract},
                                        draft=True)
        self.assertEqual(report["audio_contract"], contract)
        self.assertEqual(set(report["semantic_audio_review_required"]),
                         {"voice", "language", "bgm", "sfx", "lip_sync"})


class FormalSingleAndFallbackTests(unittest.TestCase):
    def test_formal_single_consumes_exact_handoff_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            segment = {"id": "s1", "text": "real", "dialogue": "real",
                       "audio_contract": {"track": "required", "dialogue": "real"},
                       "duration": 7, "video_type": 5, "urls": ["real.png"],
                       "ratio": "16:9", "resolution": "1080p",
                       "out_path": os.path.join(directory, "real.mp4")}
            segment["video_handoff_fingerprint"] = ac.build_video_handoff(segment)["fingerprint"]
            spec_path = os.path.join(directory, "segments.json")
            with open(spec_path, "w", encoding="utf-8") as handle:
                json.dump({"segments": [segment]}, handle)
            manifest = rm.create_manifest("acme", "run1")
            manifest["handoffs"]["video"] = {
                "segments": {"s1": segment["video_handoff_fingerprint"]},
                "file": rm.file_record(spec_path)}
            manifest_path = os.path.join(directory, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
                    mock.patch.object(ve, "render_batch", return_value=[{
                        "ok": True, "videoUrl": "https://x/v.mp4", "localPath": segment["out_path"]
                    }]) as render_batch, contextlib.redirect_stdout(io.StringIO()):
                code = ve.main(["--text", "forged", "--client", "acme",
                                "--manifest", manifest_path, "--results-out",
                                os.path.join(directory, "results.json")])
            self.assertEqual(code, 0)
            self.assertEqual(render_batch.call_args.args[0][0], segment)

    def test_poll_privacy_fallback_uses_canonical_picker(self):
        segments = [{"id": "s1", "text": "A", "video_type": 5,
                     "urls": ["human.png"], "duration": 3}]
        states = iter([{"status": "failed", "message": "上传的参考图检测到真实人物，因隐私保护不支持生成"},
                       {"status": "succeeded", "videoUrl": "https://x/v.mp4"}])
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk"), \
                mock.patch.object(ve, "_pick_video_model", side_effect=[
                    "seedance-2.0", "seedance-2.0", "canonical/kling"]) as pick, \
                mock.patch.object(ve.br_client, "to_image_ref", side_effect=lambda value, **_: value), \
                mock.patch.object(ve.br_client, "create_video", side_effect=["t1", "t2"]), \
                mock.patch.object(ve.br_client, "get_video", side_effect=lambda *_: next(states)), \
                mock.patch("time.sleep"):
            result = ve.render_batch(segments, draft=True, verbose=False)
        self.assertTrue(result[0]["ok"])
        self.assertEqual(pick.call_args_list[-1].args[0], "kling-v3-omni-video")
        self.assertEqual(result[0]["model"], "canonical/kling")


if __name__ == "__main__":
    unittest.main()
