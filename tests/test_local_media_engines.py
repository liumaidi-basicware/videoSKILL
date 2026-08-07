import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import hf_engine
import remotion_engine
import compose


class HyperFramesOfflineTests(unittest.TestCase):
    def test_browser_path_accepts_explicit_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = os.path.join(directory, "chrome-headless-shell")
            with open(browser, "w") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(browser, 0o700)
            with mock.patch.dict(os.environ,
                                 {"HYPERFRAMES_BROWSER_PATH": browser}, clear=False):
                ok, resolved = hf_engine.ensure_browser_path()
            self.assertTrue(ok)
            self.assertEqual(resolved, browser)

    def test_cli_prefers_vendored_hyperframes_runtime(self):
        with mock.patch.object(hf_engine.shutil, "which", return_value="/bin/node"):
            command = hf_engine._npx_command(["hyperframes", "lint"])
        self.assertEqual(command[0], "/bin/node")
        self.assertTrue(command[1].endswith("hyperframes/bin/hyperframes.mjs"))
        self.assertEqual(command[2:], ["lint"])

    def test_project_uses_vendored_gsap_without_remote_script(self):
        spec = {"scenes": [{"text": "local", "start": 0, "end": 1}]}
        with tempfile.TemporaryDirectory() as directory:
            html = hf_engine.build_project(spec, directory)
            with open(html, encoding="utf-8") as handle:
                source = handle.read()
            self.assertRegex(source, r'<script nonce="[^"]+" src="gsap\.min\.js"></script>')
            self.assertNotIn("cdn.jsdelivr.net", source)
            runtime = os.path.join(directory, "gsap.min.js")
            self.assertTrue(os.path.isfile(runtime))
            with open(runtime, encoding="utf-8") as handle:
                self.assertIn("GSAP 3.15.0", handle.read(200))

    def test_scene_track_index_is_rendered_for_overlay_layers(self):
        html = hf_engine.build_html({
            "scenes": [{"text": "subtitle", "start": 0, "end": 2},
                       {"text": "label", "start": 0.5, "end": 1.5,
                        "track_index": 2}]})
        self.assertIn('data-track-index="1"', html)
        self.assertIn('data-track-index="2"', html)

    def test_xfade_offsets_accumulate_across_segments(self):
        probes = [mock.Mock(stdout="4\n"), mock.Mock(stdout="6\n"),
                  mock.Mock(stdout="8\n")]
        with mock.patch.object(compose.subprocess, "run", side_effect=probes), \
             mock.patch.object(compose, "_ffprobe_bin", return_value="ffprobe"), \
             mock.patch.object(compose, "_run") as run:
            compose._concat_with_xfade(["a.mp4", "b.mp4", "c.mp4"], "out.mp4", 0.4)
        command = run.call_args.args[0]
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("offset=3.600", filter_graph)
        self.assertIn("offset=9.200", filter_graph)


class RemotionMediaSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="remotion-security-")
        self.original_root = remotion_engine.ROOT
        self.original_engine = remotion_engine.ENGINE
        remotion_engine.ROOT = self.temp
        remotion_engine.ENGINE = os.path.join(self.temp, "engine")

    def tearDown(self):
        remotion_engine.ROOT = self.original_root
        remotion_engine.ENGINE = self.original_engine
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_remote_media_requires_explicit_allowlist(self):
        with mock.patch.dict(os.environ, {remotion_engine.REMOTE_MEDIA_ALLOWLIST_ENV: ""}):
            with self.assertRaisesRegex(SystemExit, "REMOTE_MEDIA_NOT_ALLOWLISTED"):
                remotion_engine._stage_local_media(
                    {"shots": [{"video": "https://media.example/video.mp4"}]})

    def test_allowlisted_host_resolving_private_is_rejected(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with mock.patch.dict(os.environ,
                             {remotion_engine.REMOTE_MEDIA_ALLOWLIST_ENV: "media.example"}), \
             mock.patch.object(remotion_engine.socket, "getaddrinfo", return_value=answer):
            with self.assertRaisesRegex(SystemExit, "REMOTE_MEDIA_PRIVATE_ADDRESS"):
                remotion_engine._stage_local_media(
                    {"shots": [{"video": "https://media.example/video.mp4"}]})

    def test_local_staging_is_unique_per_call(self):
        source = os.path.join(self.temp, "frame.jpg")
        with open(source, "wb") as handle:
            handle.write(b"fixture")
        first = remotion_engine._stage_local_media({"shots": [{"image": source}]})
        second = remotion_engine._stage_local_media({"shots": [{"image": source}]})
        self.assertNotEqual(first["shots"][0]["image"], second["shots"][0]["image"])
        for staged in (first["shots"][0]["image"], second["shots"][0]["image"]):
            self.assertTrue(os.path.isfile(os.path.join(remotion_engine.ENGINE, "public", staged)))


if __name__ == "__main__":
    unittest.main()
