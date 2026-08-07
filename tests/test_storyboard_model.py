#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import storyboard  # noqa: E402


class StoryboardModelTests(unittest.TestCase):
    def test_storyboard_defaults_to_gpt_image_2(self):
        self.assertEqual(storyboard.DEFAULT_MODEL, "gpt-image-2")

    def test_color_mode_is_not_overridden_by_hardcoded_bw(self):
        plan = {"color_mode": "color", "project_title": "test"}
        shot = {"id": "s1", "scene_prompt": "clean studio"}
        prompt = storyboard.shot_prompt(plan, shot, 1, bw=True, strict_bw=False)
        self.assertNotIn("STRICT BLACK-AND-WHITE", prompt)

    def test_structured_panel_plan_is_expanded_into_director_beats(self):
        plan = {"project_title": "test"}
        shot = {"panel_plan": [{
            "panel": 1,
            "beat": "host lifts the product",
            "shot_size": "medium",
            "camera_movement": "slow push-in",
            "composition": "product on lower-right third",
        }]}
        prompt = storyboard.shot_prompt(plan, shot, 1)
        self.assertIn("PANEL-BY-PANEL DIRECTOR BEATS", prompt)
        self.assertIn("beat=host lifts the product", prompt)
        self.assertIn("camera=slow push-in", prompt)

    def test_missing_panel_plan_defaults_to_twelve_panels(self):
        plan = storyboard.shot_prompt({}, {"id": "s1"}, 1)
        self.assertEqual(len(storyboard.normalize_panel_plan({})), 12)
        self.assertIn("Panel 12:", plan)

    def test_prompt_contains_cinematic_priority_and_qa_rules(self):
        prompt = storyboard.shot_prompt({}, {"id": "s1"}, 1)
        self.assertIn("CINEMATIC PROMPT GRAMMAR", prompt)
        self.assertIn("DIRECTOR QA", prompt)
        self.assertIn("physical action with a visible result", prompt)

    def test_brief_hero_is_hydrated_into_product_refs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            client_dir = os.path.join(directory, "assets", "aeroclip")
            image_dir = os.path.join(client_dir, "images")
            os.makedirs(image_dir)
            image_path = os.path.join(image_dir, "hero.png")
            with open(image_path, "wb") as handle:
                handle.write(b"valid-placeholder-for-mocked-brief")
            brief = {"images": [{"path": "assets/aeroclip/images/hero.png", "tag": "hero"}],
                     "product_type": "耳机"}
            with mock.patch.object(storyboard, "ROOT", directory), \
                 mock.patch("asset_prep._load_brief", return_value=brief):
                plan = storyboard._hydrate_plan_asset_refs({"client": "aeroclip", "shots": []})
            self.assertEqual(plan["asset_refs"]["product_images"], [image_path])
            self.assertEqual(plan["product_type"], "耳机")

    def test_authored_raw_product_refs_are_removed_when_confirmed_anchors_exist(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            client_dir = os.path.join(directory, "assets", "momax")
            image_dir = os.path.join(client_dir, "images")
            raw_dir = os.path.join(client_dir, "product_images")
            os.makedirs(image_dir)
            os.makedirs(raw_dir)
            confirmed_path = os.path.join(image_dir, "hero.png")
            raw_path = os.path.join(raw_dir, "raw.png")
            with open(confirmed_path, "wb") as handle:
                handle.write(b"confirmed")
            with open(raw_path, "wb") as handle:
                handle.write(b"raw")
            brief = {"client": "momax", "images": [{
                "path": "assets/momax/images/hero.png",
                "tag": "hero",
                "status": "confirmed",
                "via": "standardize",
                "model": "gpt-image-2",
            }]}
            plan = {"client": "momax", "asset_refs": {
                "product_images": [
                    "assets/momax/images/hero.png",
                    "assets/momax/product_images/raw.png",
                ]}}

            with mock.patch.object(storyboard, "ROOT", directory), \
                 mock.patch("asset_prep.ROOT", directory), \
                 mock.patch("asset_prep.ASSETS", os.path.join(directory, "assets")), \
                 mock.patch("asset_prep._load_brief", return_value=brief):
                hydrated = storyboard._hydrate_plan_asset_refs(plan)

            self.assertEqual(hydrated["asset_refs"]["product_images"], [confirmed_path])


if __name__ == "__main__":
    unittest.main()
