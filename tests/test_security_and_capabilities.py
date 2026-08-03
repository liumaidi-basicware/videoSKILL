#!/usr/bin/env python3
"""Regression tests for input validation and capability reporting."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import br_client  # noqa: E402
import project_utils  # noqa: E402
import setup_env  # noqa: E402


class ClientValidationTests(unittest.TestCase):
    def test_valid_client_slug(self):
        self.assertEqual(project_utils.validate_client("hotel_hk-2"), "hotel_hk-2")

    def test_rejects_path_traversal_and_uppercase(self):
        for value in ("../acme", "Acme", "acme/other", "-acme", "acme-"):
            with self.assertRaises(ValueError):
                project_utils.validate_client(value)

    def test_rejects_path_traversal_in_project_components(self):
        for label, value in (("sku", "../../other/product"),
                             ("actor", "../other-host"),
                             ("run_id", "run/child")):
            with self.assertRaises(ValueError):
                project_utils.validate_component(value, label)


class KeyValidationTests(unittest.TestCase):
    def test_public_model_list_alone_does_not_validate_key(self):
        def request(method, path, api_key=None, body=None, query=None, timeout=120, **kwargs):
            if method == "GET":
                return [{"modelName": "qwen3.6-plus", "online": True, "status": True}]
            raise br_client.BRError("401 invalid key")

        with mock.patch.object(br_client, "_request", side_effect=request):
            ok, message = br_client.validate_key("sk-invalid")
        self.assertFalse(ok)
        self.assertIn("401", message)

    def test_authenticated_chat_response_validates_key(self):
        def request(method, path, api_key=None, body=None, query=None, timeout=120, **kwargs):
            if method == "GET":
                return [{"modelName": "qwen3.6-plus", "online": True, "status": True}]
            return {"choices": [{"message": {"content": "pong"}}]}

        with mock.patch.object(br_client, "_request", side_effect=request):
            self.assertEqual(br_client.validate_key("sk-valid"), (True, "ok"))


class CapabilityStatusTests(unittest.TestCase):
    def test_capability_status_is_machine_readable(self):
        with mock.patch.object(setup_env, "check", return_value=[]), \
             mock.patch.object(setup_env, "verify_hyperframes_engine", return_value=(True, True)), \
             mock.patch.object(setup_env, "verify_video_engines", return_value=(False, True)), \
             mock.patch.object(setup_env, "verify_ffmpeg", return_value=True), \
             mock.patch.object(setup_env, "_importable", return_value=True):
            status = setup_env.capability_status()
        self.assertEqual(status["python_core"], True)
        self.assertEqual(status["hyperframes"], True)
        self.assertEqual(status["remotion"], False)
        self.assertIn("remotion_chrome", status)
        self.assertIn("dependencies_ready", status)
        self.assertIn("ocr", status)


if __name__ == "__main__":
    unittest.main()
