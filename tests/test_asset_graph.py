import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import asset_graph  # noqa: E402


class AssetGraphTests(unittest.TestCase):
    def test_graph_connects_boards_shots_segments_and_output(self):
        manifest = {
            "approvals": {"storyboard": True, "final": True},
            "generation": {"final": {"status": "completed", "outputs": ["final.mp4"]}},
            "tasks": [{"stage": "video", "unit_id": "s1", "status": "succeeded"}],
        }
        result = {"cast_board": {"status": "confirmed", "path": "cast.jpg"},
                  "product_board": {"status": "pending", "path": "product.jpg"},
                  "shots": [{"shot": {"id": "s1"}, "path": "sheet.jpg"}]}
        segments = {"segments": [{"id": "s1", "source_shot_ids": ["s1"]}]}
        graph = asset_graph.build_graph(manifest, result, segments)
        states = {node["id"]: node["state"] for node in graph["nodes"]}
        edges = {(edge["from"], edge["to"]) for edge in graph["edges"]}
        self.assertEqual(states["board:cast_board"], "confirmed")
        self.assertEqual(states["board:product_board"], "pending")
        self.assertEqual(states["segment:s1"], "succeeded")
        self.assertIn(("board:cast_board", "shot:s1"), edges)
        self.assertIn(("shot:s1", "segment:s1"), edges)
        self.assertIn(("segment:s1", "output:assembled"), edges)

    def test_generate_graph_writes_escaped_html_with_partial_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            storyboard = os.path.join(directory, "storyboard_result.json")
            output = os.path.join(directory, "graph.html")
            with open(storyboard, "w", encoding="utf-8") as handle:
                json.dump({"cast_board": {"status": "confirmed", "path": "<cast>.jpg"}}, handle)
            graph = asset_graph.generate_graph(out_path=output, storyboard_result_path=storyboard)
            self.assertEqual(len(graph["nodes"]), 1)
            with open(output, encoding="utf-8") as handle:
                page = handle.read()
            self.assertIn("Role / product boards", page)
            self.assertIn("&lt;cast&gt;.jpg", page)
            self.assertNotIn("<cast>.jpg", page)


if __name__ == "__main__":
    unittest.main()
