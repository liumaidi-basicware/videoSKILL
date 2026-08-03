import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import media_qc  # noqa: E402
import video_engine  # noqa: E402


class MediaQcUnitTests(unittest.TestCase):
    def test_formal_missing_tools_fails_closed_and_binds_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            media = os.path.join(directory, "clip.mp4")
            report_path = media + ".qc.json"
            with open(media, "wb") as handle:
                handle.write(b"not-a-video")
            with mock.patch.object(media_qc, "_bins", return_value=(None, None)):
                report = media_qc.check(media, profile="formal", report_path=report_path)
            self.assertFalse(report["passed"])
            self.assertIn("FFMPEG_UNAVAILABLE", report["errors"])
            self.assertEqual(report["file"]["sha256"], hashlib.sha256(b"not-a-video").hexdigest())
            with open(report_path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["file"]["sha256"], report["file"]["sha256"])

    def test_draft_missing_tools_fails_closed(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as media:
            with mock.patch.object(media_qc, "_bins", return_value=(None, None)):
                report = media_qc.check(media.name, profile="draft")
        self.assertFalse(report["passed"])
        self.assertIn("FFMPEG_UNAVAILABLE", report["errors"])

    def test_video_qc_guard_persists_report_in_manifest(self):
        report = {"passed": True, "media": {"actual_duration": 3.1},
                  "file": {"sha256": "abc"}}
        manifest = {}
        with mock.patch.object(video_engine.media_qc, "check", return_value=report):
            result = video_engine._media_qc_guard(
                "/tmp/x.mp4", {"id": "s1", "duration": 3, "ratio": "16:9", "text": "talk"},
                draft=True, manifest=manifest)
        self.assertEqual(result["report_path"], "/tmp/x.mp4.qc.json")
        self.assertEqual(manifest["media_qc"]["video"]["s1"]["file"]["sha256"], "abc")

    def test_require_pass_rejects_report_after_media_changes(self):
        with tempfile.NamedTemporaryFile() as media:
            media.write(b"first")
            media.flush()
            report = {"passed": True, "file": {"path": media.name,
                      "sha256": media_qc.file_sha256(media.name)}}
            media.seek(0)
            media.write(b"changed")
            media.truncate()
            media.flush()
            with self.assertRaisesRegex(ValueError, "MEDIA_QC_STALE"):
                media_qc.require_pass(report)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe unavailable")
class MediaQcIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="media_qc_")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def make(self, name, *, duration=2.0, size="1280x720", audio=True):
        path = os.path.join(self.directory, name)
        command = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "testsrc2=size=%s:rate=30:duration=%s" % (size, duration)]
        if audio:
            command += ["-f", "lavfi", "-i",
                        "sine=frequency=880:sample_rate=48000:duration=%s" % duration, "-shortest"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            command += ["-c:a", "aac"]
        command.append(path)
        subprocess.run(command, check=True)
        return path

    def test_normal_fixture_passes(self):
        report = media_qc.check(self.make("normal.mp4"), profile="formal",
                                expected_duration=2, expected_ratio="16:9", audio_required=True)
        self.assertTrue(report["passed"], report["errors"])
        self.assertAlmostEqual(report["media"]["actual_duration"], 2, delta=0.2)

    def test_no_audio_fixture_fails_required_audio(self):
        report = media_qc.check(self.make("mute.mp4", audio=False), profile="formal",
                                expected_duration=2, expected_ratio="16:9", audio_required=True)
        self.assertIn("AUDIO_TRACK_FAILED", report["errors"])

    def test_short_fixture_fails_expected_duration(self):
        report = media_qc.check(self.make("short.mp4", duration=0.5), profile="formal",
                                expected_duration=3, expected_ratio="16:9", audio_required=True)
        self.assertIn("DURATION_FAILED", report["errors"])

    def test_low_resolution_fixture_fails_dimensions(self):
        report = media_qc.check(self.make("low.mp4", size="320x180"), profile="formal",
                                expected_duration=2, expected_ratio="16:9", audio_required=True)
        self.assertIn("DIMENSIONS_FAILED", report["errors"])


if __name__ == "__main__":
    unittest.main()
