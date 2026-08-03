#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import artifact_contract as ac  # noqa: E402
import continuity_state as cs  # noqa: E402
import generation_ledger as gl  # noqa: E402
import run_manifest as rm  # noqa: E402
import script_splitter as ss  # noqa: E402
import take_review as tr  # noqa: E402
import video_engine as ve  # noqa: E402


class ArtifactContractTests(unittest.TestCase):
    def test_handoff_detects_same_path_content_change(self):
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "ref.png")
            with open(image, "wb") as handle:
                handle.write(b"one")
            segment = {"id": "s1", "text": "x", "duration": 5, "ratio": "16:9",
                       "resolution": "1080p", "video_type": 2,
                       "references": [{"id": "r1", "url": image,
                                       "type": "product_identity", "scope": "scene"}]}
            segment["video_handoff_fingerprint"] = ac.build_video_handoff(segment)["fingerprint"]
            self.assertTrue(ac.verify_video_handoff(segment)["ok"])
            with open(image, "wb") as handle:
                handle.write(b"two")
            self.assertFalse(ac.verify_video_handoff(segment)["ok"])


class LedgerAndManifestTests(unittest.TestCase):
    def test_ledger_ignores_partial_last_line(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "generation_runs.jsonl")
            gl.append_event(path, "task_submitted", unit_id="s1", task_id="t1",
                            handoff_fingerprint="h1")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write('{"broken"')
            self.assertEqual(len(gl.read_events(path)), 1)

    def test_task_upsert_and_resume(self):
        manifest = rm.create_manifest("acme", "run1")
        task = {"stage": "video", "unit_id": "s1", "handoff_fingerprint": "h1",
                "attempt": 1, "task_id": "t1", "status": "submitted"}
        rm.upsert_task(manifest, task)
        rm.upsert_task(manifest, dict(task, status="running"))
        self.assertEqual(len(manifest["tasks"]), 1)
        self.assertEqual(rm.find_resumable_task(manifest, "video", "s1", "h1")["task_id"], "t1")


class TakeReviewTests(unittest.TestCase):
    def _review(self):
        result = {"segment_id": "s1", "taskId": "t1", "videoUrl": "https://x/v.mp4",
                  "video_handoff_fingerprint": "h1"}
        return tr.create_review(result, {"id": "s1", "scene_id": "studio"})

    def test_immutable_issue_blocks_acceptance(self):
        review = self._review()
        tr.add_issue(review, "immutable_error", "WRONG_PRODUCT", "wrong")
        with self.assertRaisesRegex(tr.ReviewGateError, "IMMUTABLE"):
            tr.decide(review, "accepted", "lead", "no", draft_acceptance=True)

    def test_warning_requires_explicit_acceptance(self):
        review = self._review()
        tr.add_issue(review, "transient_warning", "LIGHT_DRIFT", "minor")
        issue_id = review["issues"]["transient_warnings"][0]["issue_id"]
        with self.assertRaisesRegex(tr.ReviewGateError, "UNACKNOWLEDGED"):
            tr.decide(review, "accepted", "lead", "ok", draft_acceptance=True)
        accepted = tr.decide(review, "accepted", "lead", "ok", [issue_id],
                             draft_acceptance=True)
        self.assertTrue(tr.is_accepted(accepted))


class ContinuityTests(unittest.TestCase):
    def _state(self, td):
        state = cs.create_state("project", "run", {"max_chain_depth": 1})
        return cs.register_scene_anchor(state, "studio", {
            "approved": True, "reference_urls": [os.path.join(td, "anchor.png")],
            "reference_roles": ["scene_environment"]})

    def test_only_accepted_parent_can_continue_and_depth_reanchors(self):
        with tempfile.TemporaryDirectory() as td:
            frame = os.path.join(td, "tail.png")
            with open(frame, "wb") as handle:
                handle.write(b"tail")
            state = self._state(td)
            root = {"id": "s1", "scene_id": "studio"}
            root_plan = cs.plan_generation(state, root)
            result = {"ok": True, "take_id": "s1-take-01", "take_fingerprint": "tf1"}
            state = cs.register_take(state, root, result, root_plan)
            pending = tr.create_review({"segment_id": "s1", "take_id": "s1-take-01",
                                        "take_fingerprint": "tf1"}, root)
            with self.assertRaisesRegex(cs.ContinuityGateError, "PARENT_NOT_ACCEPTED"):
                cs.plan_generation(state, {"id": "s2", "scene_id": "studio"},
                                   "s1-take-01", pending)
            pending["observed_end_state"] = {"frame_path": frame,
                                              "frame_sha256": ac.file_sha256(frame), "state": {}}
            accepted = tr.decide(pending, "accepted", "lead", "ok", require_end_state=True,
                                 draft_acceptance=True)
            state = cs.accept_take(state, accepted)
            child = cs.plan_generation(state, {"id": "s2", "scene_id": "studio"},
                                       "s1-take-01", accepted)
            self.assertEqual(child["mode"], "tail_frame")
            self.assertEqual(child["next_chain_depth"], 1)

    def test_scene_boundary_uses_reanchor_not_tail(self):
        state = cs.create_state("project", "run")
        state = cs.register_scene_anchor(state, "street", {"approved": True,
                                         "reference_urls": ["street.png"]})
        state["takes"]["old"] = {"scene_id": "studio", "take_fingerprint": "x"}
        plan = cs.plan_generation(state, {"id": "s2", "scene_id": "street"}, "old", {})
        self.assertEqual(plan["mode"], "scene_reanchor")
        self.assertEqual(plan["reanchor_reason"], "scene_boundary")

    def test_state_comparison_blocks_identity_and_warns_pose(self):
        blocked = cs.compare_states(
            {"character": {"character_id": "a", "pose": "standing"}},
            {"character": {"character_id": "b", "pose": "sitting"}})
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["code"], "IMMUTABLE_STATE_MISMATCH")
        self.assertEqual(blocked["warnings"][0]["code"], "TRANSIENT_STATE_MISMATCH")


class VideoResumeIntegrationTests(unittest.TestCase):
    def test_resumable_manifest_task_is_polled_without_resubmit(self):
        segment = {"id": "s1", "text": "x", "duration": 5, "video_type": 1,
                   "ratio": "16:9", "resolution": "1080p", "out_path": None}
        handoff = ac.build_video_handoff(segment)["fingerprint"]
        segment["video_handoff_fingerprint"] = handoff
        manifest = rm.create_manifest("acme", "run1")
        rm.upsert_task(manifest, {"stage": "video", "unit_id": "s1",
                                 "handoff_fingerprint": handoff, "attempt": 1,
                                 "task_id": "existing", "model": "seedance-2.0",
                                 "status": "submitted"})
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
             mock.patch.object(ve.br_client, "create_video") as submit, \
             mock.patch.object(ve.br_client, "get_video", return_value={
                 "status": "succeeded", "videoUrl": "https://x/v.mp4"}):
            result = ve.render_batch([segment], manifest=manifest, verbose=False, draft=True)
        submit.assert_not_called()
        self.assertTrue(result[0]["ok"])
        self.assertEqual(result[0]["taskId"], "existing")

    def test_strict_assemble_requires_review(self):
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "s1.mp4")
            with open(video, "wb") as handle:
                handle.write(b"video")
            segment = {"id": "s1", "out_path": video, "take_review_required": True,
                       "video_handoff_fingerprint": "h1"}
            result = {"ok": True, "localPath": video, "segment_id": "s1",
                       "video_handoff_fingerprint": "h1", "review_status": "pending"}
            result["take_fingerprint"] = tr.take_fingerprint(result)
            with self.assertRaisesRegex(RuntimeError, "TAKE_REVIEW_REQUIRED"):
                ss.assemble([segment], [result], os.path.join(td, "final.mp4"))

    def test_chain_result_is_bound_to_pending_review(self):
        segment = {"id": "s1", "text": "x", "duration": 5, "video_type": 1,
                   "ratio": "16:9", "resolution": "1080p", "out_path": None}
        segment["video_handoff_fingerprint"] = ac.build_video_handoff(segment)["fingerprint"]
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
             mock.patch.object(ve.br_client, "create_video", return_value="task"), \
             mock.patch.object(ve.br_client, "wait_video", return_value="https://x/v.mp4"):
            result = ve.render_chained([segment], verbose=False, draft=True)[0]
        self.assertEqual(result["segment_id"], "s1")
        self.assertEqual(result["review_status"], "pending")
        self.assertEqual(result["video_handoff_fingerprint"],
                         segment["video_handoff_fingerprint"])


if __name__ == "__main__":
    unittest.main()
