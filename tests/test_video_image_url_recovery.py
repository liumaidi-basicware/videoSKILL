import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import video_image_url_recovery as recovery  # noqa: E402


class VideoImageUrlRecoveryTests(unittest.TestCase):
    def write_json(self, path, value):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)

    def touch(self, path):
        with open(path, "wb") as handle:
            handle.write(b"image")

    def test_recovers_product_and_storyboard_urls_from_preserved_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            product = os.path.join(directory, "product_board_pending.jpg")
            shot = os.path.join(directory, "shot_01_s1.jpg")
            self.touch(product)
            self.touch(shot)
            self.write_json(os.path.join(directory, "product_board_state.json"), {
                "result_url": "https://cdn.example/product.png",
            })
            self.write_json(os.path.join(directory, "storyboard_result.json"), {
                "shots": [{
                    "id": "s1", "path": shot,
                    "url": "https://cdn.example/shot.png",
                }]
            })
            segments = {"segments": [{
                "id": "s1",
                "storyboard_ref": True,
                "storyboard_path": shot,
                "urls": [product, shot],
                "references": [
                    {"tag": "@product", "type": "product_board", "url": product},
                    {"tag": "@storyboard", "type": "storyboard_composition", "url": shot},
                ],
            }]}
            result = recovery.recover_segments(segments, directory)
        segment = result["segments_doc"]["segments"][0]
        self.assertFalse(result["missing"])
        self.assertEqual(segment["urls"], [
            "https://cdn.example/product.png",
            "https://cdn.example/shot.png",
        ])
        self.assertEqual(segment["storyboard_url"], "https://cdn.example/shot.png")

    def test_missing_url_remains_blocked_without_redraw_or_base64(self):
        with tempfile.TemporaryDirectory() as directory:
            shot = os.path.join(directory, "shot.jpg")
            self.touch(shot)
            self.write_json(os.path.join(directory, "storyboard_result.json"), {
                "shots": [{"id": "s1", "path": shot}]
            })
            segments = {"segments": [{
                "id": "s1", "storyboard_ref": True,
                "storyboard_path": shot,
                "urls": [shot],
                "references": [{"tag": "@storyboard", "type": "storyboard_composition",
                                "url": shot}],
            }]}
            result = recovery.recover_segments(segments, directory)
        self.assertTrue(result["missing"])
        self.assertEqual(result["segments_doc"]["segments"][0]["urls"], [shot])

    def test_missing_asset_plan_dedupes_fields_into_unique_regeneration_items(self):
        with tempfile.TemporaryDirectory() as directory:
            shot = os.path.join(directory, "shot_01_s1.jpg")
            cast = os.path.join(directory, "cast_board.jpg")
            for path in (shot, cast):
                self.touch(path)
            segments = {"segments": [{
                "id": "s1",
                "storyboard_ref": True,
                "storyboard_path": shot,
                "urls": [shot, cast],
                "references": [
                    {"tag": "@storyboard", "type": "storyboard_composition",
                     "url": shot},
                    {"tag": "@mina", "type": "character_board", "url": cast},
                    {"tag": "@product", "type": "product_board",
                     "url": "https://cdn.example/product.png"},
                ],
            }]}
            result = recovery.recover_segments(segments, directory)
            plan = recovery.build_missing_asset_plan(
                result, client="momax", run_id="run-1")
        self.assertEqual(plan["status"], "blocked_until_remote_urls_restored")
        self.assertEqual(plan["client"], "momax")
        self.assertEqual(plan["run_id"], "run-1")
        self.assertEqual(len(plan["missing_assets"]), 2)
        by_kind = {item["asset_kind"]: item for item in plan["missing_assets"]}
        self.assertEqual(by_kind["storyboard_shot"]["segments"], ["s1"])
        self.assertEqual(by_kind["storyboard_shot"]["tags"], ["@storyboard"])
        self.assertEqual(by_kind["storyboard_shot"]["types"], ["storyboard_composition"])
        self.assertIn("references.url", by_kind["storyboard_shot"]["fields"])
        self.assertIn("storyboard_url", by_kind["storyboard_shot"]["fields"])
        self.assertEqual(by_kind["cast_board"]["tags"], ["@mina"])
        action_codes = {item["code"] for item in plan["recommended_actions"]}
        self.assertIn("REGENERATE_CAST_BOARD_FOR_URL", action_codes)
        self.assertIn("REGENERATE_STORYBOARD_SHOTS_FOR_URL", action_codes)
        self.assertEqual(plan["already_remote_references"][0]["url"],
                         "https://cdn.example/product.png")


if __name__ == "__main__":
    unittest.main()
