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
        for term in ("3x3", "front hero", "rear", "macro material/detail",
                     "bottom/connection/detail", "identical geometry",
                     "PRODUCT IDENTITY CONTRACT", "Color", "Material/finish",
                     "Controls/buttons", "Ports/connectors/openings",
                     "Functional/contact surfaces"):
            self.assertIn(term, prompt)
        self.assertIn("no person, hand, usage scene", prompt)

    def test_product_board_fingerprint_includes_identity_contract_policy(self):
        refs = ["data:image/png;base64,a"]
        basic = {"product_type": "speaker", "product_color": "yellow"}
        changed = dict(basic, buttons="front power button plus bluetooth button")
        self.assertNotEqual(
            product_board.product_board_source_fingerprint(refs, basic),
            product_board.product_board_source_fingerprint(refs, changed))

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
        self.assertEqual(captured["body"]["videoUrls"],
                         ["https://example.test/previous.mp4"])

    def test_product_board_uses_async_retrieve_and_regenerates_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            waits = []
            def create_image_generation(_key, _prompt, **kwargs):
                calls.append(kwargs["image_urls"])
                return "img_%d" % len(calls)
            def wait_image_generation(_key, task_id, **_kwargs):
                waits.append(task_id)
                return ["https://example.test/board.jpg"]
            download_kwargs = []
            def download(_url, path, **kwargs):
                download_kwargs.append(kwargs)
                with open(path, "wb") as handle:
                    handle.write(b"board")
            with mock.patch.object(product_board.br_client, "create_image_generation",
                                   side_effect=create_image_generation), \
                 mock.patch.object(product_board.br_client, "wait_image_generation",
                                   side_effect=wait_image_generation), \
                 mock.patch.object(product_board.br_client, "download", side_effect=download):
                first = product_board.generate_from_reference_urls("k", ["data:image/png;base64,AAA"], directory)
                reused = product_board.generate_from_reference_urls("k", ["data:image/png;base64,AAA"], directory)
                second = product_board.generate_from_reference_urls("k", ["data:image/png;base64,BBB"], directory)
            self.assertEqual(len(calls), 2)
            self.assertEqual(waits, ["img_1", "img_2"])
            self.assertEqual(calls[0], ["data:image/png;base64,AAA"])
            self.assertEqual(calls[1], ["data:image/png;base64,BBB"])
            self.assertTrue(all(item.get("allow_nonpublic_peer")
                                for item in download_kwargs))
            state = product_board._load_state(directory)
            self.assertEqual(state["task_id"], "img_2")
            self.assertEqual(state["result_url"], "https://example.test/board.jpg")
            self.assertEqual(first["url"], "https://example.test/board.jpg")
            self.assertEqual(reused["result_url"], "https://example.test/board.jpg")
            self.assertEqual(second["task_id"], "img_2")

    def test_product_board_resumes_download_pending_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as directory:
            source_fp = product_board.product_board_source_fingerprint(
                ["data:image/png;base64,AAA"],
                {"product_type": "the exact product",
                 "style_hint": "commercial product reference photography"})
            product_board._save_state(directory, {
                "status": "download_pending",
                "model": product_board.DEFAULT_MODEL,
                "source_fingerprint": source_fp,
                "task_id": "img_existing",
                "request_id": "req_existing",
                "result_url": "https://example.test/board.jpg",
            })

            def download(_url, path, **_kwargs):
                with open(path, "wb") as handle:
                    handle.write(b"board")

            with mock.patch.object(product_board.br_client, "create_image_generation",
                                   side_effect=AssertionError("resubmitted")), \
                 mock.patch.object(product_board.br_client, "wait_image_generation",
                                   side_effect=AssertionError("repolled")), \
                 mock.patch.object(product_board.br_client, "download",
                                   side_effect=download):
                result = product_board.generate_from_reference_urls(
                    "k", ["data:image/png;base64,AAA"], directory)
            state = product_board._load_state(directory)
            self.assertTrue(result["skipped"])
            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["task_id"], "img_existing")
            self.assertEqual(state["result_url"], "https://example.test/board.jpg")

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
