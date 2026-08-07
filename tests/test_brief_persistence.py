import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import asset_prep  # noqa: E402


class BriefPersistenceTests(unittest.TestCase):
    def test_legacy_brief_load_backfills_missing_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            original_assets = asset_prep.ASSETS
            asset_prep.ASSETS = os.path.join(root, "assets")
            try:
                client_dir = asset_prep._client_dir("legacy")
                path = os.path.join(client_dir, "brief.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"client": "legacy", "product": "old product"}, handle)

                brief = asset_prep._load_brief("legacy")

                self.assertEqual(brief["product"], "old product")
                self.assertEqual(brief["style_hints"], [])
                self.assertEqual(brief["specs"], {})
                self.assertEqual(brief["ppt_files"], [])
                self.assertIsNone(brief["render_profile"])
            finally:
                asset_prep.ASSETS = original_assets

    def test_set_profile_handles_legacy_brief_without_style_hints(self):
        with tempfile.TemporaryDirectory() as root:
            original_assets = asset_prep.ASSETS
            asset_prep.ASSETS = os.path.join(root, "assets")
            try:
                client_dir = asset_prep._client_dir("legacy")
                path = os.path.join(client_dir, "brief.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"client": "legacy", "product": "old product"}, handle)

                result = asset_prep.set_profile(
                    "legacy",
                    product_type="speaker",
                    render_profile={"visual": "lifestyle"},
                )

                self.assertEqual(result["product_type"], "speaker")
                self.assertEqual(result["render_profile"], {"visual": "lifestyle"})
                self.assertEqual(result["style_hints"], [])
            finally:
                asset_prep.ASSETS = original_assets

    def test_stale_image_write_merges_with_newer_assets_and_product_fields(self):
        with tempfile.TemporaryDirectory() as root:
            original_assets = asset_prep.ASSETS
            asset_prep.ASSETS = os.path.join(root, "assets")
            try:
                asset_prep._save_brief("acme", {
                    "client": "acme", "product_type": "earbuds",
                    "usps": ["open ear"], "images": [{"path": "a.png", "tag": "wear"}],
                }, replace=True)
                asset_prep._save_brief("acme", {
                    "client": "acme", "product_type": None,
                    "usps": [], "images": [{"path": "b.png", "tag": "detail"}],
                })
                with open(os.path.join(root, "assets", "acme", "brief.json"), encoding="utf-8") as handle:
                    brief = json.load(handle)
                self.assertEqual(brief["product_type"], "earbuds")
                self.assertEqual({item["path"] for item in brief["images"]}, {"a.png", "b.png"})
            finally:
                asset_prep.ASSETS = original_assets


if __name__ == "__main__":
    unittest.main()
