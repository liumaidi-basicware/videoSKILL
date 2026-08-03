import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import artifact_contract  # noqa: E402
import br_client  # noqa: E402
import fuse  # noqa: E402
import ocr_check  # noqa: E402
import pipeline  # noqa: E402


class OptimizationReportRegressions(unittest.TestCase):
    def test_provider_candidate_order_is_not_reversed(self):
        catalog = [
            {"modelId": "seedance-id", "modelName": "seedance-provider"},
            {"modelId": "kling-id", "modelName": "kling-provider"},
        ]
        with mock.patch.object(br_client, "list_video_models", return_value=catalog):
            candidates = br_client._legacy_video_model_candidates("seedance-id")
        self.assertEqual(candidates[:2], ["seedance-provider", "seedance-id"])

    def test_missing_declared_storyboard_cannot_be_fingerprinted(self):
        with self.assertRaisesRegex(ValueError, "STALE_STORYBOARD_ARTIFACT_MISSING"):
            artifact_contract.build_video_handoff({"storyboard_path": "/missing/shot.jpg"})

    def test_full_slot_uses_aspect_preserving_crop(self):
        def fake_run(command, **_kwargs):
            with open(command[-1], "wb") as handle:
                handle.write(b"video")
            return mock.Mock(returncode=0)

        with mock.patch.object(fuse, "_probe_wh", return_value=(1920, 1080)), \
                mock.patch.object(fuse, "_probe_duration", return_value=1), \
                mock.patch.object(fuse, "_has_audio", return_value=False), \
                mock.patch.object(fuse, "ensure_ffmpeg_on_path", return_value="ffmpeg"), \
                mock.patch.object(fuse, "_valid_video", return_value=True), \
                mock.patch.object(fuse.subprocess, "run", side_effect=fake_run) as run:
            with tempfile.TemporaryDirectory() as directory:
                fuse.overlay("bg.mp4", "portrait.mp4", os.path.join(directory, "out.mp4"),
                             slot="full")
        filter_complex = run.call_args.args[0][run.call_args.args[0].index("-filter_complex") + 1]
        self.assertIn("force_original_aspect_ratio=increase,crop=1728:972", filter_complex)

    def test_vision_request_error_is_reported_not_clear(self):
        with mock.patch.object(ocr_check.sys, "platform", "darwin"), \
                mock.patch.dict(sys.modules, {"Vision": mock.Mock()}), \
                mock.patch.object(ocr_check, "extract_frames", return_value=(
                    ["frame.jpg"], "/tmp", {"expected": 1, "duration": 1,
                    "include_endpoints": True})), \
                mock.patch.object(ocr_check, "_vision_ocr_file",
                                  side_effect=RuntimeError("Vision OCR 请求失败")):
            report = ocr_check.check_video("take.mp4", min_frames=1, max_frames=1)
        self.assertEqual(report["status"], "error")
        self.assertFalse(report["subtitle_detected"])

    def test_storyboard_previews_follow_customer_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("preview.html", "embedded.md", "index.md"):
                path = os.path.join(directory, name)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(name)
                paths.append(path)
            result = os.path.join(directory, "result.json")
            with open(result, "w", encoding="utf-8") as handle:
                import json
                json.dump(dict(zip(("preview_html", "embedded_md", "index_md"), paths)), handle)
            manifest = {"generation": {"storyboard": {"outputs": []}}, "approvals": {},
                        "storyboard_approval": {"result": {"path": result}}}
            with mock.patch.object(pipeline.rm, "STAGES", ("storyboard",)), \
                    mock.patch.object(pipeline.rm, "approval_is_current", return_value=False):
                status = pipeline.pipeline_status(manifest)
            self.assertEqual(status["customer_preview"], [os.path.abspath(path) for path in paths])


if __name__ == "__main__":
    unittest.main()
