import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import asset_prep  # noqa: E402


class BriefPersistenceTests(unittest.TestCase):
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
