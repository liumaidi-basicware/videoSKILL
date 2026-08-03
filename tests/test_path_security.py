import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import asset_prep
import run_manifest
from artifact_contract import build_video_handoff


class PathSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="path-security-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_brief_cannot_confirm_cross_client_file(self):
        original = asset_prep.ASSETS
        asset_prep.ASSETS = os.path.join(self.temp, "assets")
        try:
            other = os.path.join(asset_prep._client_dir("other"), "images", "private.png")
            with open(other, "wb") as handle:
                handle.write(bytes.fromhex("89504e470d0a1a0a"))
            rel = os.path.relpath(other, asset_prep.ROOT)
            asset_prep._save_brief(
                "acme", {"client": "acme", "images": [
                    {"path": rel, "status": "confirmed"}]}, replace=True)
            self.assertFalse(asset_prep.is_confirmed("acme", rel))
        finally:
            asset_prep.ASSETS = original

    def test_confirm_image_never_deletes_outside_client_root(self):
        original = asset_prep.ASSETS
        asset_prep.ASSETS = os.path.join(self.temp, "assets")
        try:
            images = os.path.join(asset_prep._client_dir("acme"), "images")
            chosen = os.path.join(images, "chosen.png")
            outside = os.path.join(self.temp, "outside.png")
            for path in (chosen, outside):
                with open(path, "wb") as handle:
                    handle.write(bytes.fromhex("89504e470d0a1a0a"))
            asset_prep._save_brief("acme", {"client": "acme", "images": [
                {"path": chosen, "tag": "hero", "status": "pending"},
                {"path": outside, "tag": "hero", "status": "pending"},
            ]}, replace=True)
            asset_prep.confirm_image("acme", chosen)
            self.assertTrue(os.path.isfile(outside))
        finally:
            asset_prep.ASSETS = original

    def test_formal_handoff_rejects_output_outside_run_root(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        approval = {"status": "confirmed", "client": "acme", "run_id": "run-1"}
        segment = {"id": "s1", "client": "acme", "run_id": "run-1",
                   "out_path": os.path.join(self.temp, "escaped.mp4"),
                   "storyboard_approval": approval}
        segment["video_handoff_fingerprint"] = build_video_handoff(segment)["fingerprint"]
        with self.assertRaisesRegex(ValueError, "SEGMENT_OUTPUT_ESCAPE"):
            run_manifest.record_video_handoff(manifest, {
                "client": "acme", "run_id": "run-1", "segments": [segment],
                "storyboard_approval": approval, "missing_images": [], "needs_image": []})

    def test_manifest_revalidates_loaded_identifiers(self):
        manifest = run_manifest.create_manifest("acme", "run-1")
        manifest["client"] = "../../outside"
        manifest["identity"]["client"] = "../../outside"
        with self.assertRaisesRegex(ValueError, "CLIENT_INVALID"):
            run_manifest.identity_gate(manifest)


if __name__ == "__main__":
    unittest.main()
