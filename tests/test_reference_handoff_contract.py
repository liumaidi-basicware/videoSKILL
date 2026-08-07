import os
import sys
import json
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import script_splitter  # noqa: E402


class ReferenceHandoffContractTests(unittest.TestCase):
    def test_human_product_usage_clip_requires_usage_identity(self):
        required = script_splitter._required_reference_types(
            [], has_human=True, has_product=True, usage_required=True)
        self.assertEqual(required, {
            "storyboard_composition", "character_board",
            "product_board", "product_usage_identity"})

    def test_human_product_clip_without_physical_usage_does_not_require_usage_identity(self):
        required = script_splitter._required_reference_types(
            [], has_human=True, has_product=True, usage_required=False)
        self.assertEqual(required, {
            "storyboard_composition", "character_board", "product_board"})

    def test_generated_boards_replace_raw_duplicates(self):
        refs = {
            "product_images": ["raw-product"],
            "digital_human_portraits": ["raw-human"],
            "usage_reference_images": ["pose-guide"],
        }
        refs.pop("usage_reference_images")
        refs.pop("digital_human_portraits")
        refs.pop("product_images")
        refs.update({
            "product_usage_images": ["usage-board"],
            "cast_boards": ["cast-board"],
            "product_boards": ["product-board"],
        })
        self.assertEqual(set(refs), {
            "product_usage_images", "cast_boards", "product_boards"})

    def test_confirmed_product_board_replaces_product_registry_tags(self):
        registry = script_splitter._remap_reference_registry_to_confirmed_boards(
            [
                {"tag": "@product_hero", "url": "raw-hero",
                 "type": "product_identity"},
                {"tag": "@product_angle", "url": "raw-angle",
                 "type": "product_identity"},
                {"tag": "@mina", "url": "raw-actor",
                 "type": "character_identity"},
            ],
            {"product_boards": "/confirmed/product-board.jpg",
             "cast_boards": "/confirmed/cast-board.jpg"})
        by_tag = {item["tag"]: item for item in registry}
        self.assertEqual(by_tag["@product_hero"]["url"], "/confirmed/product-board.jpg")
        self.assertEqual(by_tag["@product_hero"]["type"], "product_board")
        self.assertEqual(by_tag["@product_angle"]["source"], "asset_refs.product_boards")
        self.assertEqual(by_tag["@mina"]["type"], "character_board")

    def test_collect_references_satisfies_product_board_when_registry_uses_product_tags(self):
        plan_refs = {
            "reference_registry": script_splitter._remap_reference_registry_to_confirmed_boards(
                [
                    {"tag": "@product_hero", "url": "raw-hero",
                     "type": "product_identity"},
                    {"tag": "@product_angle", "url": "raw-angle",
                     "type": "product_identity"},
                ],
                {"product_boards": "/confirmed/product-board.jpg"}),
        }
        references, dropped = script_splitter._collect_typed_references(
            {"id": "s1", "ref_tags": ["@product_hero", "@product_angle"]},
            plan_refs, "/confirmed/storyboard.jpg")
        self.assertIn("product_board", {item["type"] for item in references})
        self.assertIn("storyboard_composition", {item["type"] for item in references})
        self.assertNotIn("product_identity", {item["type"] for item in references})
        self.assertFalse([item for item in dropped if item.get("type") == "product_board"])

    def test_collect_references_does_not_mutate_authored_ref_tags(self):
        shot = {"id": "s5", "characters": ["mina"],
                "ref_tags": ["@mina", "@product_hero", "@product_angle"]}
        plan_refs = {"reference_registry": [
            {"tag": "@mina", "url": "/confirmed/cast.jpg", "type": "character_board"},
            {"tag": "@product_hero", "url": "/confirmed/product.jpg", "type": "product_board"},
            {"tag": "@product_angle", "url": "/confirmed/product.jpg", "type": "product_board"},
            {"tag": "@usage", "url": "/confirmed/usage.jpg", "type": "product_usage_identity"},
        ]}
        script_splitter._collect_typed_references(shot, plan_refs, "/confirmed/storyboard.jpg")
        self.assertEqual(shot["ref_tags"],
                         ["@mina", "@product_hero", "@product_angle"])

    def test_collect_references_respects_requires_usage_false_contract(self):
        shot = {
            "id": "s1",
            "visual": "hand magnetically attaches the speaker to the phone back",
            "requires_usage": False,
            "ref_tags": ["@product_hero"],
        }
        plan_refs = {"reference_registry": [
            {"tag": "@product_hero", "url": "/confirmed/product.jpg", "type": "product_board"},
            {"tag": "@usage", "url": "/confirmed/usage.jpg", "type": "product_usage_identity"},
        ]}
        references, _ = script_splitter._collect_typed_references(
            shot, plan_refs, "/confirmed/storyboard.jpg")
        self.assertNotIn("product_usage_identity", {item["type"] for item in references})

    def test_collect_references_legacy_usage_fallback_when_contract_absent(self):
        shot = {
            "id": "s1",
            "visual": "hand magnetically attaches the speaker to the phone back",
            "ref_tags": ["@product_hero"],
        }
        plan_refs = {"reference_registry": [
            {"tag": "@product_hero", "url": "/confirmed/product.jpg", "type": "product_board"},
            {"tag": "@usage", "url": "/confirmed/usage.jpg", "type": "product_usage_identity"},
        ]}
        references, _ = script_splitter._collect_typed_references(
            shot, plan_refs, "/confirmed/storyboard.jpg")
        self.assertIn("product_usage_identity", {item["type"] for item in references})

    def test_storyboard_shot_map_prefers_remote_url_for_video_refs(self):
        mapping = script_splitter._storyboard_shot_map({
            "shots": [{
                "shot": {"id": "s1"},
                "path": "/tmp/local-shot.jpg",
                "url": "https://cdn.example/shot.jpg",
            }]
        })
        self.assertEqual(mapping["s1"]["path"], "/tmp/local-shot.jpg")
        self.assertEqual(mapping["s1"]["url"], "https://cdn.example/shot.jpg")

    def test_product_board_video_ref_recovers_state_result_url(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "product_board_state.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"result_url": "https://cdn.example/product-board.jpg"}, handle)
            ref = script_splitter._result_item_video_ref(
                {"path": os.path.join(directory, "product_board.jpg")},
                storyboard_dir=directory, result_key="product_board")
        self.assertEqual(ref, "https://cdn.example/product-board.jpg")


if __name__ == "__main__":
    unittest.main()
