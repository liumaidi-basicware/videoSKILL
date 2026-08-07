import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run_manifest  # noqa: E402
import script_splitter  # noqa: E402


class SplitAtomicityTests(unittest.TestCase):
    def test_formal_gate_failure_does_not_overwrite_existing_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = os.path.join(directory, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as handle:
                json.dump({"shots": [{"id": "s1", "duration": 3, "visual": "x"}]}, handle)
            manifest = run_manifest.create_manifest("acme", "run-1", plan_path=plan_path)
            manifest_path = os.path.join(directory, "run_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            out_path = os.path.join(directory, "segments.json")
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write('{"sentinel": true}\n')

            with self.assertRaisesRegex(ValueError, "GENERATION_BLOCKED"):
                script_splitter.main([
                    "split", "--plan", plan_path, "--client", "acme",
                    "--run-id", "run-1", "--manifest", manifest_path,
                    "--out", out_path,
                ])

            with open(out_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), '{"sentinel": true}\n')


if __name__ == "__main__":
    unittest.main()
