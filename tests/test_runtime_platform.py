#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import runtime_platform  # noqa: E402


class RuntimePlatformTests(unittest.TestCase):
    def test_platform_tag_is_normalized(self):
        with mock.patch.object(runtime_platform.platform, "system", return_value="Darwin"), \
             mock.patch.object(runtime_platform.platform, "machine", return_value="arm64"):
            self.assertEqual(runtime_platform.platform_tag(), "darwin-arm64")

    def test_bundled_macos_node_modules_not_usable_on_windows(self):
        with mock.patch.object(runtime_platform, "platform_tag", return_value="win-x64"):
            self.assertFalse(runtime_platform.bundled_node_modules_usable("/tmp/engine"))

    def test_remotion_stages_project_relative_media(self):
        import remotion_engine
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "frame.jpg")
            with open(source, "wb") as handle:
                handle.write(b"test media")
            original_root = remotion_engine.ROOT
            original_engine = remotion_engine.ENGINE
            try:
                remotion_engine.ROOT = directory
                remotion_engine.ENGINE = os.path.join(directory, "engine")
                result = remotion_engine._stage_local_media({"shots": [{"image": "frame.jpg"}]})
                staged = result["shots"][0]["image"]
                self.assertTrue(staged.startswith("render-"))
                self.assertTrue(os.path.isfile(os.path.join(remotion_engine.ENGINE, "public", staged)))
            finally:
                remotion_engine.ROOT = original_root
                remotion_engine.ENGINE = original_engine


if __name__ == "__main__":
    unittest.main()
