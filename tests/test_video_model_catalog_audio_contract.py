import os
import sys
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import artifact_contract  # noqa: E402
import script_splitter  # noqa: E402
import video_engine  # noqa: E402


class ModelCatalogTests(unittest.TestCase):
    def test_known_model_uses_offline_audio_contract_when_catalog_omits_field(self):
        catalog = video_engine._normalize_model_catalog([{
            "modelId": "kling-v3-omni-video", "modelName": "kling-v3-omni-video",
            "online": True, "status": True, "allowVideoType": [1, 2, 3, 4, 5],
            "imageCount": 7,
        }])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            self.assertEqual(video_engine._pick_video_model(
                "kling-v3-omni-video", 5, dialogue="台词", formal=True,
                reference_count=4), "kling-v3-omni-video")

    def test_seedance_catalog_alias_supplies_integrated_audio_contract(self):
        catalog = video_engine._normalize_model_catalog([{
            "modelId": "dreamina-seedance-2-0-260128",
            "modelName": "seedance-2.0",
            "online": True,
            "status": True,
            "allowVideoType": [1, 2, 3, 5],
        }])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            self.assertEqual(video_engine._pick_video_model(
                "seedance-2.0", 5, dialogue="台词", formal=True),
                "dreamina-seedance-2-0-260128")

    def test_normalizes_aliases_duplicates_and_json_capabilities(self):
        catalog = video_engine._normalize_model_catalog([
            {"modelId": "provider/seedance-v2", "modelName": "seedance-2.0",
             "alias": '["seedance", "sd2"]', "online": "true", "status": "active",
             "allowVideoType": "[1, 2, 3, 5]", "integratedAudio": "true"},
            {"modelId": "provider/seedance-v2", "modelName": "seedance-2.0",
             "aliases": ["sd2"], "online": True, "status": True,
             "allowVideoType": [1, 2, 3, 5], "integratedAudio": True},
        ])
        record = catalog["records"]["provider/seedance-v2"]
        self.assertEqual(record["allow_types"], {1, 2, 3, 5})
        self.assertTrue(record["integrated_audio"])
        self.assertEqual(catalog["aliases"]["seedance-2.0"], {"provider/seedance-v2"})

    def test_picks_canonical_id_and_rejects_ambiguous_alias(self):
        catalog = video_engine._normalize_model_catalog([
            {"modelId": "provider/seedance-v2", "modelName": "seedance-2.0",
             "online": True, "status": True, "allowVideoType": [1, 5],
             "integratedAudio": True},
        ])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            self.assertEqual(video_engine._pick_video_model("seedance-2.0", 5),
                             "provider/seedance-v2")

        ambiguous = video_engine._normalize_model_catalog([
            {"modelId": "a/seedance", "modelName": "seedance-2.0", "online": True,
             "status": True, "allowVideoType": [1], "integratedAudio": True},
            {"modelId": "b/seedance", "modelName": "seedance-2.0", "online": True,
             "status": True, "allowVideoType": [1], "integratedAudio": True},
        ])
        with mock.patch.object(video_engine, "_model_catalog", return_value=ambiguous):
            with self.assertRaisesRegex(ValueError, "AMBIGUOUS_MODEL_ALIAS"):
                video_engine._pick_video_model("seedance-2.0", 1)

    def test_duplicate_capability_conflict_fails_closed(self):
        catalog = video_engine._normalize_model_catalog([
            {"modelId": "x/model", "modelName": "seedance-2.0", "online": True,
             "status": True, "allowVideoType": [1], "integratedAudio": True},
            {"modelId": "x/model", "modelName": "seedance-2.0", "online": True,
             "status": True, "allowVideoType": [1, 5], "integratedAudio": True},
        ])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            with self.assertRaisesRegex(ValueError, "NO_CAPABLE_VIDEO_MODEL"):
                video_engine._pick_video_model("seedance-2.0", 1)

    def test_formal_dialogue_rejects_wan_and_unknown_audio(self):
        catalog = video_engine._normalize_model_catalog([
            {"modelId": "wan/2.7", "modelName": "wan2.7-i2v", "online": True,
             "status": True, "allowVideoType": "[1,2]", "integratedAudio": False},
            {"modelId": "mystery/video", "modelName": "mystery-video", "online": True,
             "status": True, "allowVideoType": [1]},
        ])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            with self.assertRaisesRegex(ValueError, "NO_CAPABLE_VIDEO_MODEL"):
                video_engine._pick_video_model(video_type=1, dialogue="hello", formal=True)
            self.assertEqual(video_engine._pick_video_model("wan2.7-i2v", 1), "wan/2.7")

    def test_no_fallback_still_canonicalizes_and_validates(self):
        catalog = video_engine._normalize_model_catalog([
            {"modelId": "provider/seedance", "modelName": "seedance-2.0",
             "online": True, "status": True, "allowVideoType": [1],
             "integratedAudio": True},
            {"modelId": "provider/kling", "modelName": "kling-v3-omni-video",
             "online": True, "status": True, "allowVideoType": [4],
             "integratedAudio": True},
        ])
        with mock.patch.object(video_engine, "_model_catalog", return_value=catalog):
            with self.assertRaisesRegex(ValueError, "NO_CAPABLE_VIDEO_MODEL"):
                video_engine._pick_video_model(
                    "seedance-2.0", 4, allow_fallback=False)
            self.assertEqual(video_engine._pick_video_model(
                "kling-v3-omni-video", 4, allow_fallback=False), "provider/kling")


class AudioContractTests(unittest.TestCase):
    def test_split_propagates_explicit_audio_contract(self):
        plan = {"language": "zh-CN", "audio": {"bgm": "light"}, "shots": [{
            "id": "s1", "duration": 3, "dialogue": "你好", "characters": ["host"],
            "audio": {"voice": "warm", "sfx": "click", "lip_sync": True},
            "asset_refs": {"product_images": ["/tmp/product.png"]},
        }]}
        segment = script_splitter.split(
            plan, client="test", allow_unconfirmed=True)["segments"][0]
        self.assertEqual({key: segment["audio_contract"][key] for key in (
            "track", "speech", "dialogue", "language", "voice", "bgm", "sfx", "lip_sync")}, {
            "track": "required", "speech": True, "dialogue": "你好",
            "language": "zh-CN", "voice": "warm", "bgm": "light",
            "sfx": "click", "lip_sync": True,
        })
        self.assertIn("voice_continuity", segment["audio_contract"])
        self.assertIn("bgm_continuity", segment["audio_contract"])
        self.assertIn("sfx_continuity", segment["audio_contract"])
        self.assertEqual(segment["audio_contract"]["voice_continuity_method"],
                         "text_contract_and_human_qc")
        self.assertEqual(segment["audio_contract"]["bgm_continuity_method"],
                         "post_mix_preferred")
        self.assertEqual(
            segment["audio_contract"]["media_reference_method"],
            "basicrouter_video_v1_has_no_public_audio_reference_field")

    def test_audio_contract_is_handoff_bound(self):
        segment = {"id": "s1", "dialogue": "hello", "audio_contract": {
            "track": "required", "speech": True, "dialogue": "hello",
            "language": "en", "voice": "warm", "bgm": None, "sfx": None,
            "lip_sync": True}}
        first = artifact_contract.build_video_handoff(segment)["fingerprint"]
        segment["audio_contract"]["voice"] = "bright"
        self.assertNotEqual(first, artifact_contract.build_video_handoff(segment)["fingerprint"])

    def test_qc_reads_only_audio_contract(self):
        with mock.patch.object(video_engine.media_qc, "check",
                               return_value={"passed": True, "media": {}}) as check:
            video_engine._media_qc_guard("/tmp/a.mp4", {"text": "spoken text"}, draft=True)
            self.assertFalse(check.call_args.kwargs["audio_required"])
            video_engine._media_qc_guard(
                "/tmp/b.mp4", {"audio_contract": {"track": "required"}}, draft=True)
            self.assertTrue(check.call_args.kwargs["audio_required"])


if __name__ == "__main__":
    unittest.main()
