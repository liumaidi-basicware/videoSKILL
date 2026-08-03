#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cjk_font  # noqa: E402
import hf_engine  # noqa: E402
import text_anim  # noqa: E402


class CjkFontTests(unittest.TestCase):
    def test_font_resolver_returns_family(self):
        self.assertTrue(cjk_font.family())

    def test_text_anim_uses_cjk_font_when_default_is_generic(self):
        with mock.patch.object(cjk_font, "family", return_value="Test CJK"):
            self.assertEqual(text_anim._font_family({"brand": {"font": "Arial"}}), "Test CJK")

    def test_hyperframes_css_contains_cjk_fallbacks(self):
        with mock.patch.object(cjk_font, "family", return_value="Test CJK"):
            self.assertIn("Test CJK", hf_engine._cjk_font_css())


if __name__ == "__main__":
    unittest.main()
