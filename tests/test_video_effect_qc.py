import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import video_effect_qc  # noqa: E402


def write_json(directory, name, value):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    return path


class VideoEffectQcTests(unittest.TestCase):
    def good_segment(self):
        return {
            "id": "s2",
            "text": "磁吸一贴",
            "visual": "手将音响底部磁吸面贴向手机背面，展示稳定吸附。",
            "audio_contract": {
                "track": "required",
                "dialogue": "磁吸一贴",
                "voice": "中文普通话年轻女声",
                "bgm": "潮流电子节拍",
                "voice_continuity_method": "text_contract_and_human_qc",
                "bgm_continuity_method": "post_mix_preferred",
                "media_reference_method": "basicrouter_video_v1_has_no_public_audio_reference_field",
            },
        }

    def good_review(self):
        seedance = (
            "Storyboard mode: use the uploaded Seedance-native storyboard/contact sheet. "
            "NEVER render the video as a sketch; final output is photorealistic live-action. "
            "Audio continuity: Voice method: text_contract_and_human_qc. "
            "Media reference method: basicrouter_video_v1_has_no_public_audio_reference_field. "
            "音响底部磁吸面贴合手机背面。"
        )
        kling = (
            "Storyboard mode: use the uploaded SINGLE 16:9 reference plate only. "
            "NEVER render the video as a sketch; final output is photorealistic live-action. "
            "Audio continuity: Voice method: text_contract_and_human_qc. "
            "Media reference method: basicrouter_video_v1_has_no_public_audio_reference_field. "
            "音响底部磁吸面贴合手机背面。"
        )
        return {
            "status": "confirmed",
            "stage": "video",
            "prompts": [{
                "shot_id": "s2",
                "prompt_zh": "P",
                "submission_prompt_zh": seedance,
                "model": "seedance-2.0",
                "model_submission_prompts": {
                    "seedance-2.0": seedance,
                    "kling-v3-omni-video": kling,
                },
            }],
        }

    def test_preflight_passes_with_confirmed_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [self.good_segment()]})
            review = write_json(directory, "review.json", self.good_review())
            report = video_effect_qc.build_report(segments, review)
        self.assertTrue(report["generation_ready"], report["errors"])
        self.assertTrue(report["passed"], report["errors"])
        self.assertIn("manifest_supplied", report["warnings"])

    def test_preflight_blocks_product_back_attachment_phrase(self):
        bad = self.good_segment()
        bad["visual"] = "手将产品贴近手机背面，产品背部贴合手机后松手。"
        review = self.good_review()
        review["prompts"][0]["model_submission_prompts"]["seedance-2.0"] += " 产品背部贴合手机。"
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [bad]})
            review_path = write_json(directory, "review.json", review)
            report = video_effect_qc.build_report(segments, review_path)
        self.assertFalse(report["passed"])
        self.assertIn("magnetic_bottom_to_phone_back_contract", report["errors"])

    def test_preflight_blocks_missing_storyboard_plan_shot(self):
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [self.good_segment()]})
            review = write_json(directory, "review.json", self.good_review())
            plan = write_json(directory, "plan.json", {"shots": [
                {"id": "s1"}, {"id": "s2"}]})
            report = video_effect_qc.build_report(segments, review, plan_path=plan)
        self.assertFalse(report["passed"])
        self.assertIn("segments_cover_storyboard_plan", report["errors"])
        self.assertIn("prompt_review_covers_expected_shots", report["errors"])
        regenerate = [item for item in report["next_actions"]
                      if item["code"] == "REGENERATE_STALE_STORYBOARD_SHOTS"]
        self.assertEqual(regenerate[0]["shot_ids"], ["s1"])
        self.assertTrue(regenerate[0]["requires_paid_generation"])
        self.assertTrue(any("capture-storyboard" in command
                            for command in regenerate[0]["commands"]))
        self.assertTrue(any("storyboard.py" in command and "gpt-image-2" in command
                            for command in regenerate[0]["commands"]))
        self.assertTrue(any("--only-shot s1" in command
                            for command in regenerate[0]["commands"]))
        recapture = [item for item in report["next_actions"]
                     if item["code"] == "RECAPTURE_VIDEO_PROMPTS"]
        self.assertTrue(recapture)
        self.assertEqual(recapture[0]["depends_on"], ["REFRESH_APPROVAL_CHAIN"])
        self.assertFalse(any(item["code"] == "CONFIRM_VIDEO_PROMPT_REVIEW"
                             for item in report["next_actions"]))

    def test_preflight_confirms_existing_regenerated_storyboard_instead_of_regenerating(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run-1")
            storyboard_dir = os.path.join(root, "storyboard", "run-1")
            os.makedirs(run_dir)
            os.makedirs(storyboard_dir)
            segments = write_json(run_dir, "segments.json", {"segments": [self.good_segment()]})
            review = write_json(run_dir, "review.json", self.good_review())
            plan = write_json(root, "plan.json", {"shots": [{"id": "s1"}, {"id": "s2"}]})
            s1 = os.path.join(storyboard_dir, "shot_01_s1.jpg")
            s2 = os.path.join(storyboard_dir, "shot_02_s2.jpg")
            for path in (s1, s2):
                with open(path, "wb") as handle:
                    handle.write(b"storyboard")
            write_json(storyboard_dir, "storyboard_result.json", {
                "needs_confirmation": True,
                "shots": [
                    {"id": "s1", "shot": {"id": "s1"}, "path": s1},
                    {"id": "s2", "shot": {"id": "s2"}, "path": s2},
                ],
            })
            report = video_effect_qc.build_report(segments, review, plan_path=plan)
        self.assertFalse(report["passed"])
        self.assertTrue(report["storyboard_result_status"]["covers_plan"])
        self.assertFalse(any(item["code"] == "REGENERATE_STALE_STORYBOARD_SHOTS"
                             for item in report["next_actions"]))
        confirm = [item for item in report["next_actions"]
                   if item["code"] == "CONFIRM_REGENERATED_STORYBOARD"]
        self.assertEqual(confirm[0]["shot_ids"], ["s1"])
        self.assertFalse(confirm[0]["requires_paid_generation"])

    def test_preflight_blocks_segments_file_declaring_missing_images(self):
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {
                "segments": [self.good_segment()],
                "missing_images": ["s1"],
            })
            review = write_json(directory, "review.json", self.good_review())
            report = video_effect_qc.build_report(segments, review)
        self.assertFalse(report["passed"])
        self.assertIn("segments_no_declared_missing_images", report["errors"])
        regenerate = [item for item in report["next_actions"]
                      if item["code"] == "REGENERATE_STALE_STORYBOARD_SHOTS"]
        self.assertEqual(regenerate[0]["shot_ids"], ["s1"])
        self.assertFalse(any(item["code"] == "CONFIRM_VIDEO_PROMPT_REVIEW"
                             for item in report["next_actions"]))

    def test_preflight_blocks_required_reference_type_mismatch(self):
        segment = self.good_segment()
        segment.update({
            "required_reference_types": ["product_board", "storyboard_composition"],
            "references": [
                {"type": "product_identity", "tag": "@product_hero"},
                {"type": "storyboard_composition", "tag": "@storyboard"},
            ],
        })
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [segment]})
            review = write_json(directory, "review.json", self.good_review())
            report = video_effect_qc.build_report(segments, review)
        self.assertFalse(report["passed"])
        self.assertIn("reference_handoff_complete", report["errors"])
        recompile = [item for item in report["next_actions"]
                     if item["code"] == "RECOMPILE_VIDEO_REFERENCES"]
        self.assertTrue(recompile)
        self.assertFalse(recompile[0]["requires_paid_generation"])

    def test_preflight_blocks_local_or_base64_video_image_urls(self):
        segment = self.good_segment()
        segment.update({
            "urls": ["/tmp/product.jpg", "data:image/png;base64,AAA"],
            "references": [
                {"type": "product_board", "url": "https://cdn.example/product.jpg",
                 "tag": "@product"},
                {"type": "storyboard_composition", "url": "/tmp/storyboard.jpg",
                 "tag": "@storyboard"},
            ],
        })
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [segment]})
            review = write_json(directory, "review.json", self.good_review())
            report = video_effect_qc.build_report(segments, review)
        self.assertFalse(report["passed"])
        self.assertIn("video_reference_urls_are_remote", report["errors"])
        restore = [item for item in report["next_actions"]
                   if item["code"] == "RESTORE_VIDEO_IMAGE_URLS"]
        self.assertTrue(restore)
        commands = "\n".join(restore[0]["commands"])
        self.assertIn("video_image_url_recovery.py", commands)
        self.assertIn("--plan-out", commands)
        self.assertIn("--fail-on-missing", commands)
        self.assertIn("video_effect_qc.py", commands)

    def test_preflight_blocks_stale_render_plan_storyboard_result(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run-1")
            storyboard_dir = os.path.join(root, "storyboard", "run-1")
            os.makedirs(run_dir)
            os.makedirs(storyboard_dir)
            current_result = os.path.join(storyboard_dir, "storyboard_result.json")
            shot_path = os.path.join(storyboard_dir, "shot_01_s2.jpg")
            with open(shot_path, "wb") as handle:
                handle.write(b"storyboard")
            write_json(storyboard_dir, "storyboard_result.json", {
                "needs_confirmation": False,
                "shots": [{"id": "s2", "shot": {"id": "s2"}, "path": shot_path}],
            })
            segment = self.good_segment()
            segment["render_plan"] = {
                "content": {
                    "status": "confirmed",
                    "storyboard_result": os.path.join(root, "storyboard", "run-1-old",
                                                       "storyboard_result.json"),
                }
            }
            segments = write_json(run_dir, "segments.json", {"segments": [segment]})
            review = write_json(run_dir, "review.json", self.good_review())
            plan = write_json(root, "plan.json", {"shots": [{"id": "s2"}]})
            report = video_effect_qc.build_report(segments, review, plan_path=plan)
        self.assertFalse(report["passed"])
        self.assertIn("render_plan_storyboard_result_current", report["errors"])
        mismatch = [check for check in report["checks"]
                    if check["id"] == "render_plan_storyboard_result_current"][0]
        self.assertEqual(mismatch["evidence"]["mismatches"][0]["expected"],
                         os.path.abspath(current_result))
        recapture = [item for item in report["next_actions"]
                     if item["code"] == "RECAPTURE_VIDEO_PROMPTS"]
        self.assertTrue(recapture)
        self.assertEqual(recapture[0]["depends_on"], ["REFRESH_APPROVAL_CHAIN"])

    def test_manifest_recovery_commands_refresh_full_approval_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [self.good_segment()]})
            review = write_json(directory, "review.json", self.good_review())
            plan = write_json(directory, "plan.json", {"shots": [{"id": "s2"}]})
            manifest = write_json(directory, "manifest.json", {"client": "acme"})
            report = video_effect_qc.build_report(
                segments, review, manifest_path=manifest, client="acme", plan_path=plan)
        refresh = [item for item in report["next_actions"]
                   if item["code"] == "REFRESH_APPROVAL_CHAIN"]
        commands = "\n".join(refresh[0]["commands"])
        for stage in ("script", "cast_board", "product_usage", "storyboard", "render_plan"):
            self.assertIn("--stage %s" % stage, commands)
        self.assertIn("run_manifest.py finish-stage", commands)
        self.assertIn("run_manifest.py approve", commands)
        self.assertIn("script_splitter.py split", commands)

    def test_magnetic_review_ignores_prompt_for_missing_segment(self):
        review = self.good_review()
        review["prompts"].append({
            "shot_id": "missing",
            "prompt_zh": "产品背部贴合手机。",
            "submission_prompt_zh": (
                "NEVER render the video as a sketch; final output is photorealistic live-action. "
                "Audio continuity: ok. 产品背部贴合手机。"),
            "model": "seedance-2.0",
            "model_submission_prompts": {
                "seedance-2.0": (
                    "NEVER render the video as a sketch; final output is photorealistic live-action. "
                    "Audio continuity: ok. 产品背部贴合手机。"),
                "kling-v3-omni-video": (
                    "NEVER render the video as a sketch; final output is photorealistic live-action. "
                    "Audio continuity: ok. 产品背部贴合手机。"),
            },
        })
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [self.good_segment()]})
            review_path = write_json(directory, "review.json", review)
            report = video_effect_qc.build_report(segments, review_path)
        self.assertNotIn("magnetic_bottom_to_phone_back_contract", report["errors"])

    def test_magnetic_review_ignores_product_only_feature_hook(self):
        segment = {
            "id": "s1",
            "text": "磁吸便携音箱开场",
            "visual": "产品静物特写，展示马卡龙黄色圆角造型。",
            "audio_contract": {
                "track": "required",
                "dialogue": "磁吸便携音箱开场",
                "voice_continuity_method": "text_contract_and_human_qc",
                "bgm_continuity_method": "post_mix_preferred",
                "media_reference_method": "basicrouter_video_v1_has_no_public_audio_reference_field",
            },
        }
        prompt = (
            "NEVER render the video as a sketch; final output is photorealistic live-action. "
            "Audio continuity: Voice method: text_contract_and_human_qc. "
            "Media reference method: basicrouter_video_v1_has_no_public_audio_reference_field. "
            "产品卖点可磁吸手机背面，但本镜头只展示产品静物。"
        )
        review = {
            "status": "confirmed",
            "stage": "video",
            "prompts": [{
                "shot_id": "s1",
                "prompt_zh": prompt,
                "submission_prompt_zh": prompt,
                "model": "seedance-2.0",
                "model_submission_prompts": {
                    "seedance-2.0": prompt,
                    "kling-v3-omni-video": prompt,
                },
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            segments = write_json(directory, "segments.json", {"segments": [segment]})
            review_path = write_json(directory, "review.json", review)
            report = video_effect_qc.build_report(segments, review_path)
        self.assertNotIn("magnetic_bottom_to_phone_back_contract", report["errors"])

    def test_post_requires_results_and_manual_design_review(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "s2.mp4")
            with open(video, "wb") as handle:
                handle.write(b"video")
            segments = write_json(directory, "segments.json", {"segments": [self.good_segment()]})
            review = write_json(directory, "review.json", self.good_review())
            results = write_json(directory, "results.json", [{
                "ok": True, "segment_id": "s2", "localPath": video,
                "ocr_warning": False, "media_qc": {"passed": True},
            }])
            missing_manual = video_effect_qc.build_report(
                segments, review, mode="post", results_path=results)
            self.assertFalse(missing_manual["passed"])
            self.assertIn("manual_design_review_supplied", missing_manual["errors"])
            manual = write_json(directory, "manual.json", {
                "status": "accepted",
                "checks": {key: True for key in video_effect_qc.REQUIRED_MANUAL_CHECKS},
            })
            passed = video_effect_qc.build_report(
                segments, review, mode="post", results_path=results,
                manual_review_path=manual)
        self.assertTrue(passed["passed"], passed["errors"])


if __name__ == "__main__":
    unittest.main()
