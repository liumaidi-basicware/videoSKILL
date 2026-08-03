import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import asset_prep
import digital_human


class AssetConfirmationContractTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="confirmation-assets-")
        self.original_assets = asset_prep.ASSETS
        asset_prep.ASSETS = self.root

    def tearDown(self):
        asset_prep.ASSETS = self.original_assets
        shutil.rmtree(self.root, ignore_errors=True)

    def test_only_confirmed_or_trusted_upload_pass_by_default(self):
        client = "acme"
        statuses = ["confirmed", "trusted_upload", "pending", "unknown",
                    "rejected", "failed", "remote", "quarantine"]
        images = []
        for status in statuses:
            path = os.path.join(asset_prep._client_dir(client), "images",
                                "asset-%s.png" % status)
            with open(path, "wb") as handle:
                handle.write(bytes.fromhex("89504e470d0a1a0a"))
            images.append({"path": path, "status": status})
        images.append({"path": os.path.join(asset_prep._client_dir(client), "images",
                                             "asset-missing.png")})
        asset_prep._save_brief(client, {"client": client, "images": images}, replace=True)

        self.assertTrue(asset_prep.is_confirmed(client, images[0]["path"]))
        self.assertTrue(asset_prep.is_confirmed(client, images[1]["path"]))
        for status in statuses[2:] + ["missing"]:
            self.assertFalse(asset_prep.is_confirmed(
                client, os.path.join(asset_prep._client_dir(client), "images",
                                     "asset-%s.png" % status)))

    def test_ingest_cleans_customer_file_before_registration(self):
        source = os.path.join(self.root, "upload.png")
        with open(source, "wb") as handle:
            handle.write(bytes.fromhex("89504e470d0a1a0a"))
        with mock.patch.object(asset_prep, "clean_image",
                               return_value={"status": "pending", "via": "gpt-image-2"}) as clean:
            entry = asset_prep.ingest_image("acme", source, tag="hero")
        clean.assert_called_once()
        self.assertEqual(entry["status"], "pending")

    def test_raw_trusted_upload_is_not_product_ready(self):
        client = "acme"
        path = os.path.join(asset_prep._client_dir(client), "images", "raw.png")
        with open(path, "wb") as handle:
            handle.write(bytes.fromhex("89504e470d0a1a0a"))
        asset_prep._save_brief(client, {"client": client, "images": [{
            "path": os.path.relpath(path, asset_prep.ROOT),
            "tag": "product", "status": "trusted_upload"
        }]}, replace=True)
        self.assertFalse(asset_prep.is_product_asset_ready(client, path))


class DigitalHumanConfirmationContractTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="confirmation-actors-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _create_pending(self):
        actor_dir = os.path.join(self.root, "acme", "host")
        source = os.path.join(self.root, "source.png")
        with open(source, "wb") as handle:
            handle.write(b"portrait")
        with mock.patch.object(digital_human, "_actor_dir", return_value=actor_dir):
            result = digital_human.create_actor(
                "acme", "host", from_file=source, persona={"profession": "host"})
        return actor_dir, result

    def test_pending_actor_requires_confirmation_or_draft_override(self):
        actor_dir, created = self._create_pending()
        self.assertEqual(created["meta"]["status"], "pending")
        with mock.patch.object(digital_human, "_actor_dir", return_value=actor_dir):
            with self.assertRaises(SystemExit) as error:
                digital_human.resolve("acme", "host")
            draft = digital_human.resolve("acme", "host", allow_draft=True)
        self.assertIn("UNCONFIRMED_ACTOR", str(error.exception))
        self.assertTrue(draft["draft"])

    def test_confirm_actor_enables_formal_resolve(self):
        actor_dir, _ = self._create_pending()
        with mock.patch.object(digital_human, "_actor_dir", return_value=actor_dir):
            digital_human.confirm_actor("acme", "host")
            resolved = digital_human.resolve("acme", "host")
        self.assertEqual(resolved["status"], "confirmed")
        self.assertFalse(resolved["draft"])
        with open(os.path.join(actor_dir, "meta.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
