import json
import os
import sys
import tempfile
import unittest
import contextlib
import io
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import video_engine as ve  # noqa: E402


class VideoManifestFailClosedTests(unittest.TestCase):
    def test_submission_request_id_is_stable_for_same_handoff(self):
        segment = {"id": "s1", "text": "hello"}
        first = ve._submission_request_id(segment, "seedance-2.0", 1, "handoff", 1)
        second = ve._submission_request_id(dict(segment), "seedance-2.0", 1, "handoff", 1)
        retry = ve._submission_request_id(segment, "kling-v3-omni-video", 1, "handoff", 2)
        self.assertEqual(first, second)
        self.assertNotEqual(first, retry)

    def test_expired_url_refreshes_same_task_without_new_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "video.mp4")
            expired = ve.br_client.BRError("download HTTP 403", http_status=403)
            with mock.patch.object(
                    ve.br_client, "download", side_effect=[expired, out]) as download, \
                    mock.patch.object(ve.br_client, "get_video", return_value={
                        "status": "succeeded", "videoUrl": "https://cdn.example/new.mp4"}) as get_video, \
                    mock.patch.object(ve.br_client, "create_video") as create:
                result = ve._download_completed_video(
                    "sk-test", "task-1", "https://cdn.example/old.mp4", out)
        self.assertEqual(result, out)
        get_video.assert_called_once_with("sk-test", "task-1")
        create.assert_not_called()
        self.assertEqual(download.call_args_list[1].args[0], "https://cdn.example/new.mp4")

    def test_completed_video_download_allows_configured_proxy_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "video.mp4")
            with mock.patch.object(ve.br_client, "download", return_value=out) as download:
                result = ve._download_completed_video(
                    "sk-test", "task-1", "https://cdn.example/video.mp4", out)
        self.assertEqual(result, out)
        self.assertTrue(download.call_args.kwargs["allow_nonpublic_peer"])

    def test_batch_requires_manifest_by_default(self):
        with self.assertRaisesRegex(ValueError, "FORMAL_VIDEO_REQUIRES"):
            ve.render_batch([], client="acme", verbose=False)

    def test_formal_batch_requires_prompt_review_after_manifest_args(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "run_manifest.json")
            results_path = os.path.join(directory, "batch_results.json")
            manifest = {"handoffs": {"video": {"segments": {}}}}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_REQUIRED"):
                ve.render_batch([], client="acme", verbose=False,
                                manifest=manifest, manifest_path=manifest_path,
                                results_out=results_path)

    def test_formal_chain_requires_prompt_review_after_manifest_args(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "run_manifest.json")
            results_path = os.path.join(directory, "batch_results.json")
            manifest = {"handoffs": {"video": {"segments": {}}}}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with self.assertRaisesRegex(ValueError, "PROMPT_REVIEW_REQUIRED"):
                ve.render_chained([], client="acme", verbose=False,
                                  manifest=manifest, manifest_path=manifest_path,
                                  results_out=results_path)

    def test_cli_formal_single_requires_prompt_review_before_manifest_read(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "missing.json")
            results_path = os.path.join(directory, "single_results.json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = ve.main(["--text", "hello", "--client", "acme",
                                "--manifest", manifest_path,
                                "--results-out", results_path])
            self.assertEqual(code, 2)
            self.assertIn("--prompt-review", out.getvalue())

    def test_draft_preserves_legacy_function_entry(self):
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"):
            self.assertEqual(ve.render_batch([], verbose=False, draft=True), [])

    def test_manifest_handoff_must_match_exactly(self):
        segment = {"id": "s1", "video_handoff_fingerprint": "new"}
        manifest = {"handoffs": {"video": {"segments": {"s1": "old"}}}}
        with self.assertRaisesRegex(ValueError, "VIDEO_HANDOFF_MISMATCH"):
            ve._manifest_handoff_matches(manifest, [segment])

    def test_formal_video_trusts_typed_generated_remote_references(self):
        segment = {
            "id": "s1",
            "urls": [
                "https://cdn.example/product-board.png",
                "https://cdn.example/storyboard.png",
            ],
            "references": [
                {"url": "https://cdn.example/product-board.png",
                 "source": "asset_refs.product_boards",
                 "type": "product_board"},
                {"url": "https://cdn.example/storyboard.png",
                 "source": "storyboard",
                 "type": "storyboard_composition"},
            ],
        }
        ve._validate_references([segment], "acme", manifest={})

    def test_formal_render_batch_trusts_typed_generated_remote_references(self):
        with tempfile.TemporaryDirectory() as directory:
            product_url = "https://cdn.example/product-board.png"
            segment = {
                "id": "s1",
                "text": "hello",
                "dialogue": "hello",
                "urls": [product_url],
                "references": [{
                    "id": "ref_01",
                    "url": product_url,
                    "source": "asset_refs.product_boards",
                    "type": "product_board",
                    "scope": "scene",
                    "tag": "@product_hero",
                }],
                "required_reference_types": ["product_board"],
                "out_path": os.path.join(directory, "s1.mp4"),
            }
            handoff = ve.artifact_contract.build_video_handoff(segment)["fingerprint"]
            segment["video_handoff_fingerprint"] = handoff
            manifest_path = os.path.join(directory, "run_manifest.json")
            results_path = os.path.join(directory, "results.json")
            review_path = os.path.join(directory, "review.json")
            manifest = {"client": "acme",
                        "handoffs": {"video": {"segments": {"s1": handoff}}}}
            review = {"status": "confirmed", "stage": "video",
                      "prompts": [{"shot_id": "s1",
                                   "submission_prompt_zh": "hello",
                                   "model": "seedance-2.0"}]}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with open(review_path, "w", encoding="utf-8") as handle:
                json.dump(review, handle)
            submitted = []

            def fake_submit(_api_key, _segment, _model, _video_type, ref_urls,
                            _negative_prompt, **_kwargs):
                submitted.append(ref_urls)
                return "task-1", "hello"

            with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
                 mock.patch.object(ve._rm, "identity_gate", return_value=None), \
                 mock.patch.object(ve._rm, "generation_gate", return_value=None), \
                 mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
                 mock.patch.object(ve, "_submit_video", side_effect=fake_submit), \
                 mock.patch.object(ve.br_client, "get_video",
                                   return_value={"status": "failed", "error": "remote failed"}), \
                 mock.patch("time.sleep"):
                result = ve.render_batch(
                    [segment], client="acme", verbose=False,
                    manifest=manifest, manifest_path=manifest_path,
                    results_out=results_path, prompt_review=review_path,
                    max_wait=0)
        self.assertEqual(submitted, [[product_url]])
        self.assertFalse(result[0]["ok"])
        self.assertNotIn("UNTRUSTED_VIDEO_REFERENCE", result[0]["error"])

    def test_formal_video_rejects_untyped_remote_references(self):
        segment = {
            "id": "s1",
            "urls": ["https://cdn.example/untracked.png"],
            "references": [],
        }
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_VIDEO_REFERENCE"):
            ve._validate_references([segment], "acme", manifest={})

    def test_locked_refs_keep_storyboard_and_recompute_type(self):
        segments = [{"id": "s1", "storyboard_ref": True,
                     "urls": ["approved-shot.jpg"], "video_type": 2}]
        result = ve._apply_locked_refs(segments, ["cast.jpg", "product.jpg"])[0]
        self.assertEqual(result["urls"][0], "approved-shot.jpg")
        self.assertEqual(result["video_type"], 5)

    def test_atomic_json_write_replaces_complete_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "results.json")
            ve._atomic_json_write(path, [{"ok": True}])
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), [{"ok": True}])

    def test_completed_without_url_is_failure(self):
        segment = {"id": "s1", "text": "x", "out_path": "x.mp4"}
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
             mock.patch.object(ve, "_submit_video", return_value=("task-1", "x")), \
             mock.patch.object(ve.br_client, "get_video", return_value={"status": "completed"}), \
             mock.patch("time.sleep"):
            result = ve.render_batch([segment], verbose=False, draft=True)
        self.assertFalse(result[0]["ok"])
        self.assertIn("COMPLETED_WITHOUT_URL", result[0]["error"])

    def test_succeeded_manifest_task_resumes_download_without_submit(self):
        segment = {"id": "s1", "text": "x", "out_path": "x.mp4"}
        fingerprint = ve.artifact_contract.build_video_handoff(segment)["fingerprint"]
        segment["video_handoff_fingerprint"] = fingerprint
        manifest = {"tasks": [{"stage": "video", "unit_id": "s1",
                               "handoff_fingerprint": fingerprint, "status": "succeeded",
                               "task_id": "task-1", "video_url": "https://x/video.mp4"}]}
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
             mock.patch.object(ve, "_submit_video") as submit, \
             mock.patch.object(ve.br_client, "get_video", return_value={
                 "status": "succeeded", "videoUrl": "https://x/refreshed.mp4"}), \
             mock.patch.object(ve.br_client, "download", side_effect=OSError("network")):
            result = ve.render_batch([segment], verbose=False, draft=True, manifest=manifest)
        submit.assert_not_called()
        self.assertTrue(result[0]["resume_available"])
        self.assertEqual(result[0]["videoUrl"], "https://x/refreshed.mp4")

    def test_product_sku_without_images_fails_closed_even_in_draft(self):
        segment = {"id": "s1", "text": "x", "product_sku": "missing"}
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_pick_video_model", return_value="seedance-2.0"), \
             mock.patch.dict(sys.modules, {"product_library": mock.Mock()}):
            sys.modules["product_library"].resolve.return_value = {"hero": None, "refs": []}
            result = ve.render_batch([segment], client="acme", verbose=False, draft=True)
        self.assertFalse(result[0]["ok"])
        self.assertIn("PRODUCT_SKU_NO_REFERENCES", result[0]["error"])

    def test_cli_formal_batch_requires_results_out(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "segments.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([], handle)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ve.main(["--batch", path, "--client", "acme"]), 2)

    def test_single_render_uses_configured_wait_without_model_resubmit(self):
        with mock.patch.object(ve.key_setup, "load_key", return_value="sk-test"), \
             mock.patch.object(ve, "_submit_video", return_value=("task-1", "prompt")) as submit, \
             mock.patch.object(ve.br_client, "wait_video", return_value="https://x/video.mp4") as wait:
            url, local = ve.render("prompt", model="seedance-2.0", draft=True,
                                   verbose=False, max_wait=7200)
        self.assertEqual(url, "https://x/video.mp4")
        self.assertIsNone(local)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(wait.call_args.kwargs["max_wait"], 7200)


if __name__ == "__main__":
    unittest.main()
