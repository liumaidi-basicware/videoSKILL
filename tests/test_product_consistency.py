#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import product_board  # noqa: E402
import product_library  # noqa: E402
import br_client  # noqa: E402
import tempfile  # noqa: E402
import json  # noqa: E402


class ProductConsistencyTests(unittest.TestCase):
    def test_product_board_prompt_requires_nine_distinct_views(self):
        prompt = product_board.product_board_prompt({"product_type": "咖啡机"})
        for term in ("3x3", "front hero", "rear", "macro material/detail", "bottom/connection/detail", "identical geometry"):
            self.assertIn(term, prompt)
        self.assertIn("no person, hand, usage scene", prompt)

    def test_resolve_reports_confirmed_product_board(self):
        with tempfile_product() as directory:
            with mock.patch.object(product_library, "_sku_dir", return_value=directory), \
                 mock.patch.object(product_library, "_load_meta", return_value={"default_views": []}):
                os.makedirs(os.path.join(directory, "views"))
                with open(os.path.join(directory, "hero.png"), "wb") as handle:
                    handle.write(b"hero-v1")
                board = os.path.join(directory, "product_board.png")
                with open(board, "wb") as handle:
                    handle.write(b"board-v1")
                with open(os.path.join(directory, "product_board_state.json"), "w") as handle:
                    json.dump({
                        "status": "confirmed",
                        "source_fingerprint": product_library.source_fingerprint("acme", "coffee"),
                        "board_sha256": product_library.file_sha256(board),
                    }, handle)
                result = product_library.resolve("acme", "coffee")
        self.assertTrue(result["product_board_confirmed"])

    def test_resolve_marks_board_stale_when_source_content_changes(self):
        with tempfile_product() as directory:
            with mock.patch.object(product_library, "_sku_dir", return_value=directory), \
                 mock.patch.object(product_library, "_load_meta", return_value={"default_views": []}):
                os.makedirs(os.path.join(directory, "views"))
                hero = os.path.join(directory, "hero.png")
                board = os.path.join(directory, "product_board.png")
                with open(hero, "wb") as handle:
                    handle.write(b"hero-v1")
                with open(board, "wb") as handle:
                    handle.write(b"board-v1")
                state_path = os.path.join(directory, "product_board_state.json")
                with open(state_path, "w") as handle:
                    json.dump({
                        "status": "confirmed",
                        "source_fingerprint": product_library.source_fingerprint("acme", "coffee"),
                        "board_sha256": product_library.file_sha256(board),
                    }, handle)
                with open(hero, "wb") as handle:
                    handle.write(b"hero-v2")
                result = product_library.resolve("acme", "coffee")
                with open(state_path) as handle:
                    state = json.load(handle)
        self.assertFalse(result["product_board_confirmed"])
        self.assertIsNone(result["product_board"])
        self.assertEqual(state["status"], "stale")

    def test_resolve_marks_board_stale_when_board_content_changes(self):
        with tempfile_product() as directory:
            with mock.patch.object(product_library, "_sku_dir", return_value=directory), \
                 mock.patch.object(product_library, "_load_meta", return_value={"default_views": []}):
                os.makedirs(os.path.join(directory, "views"))
                hero = os.path.join(directory, "hero.png")
                board = os.path.join(directory, "product_board.png")
                with open(hero, "wb") as handle:
                    handle.write(b"hero")
                with open(board, "wb") as handle:
                    handle.write(b"board-v1")
                with open(os.path.join(directory, "product_board_state.json"), "w") as handle:
                    json.dump({
                        "status": "confirmed",
                        "source_fingerprint": product_library.source_fingerprint("acme", "coffee"),
                        "board_sha256": product_library.file_sha256(board),
                    }, handle)
                with open(board, "wb") as handle:
                    handle.write(b"tampered")
                result = product_library.resolve("acme", "coffee")
        self.assertFalse(result["product_board_confirmed"])

    def test_video_request_can_extend_previous_clip(self):
        captured = {}
        def request(method, path, **kwargs):
            captured.update(kwargs)
            return {"code": 200, "data": {"taskId": "t1"}}
        with mock.patch.object(br_client, "_request", side_effect=request):
            br_client.create_video("sk-test", "continue", model="seedance-2.0",
                                  extend_video_url="https://example.test/previous.mp4")
        self.assertTrue(captured["body"]["extend"])
        self.assertEqual(captured["body"]["videoUrl"], "https://example.test/previous.mp4")

    def test_product_board_uses_sync_img2img_and_regenerates_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def create_image(_key, _prompt, **kwargs):
                calls.append(kwargs["image_urls"])
                return ["https://example.test/board.jpg"]
            def download(_url, path):
                with open(path, "wb") as handle:
                    handle.write(b"board")
            with mock.patch.object(product_board.br_client, "create_image", side_effect=create_image), \
                 mock.patch.object(product_board.br_client, "download", side_effect=download):
                product_board.generate_from_reference_urls("k", ["data:image/png;base64,AAA"], directory)
                product_board.generate_from_reference_urls("k", ["data:image/png;base64,AAA"], directory)
                product_board.generate_from_reference_urls("k", ["data:image/png;base64,BBB"], directory)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], ["data:image/png;base64,AAA"])
            self.assertEqual(calls[1], ["data:image/png;base64,BBB"])

    def test_confirm_binds_source_and_board_content_hashes(self):
        with tempfile_product() as directory:
            os.makedirs(os.path.join(directory, "views"))
            with open(os.path.join(directory, "hero.png"), "wb") as handle:
                handle.write(b"hero")
            with open(os.path.join(directory, "product_board_pending.png"), "wb") as handle:
                handle.write(b"board")
            with mock.patch.object(product_library, "_sku_dir", return_value=directory):
                source = product_library.source_fingerprint("acme", "coffee")
                product_board._save_state(directory, {
                    "status": "pending", "source_fingerprint": source,
                })
                result = product_board.confirm("acme", "coffee")
                state = product_board._load_state(directory)
            self.assertEqual(result["source_fingerprint"], source)
            self.assertEqual(state["status"], "confirmed")
            self.assertEqual(state["board_sha256"], result["board_sha256"])

    def test_confirm_rejects_pending_board_after_source_changes(self):
        with tempfile_product() as directory:
            os.makedirs(os.path.join(directory, "views"))
            hero = os.path.join(directory, "hero.png")
            with open(hero, "wb") as handle:
                handle.write(b"hero-v1")
            with open(os.path.join(directory, "product_board_pending.png"), "wb") as handle:
                handle.write(b"board")
            with mock.patch.object(product_library, "_sku_dir", return_value=directory):
                source = product_library.source_fingerprint("acme", "coffee")
                product_board._save_state(directory, {
                    "status": "pending", "source_fingerprint": source,
                })
                with open(hero, "wb") as handle:
                    handle.write(b"hero-v2")
                with self.assertRaises(SystemExit):
                    product_board.confirm("acme", "coffee")
                state = product_board._load_state(directory)
            self.assertEqual(state["status"], "stale")
            self.assertTrue(os.path.isfile(os.path.join(
                directory, "product_board_pending.png")))

    def test_product_prompt_hard_locks_uploaded_reference(self):
        prompt = product_board.product_board_prompt({"product_type": "耳机"})
        self.assertIn("sole authoritative identity source", prompt)
        self.assertIn("do not substitute a generic item", prompt)


class tempfile_product:
    def __enter__(self):
        import tempfile
        self.directory = tempfile.mkdtemp()
        return self.directory
    def __exit__(self, *_args):
        import shutil
        shutil.rmtree(self.directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
