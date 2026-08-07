import json
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import storyboard  # noqa: E402


class AssetReuseRegistryTests(unittest.TestCase):
    def test_confirmed_board_is_persisted_and_reused_by_identity(self):
        with tempfile.TemporaryDirectory() as root:
            client = "acme"
            run_dir = os.path.join(root, "output", "run-1")
            os.makedirs(run_dir)
            board = os.path.join(run_dir, "cast_board.jpg")
            with open(board, "wb") as handle:
                handle.write(b"cast-board-v1")
            result_path = os.path.join(run_dir, "storyboard_result.json")
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "client": client,
                    "model": "gpt-image-2",
                    "cast_board": {"path": board, "source_fingerprint": "identity-v1",
                                   "url": "https://cdn.example/cast.png"},
                }, handle)

            with mock.patch.object(storyboard, "ROOT", root):
                storyboard.confirm_board(result_path, "cast")
                reused = storyboard._registered_board(client, "cast", "identity-v1")
                registry_path = os.path.join(root, "assets", client, "asset_registry.json")

            self.assertEqual(reused["path"], board)
            self.assertTrue(reused["reused_from_registry"])
            with open(registry_path, encoding="utf-8") as handle:
                registry = json.load(handle)
            entry = registry["assets"]["cast:identity-v1"]
            self.assertEqual(entry["path"], board)
            self.assertEqual(entry["board_sha256"], storyboard._file_sha256(board))
            self.assertEqual(entry["url"], "https://cdn.example/cast.png")

    def test_tampered_or_different_identity_registry_entry_is_not_reused(self):
        with tempfile.TemporaryDirectory() as root:
            board = os.path.join(root, "confirmed-product.jpg")
            with open(board, "wb") as handle:
                handle.write(b"product-v1")
            record = {
                "source_fingerprint": "product-source-v1",
                "path": board,
                "board_sha256": storyboard._file_sha256(board),
                "confirmed_at": "2026-08-05T14:00:00",
            }
            with mock.patch.object(storyboard, "ROOT", root):
                storyboard._register_confirmed_board("acme", "product", record)
                self.assertIsNone(storyboard._registered_board(
                    "acme", "product", "product-source-v2"))
                with open(board, "wb") as handle:
                    handle.write(b"tampered")
                self.assertIsNone(storyboard._registered_board(
                    "acme", "product", "product-source-v1"))


if __name__ == "__main__":
    unittest.main()
