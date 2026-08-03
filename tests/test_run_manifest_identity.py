import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_manifest  # noqa: E402


class RunManifestIdentityTests(unittest.TestCase):
    def test_save_rejects_client_asset_directory_mismatch(self):
        manifest = run_manifest.create_manifest("acme", "run1")
        manifest["identity"]["asset_dir"] = os.path.join(ROOT, "assets", "other")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "RUN_IDENTITY_MISMATCH"):
                run_manifest.save_manifest(manifest, os.path.join(directory, "run.json"))

    def test_manifest_records_agent_without_requiring_kilo(self):
        with mock.patch.dict(os.environ, {
                "BASICROUTER_AGENT_RUNTIME": "custom-agent"}, clear=True):
            manifest = run_manifest.create_manifest("acme", "run1")
        self.assertEqual(
            {"name": "custom-agent", "source": "BASICROUTER_AGENT_RUNTIME"},
            manifest["identity"]["agent_runtime"])

    def test_save_allows_agent_handoff_and_records_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "run.json")
            with mock.patch.dict(os.environ, {
                    "BASICROUTER_AGENT_RUNTIME": "codex"}, clear=True):
                manifest = run_manifest.create_manifest("acme", "run1")
            with mock.patch.dict(os.environ, {
                    "BASICROUTER_AGENT_RUNTIME": "hermes"}, clear=True):
                run_manifest.save_manifest(manifest, path)
            self.assertEqual("hermes", manifest["identity"]["agent_runtime"]["name"])
            self.assertEqual(
                ["codex", "hermes"],
                [item["name"] for item in
                 manifest["identity"]["agent_runtime_history"]])


if __name__ == "__main__":
    unittest.main()
