import os
import sys
import unittest
import json
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import br_client  # noqa: E402


class LegacyVideoModelAliasTests(unittest.TestCase):
    def test_known_canonical_seedance_id_maps_to_legacy_name(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[]):
            self.assertEqual(
                br_client._legacy_video_model_name("dreamina-seedance-2-0-260128"),
                "seedance-2.0")

    def test_live_catalog_name_wins_over_fallback(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[{
            "id": "canonical-seedance", "modelName": "seedance-2.0"
        }]):
            self.assertEqual(
                br_client._legacy_video_model_name("canonical-seedance"),
                "seedance-2.0")

    def test_legacy_candidates_prefer_provider_model_name_over_internal_id(self):
        with mock.patch.object(br_client, "list_video_models", return_value=[{
            "modelId": "kling-v3-omni",
            "modelName": "kling-v3-omni-video",
            "online": True,
        }]):
            self.assertEqual(
                br_client._legacy_video_model_candidates("kling-v3-omni"),
                ["kling-v3-omni-video", "kling-v3-omni"])

    def test_create_video_retries_catalog_name_only_after_model_not_found(self):
        calls = []

        def request(_method, _path, **kwargs):
            calls.append(kwargs["body"]["model"])
            if len(calls) == 1:
                raise br_client.BRError(
                    "HTTP 400: Model not found: kling-v3-omni",
                    payload={"message": "Model not found: kling-v3-omni"},
                    http_status=400)
            return {"code": 200, "data": {"taskId": "task-kling"}}

        with mock.patch.object(br_client, "list_video_models", return_value=[{
            "modelId": "kling-v3-omni",
            "modelName": "kling-v3-omni-video",
        }]), mock.patch.object(br_client, "_request", side_effect=request):
            self.assertEqual(
                br_client.create_video("sk-test", "hello", model="kling-v3-omni"),
                "task-kling")
        self.assertEqual(calls, ["kling-v3-omni-video", "kling-v3-omni"])


if __name__ == "__main__":
    unittest.main()
