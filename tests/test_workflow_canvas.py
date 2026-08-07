import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import workflow_canvas  # noqa: E402


class WorkflowCanvasTests(unittest.TestCase):
    def _write_json(self, path, value):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    def test_build_snapshot_surfaces_feedback_loops_and_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source.png")
            feedback = os.path.join(tmp, "feedback.png")
            refined = os.path.join(tmp, "refined.png")
            storyboard = os.path.join(tmp, "storyboard.jpg")
            shot = os.path.join(tmp, "shot.jpg")
            for path in (source, feedback, refined, storyboard, shot):
                with open(path, "wb") as handle:
                    handle.write(b"x")

            manifest = {
                "client": "acme",
                "run_id": "run-1",
                "approvals": {"storyboard": False, "video": False, "captions": False,
                              "final": False, "derive": False},
                "generation": {"storyboard": {"status": "pending_approval"}},
                "identity": {"client": "acme", "asset_dir": os.path.join(ROOT, "assets", "acme")},
            }
            brief = {
                "client": "acme",
                "images": [
                    {"path": source, "tag": "hero", "status": "confirmed"},
                    {"path": refined, "tag": "hero", "status": "pending",
                     "edited_from": source, "feedback_refs": [feedback],
                     "prompt": "把背景改成纯白"},
                ],
            }
            storyboard_result = {
                "needs_confirmation": True,
                "reference_registry": [
                    {"tag": "@host", "source": source, "scope": "digital_human_portraits"}
                ],
                "cast_board": {
                    "status": "pending",
                    "path": storyboard,
                    "refinement": {
                        "edit_prompt": "把背景改成纯白",
                        "feedback_refs": [feedback],
                        "identity_reference_paths": [source],
                        "generation_reference_paths": [source],
                    },
                },
                "shots": [
                    {"shot": {"id": "s1", "ref_tags": ["@host"], "dialogue": "hello", "visual": "host"},
                     "path": shot, "status": "pending"}
                ],
            }
            segments = [{"id": "s1", "references": [{"url": source, "tag": "@host", "type": "cast_board"}]}]
            events = [{"ts": "2026-08-07T12:00:00", "event": "asset_refined", "id": "e1"}]

            snapshot = workflow_canvas.build_snapshot(
                manifest=manifest, brief=brief, storyboard_result=storyboard_result,
                segments=segments, events=events)

            self.assertTrue(any(item["kind"] == "asset_revision" for item in snapshot["feedback_loops"]))
            self.assertTrue(any(item["kind"] == "board_revision" for item in snapshot["feedback_loops"]))
            self.assertTrue(any(item["kind"] == "storyboard_confirmation" for item in snapshot["feedback_loops"]))
            self.assertEqual(snapshot["references"][0]["tag"], "@host")
            self.assertEqual(len(snapshot["steps"]), 8)

    def test_generate_canvas_writes_html_json_and_renders_feedback_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "manifest.json")
            brief_path = os.path.join(tmp, "brief.json")
            storyboard_path = os.path.join(tmp, "storyboard_result.json")
            segments_path = os.path.join(tmp, "segments.json")
            events_path = os.path.join(tmp, "run.log")
            out_path = os.path.join(tmp, "canvas.html")

            self._write_json(manifest_path, {
                "client": "acme",
                "run_id": "run-1",
                "approvals": {"storyboard": False, "video": False, "captions": False,
                              "final": False, "derive": False},
                "generation": {"storyboard": {"status": "pending_approval"}},
                "identity": {"client": "acme", "asset_dir": os.path.join(ROOT, "assets", "acme")},
            })
            self._write_json(brief_path, {
                "client": "acme",
                "images": [
                    {"path": os.path.join(tmp, "source.png"), "tag": "hero", "status": "pending",
                     "edited_from": os.path.join(tmp, "source.png"),
                     "feedback_refs": [os.path.join(tmp, "feedback.png")],
                     "prompt": "修正人物姿态"},
                ],
            })
            self._write_json(storyboard_path, {
                "needs_confirmation": True,
                "cast_board": {"status": "pending", "path": os.path.join(tmp, "storyboard.jpg"),
                               "refinement": {"edit_prompt": "修正人物姿态",
                                              "feedback_refs": [os.path.join(tmp, "feedback.png")] }},
                "shots": [],
            })
            self._write_json(segments_path, {"segments": []})
            with open(events_path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": "2026-08-07T12:00:00", "event": "step_started"}) + "\n")

            snapshot = workflow_canvas.generate_canvas(
                out_path=out_path, manifest_path=manifest_path, brief_path=brief_path,
                storyboard_result_path=storyboard_path, segments_path=segments_path,
                events_path=events_path)

            self.assertTrue(os.path.isfile(out_path))
            self.assertTrue(os.path.isfile(os.path.splitext(out_path)[0] + ".json"))
            self.assertGreaterEqual(len(snapshot["feedback_loops"]), 2)
            with open(out_path, encoding="utf-8") as handle:
                html = handle.read()
            self.assertIn("Workflow Steps", html)
            self.assertIn("修正人物姿态", html)

    def test_runtime_records_comments_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "manifest.json")
            brief_path = os.path.join(tmp, "brief.json")
            storyboard_path = os.path.join(tmp, "storyboard_result.json")
            segments_path = os.path.join(tmp, "segments.json")
            out_path = os.path.join(tmp, "canvas.html")
            self._write_json(manifest_path, {
                "client": "acme",
                "run_id": "run-live",
                "approvals": {"storyboard": False, "video": False, "captions": False,
                              "final": False, "derive": False},
                "generation": {"storyboard": {"status": "pending_approval"}},
                "identity": {"client": "acme", "asset_dir": os.path.join(ROOT, "assets", "acme")},
            })
            self._write_json(brief_path, {"client": "acme", "images": []})
            self._write_json(storyboard_path, {"needs_confirmation": True, "shots": []})
            self._write_json(segments_path, {"segments": []})

            runtime = workflow_canvas.CanvasRuntime(
                out_path=out_path, manifest_path=manifest_path, brief_path=brief_path,
                storyboard_result_path=storyboard_path, segments_path=segments_path,
                events_path=None, history_limit=10)
            snapshot = runtime.snapshot(source="test", reason="seed")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "workflow_canvas_history.jsonl")))
            self.assertIn("history", snapshot)
            self.assertGreaterEqual(len(snapshot["history"]), 1)

            entry = runtime.record_comment("suggestion", "把背景再简洁一点")
            self.assertEqual(entry["kind"], "suggestion")
            history = runtime.history()
            self.assertTrue(any(
                item.get("source") == "interaction" and
                (item.get("interaction") or {}).get("text") == "把背景再简洁一点"
                for item in history))

            shell = workflow_canvas.render_live_shell(snapshot)
            self.assertIn("Live Preview", shell)
            self.assertIn("Interaction", shell)
            self.assertIn("historyList", shell)


if __name__ == "__main__":
    unittest.main()
