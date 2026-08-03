import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import script_splitter  # noqa: E402


class ReferenceHandoffContractTests(unittest.TestCase):
    def test_human_product_clip_requires_usage_identity(self):
        required = script_splitter._required_reference_types(
            [], has_human=True, has_product=True)
        self.assertEqual(required, {
            "storyboard_composition", "character_board",
            "product_board", "product_usage_identity"})

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


if __name__ == "__main__":
    unittest.main()
