import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import asset_prep  # noqa: E402


class AssetRecoveryTests(unittest.TestCase):
    def test_missing_brief_recovers_existing_images(self):
        with tempfile.TemporaryDirectory() as root:
            original_assets = asset_prep.ASSETS
            asset_prep.ASSETS = os.path.join(root, "assets")
            try:
                directory = asset_prep._client_dir("acme")
                image = os.path.join(directory, "images", "human2.png")
                with open(image, "wb") as handle:
                    handle.write(b"not a real png")
                # Use a minimal valid PNG signature accepted by the image utility.
                with open(image, "wb") as handle:
                    handle.write(bytes.fromhex(
                        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                        "0000000d49444154789c6360f8cf000000030001"))
                brief = asset_prep._load_brief("acme")
                self.assertEqual(len(brief["images"]), 1)
                self.assertEqual(brief["images"][0]["status"], "quarantine")
                self.assertFalse(asset_prep.is_confirmed(
                    "acme", brief["images"][0]["path"]))
                self.assertTrue(os.path.isfile(os.path.join(directory, "brief.json")))
            finally:
                asset_prep.ASSETS = original_assets


if __name__ == "__main__":
    unittest.main()
