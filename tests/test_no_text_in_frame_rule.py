#!/usr/bin/env python3
"""0-1 单测：AGENTS.md 铁律#15（背景禁字幕 + 动画元素归口 HyperFrames）落地校验。

覆盖 scripts/storyboard.py shot_prompt() 与 scripts/video_engine.py DEFAULT_NEGATIVE：
  ① shot_prompt 的正向 prompt 明确声明「画面背景严禁出现任何文字」（含 kinetic
     typography / floating slogan / 悬浮文字等具体表述），且指向 HyperFrames 后期层。
  ② shot.motion_elements 仅作为后期交接数据，不把具体文字内容发给图像模型。
  ③ 没有 motion_elements 时不产出多余的 motion layer 块（不污染无动效镜头的 prompt）。
  ④ negative constraints 里显式列出字幕/文字/kinetic typography 等压制词。
  ⑤ video_engine.DEFAULT_NEGATIVE 同步压制这些字词（视频生成侧的兜底）。

纯本地字符串断言，无网络调用。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import storyboard as sb    # noqa: E402
import video_engine as ve  # noqa: E402


def _plan_shot(motion_elements=None):
    plan = {"project_title": "demo", "characters": [
        {"id": "h", "name": "主播", "costume": "红裙", "hair": "长直发", "appearance": "亲和"}]}
    shot = {"id": "s1", "duration": 3, "dialogue": "你好", "characters": ["h"]}
    if motion_elements is not None:
        shot["motion_elements"] = motion_elements
    return plan, shot


class TestNoTextInFrameConstraint(unittest.TestCase):
    def test_prompt_declares_no_text_in_frame(self):
        plan, shot = _plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("ABSOLUTELY NO TEXT IN FRAME", p)
        self.assertIn("画面背景严禁出现任何文字", p)

    def test_prompt_forbids_kinetic_typography_and_slogans(self):
        plan, shot = _plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("kinetic", p.lower())
        self.assertIn("floating slogan text", p)
        self.assertIn("data/metric label callouts", p)

    def test_prompt_points_to_hyperframes_postproduction_layer(self):
        plan, shot = _plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("HyperFrames", p)
        self.assertIn("post-production", p.lower())

    def test_prompt_allows_physical_prop_text_exception(self):
        plan, shot = _plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        # 唯一允许的例外：产品包装/截图上客观存在的原生文字
        self.assertIn("pre-existing part of a real prop", p)

    def test_negative_constraints_include_text_terms(self):
        plan, shot = _plan_shot()
        p = sb.shot_prompt(plan, shot, 1)
        neg_line = [l for l in p.split("\n") if l.startswith("Negative constraints:")][0]
        for term in ("字幕", "文字", "水印", "logo", "kinetic typography", "悬浮文字", "数据标签"):
            self.assertIn(term, neg_line)


class TestMotionElementsField(unittest.TestCase):
    def test_motion_elements_are_not_sent_to_image_model(self):
        plan, shot = _plan_shot(motion_elements=[
            "slogan逐字打字机效果：一次计费，一次集成，无限可能",
            "模型名kinetic快闪：kimi-k2.5/minimax-m.5/qwen:3-n",
        ])
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("post-production motion layer", p)
        self.assertNotIn("slogan逐字打字机效果", p)
        self.assertNotIn("kimi-k2.5", p)

    def test_motion_elements_string_normalized_to_list(self):
        plan, shot = _plan_shot(motion_elements="单条动效描述")
        p = sb.shot_prompt(plan, shot, 1)
        self.assertIn("post-production motion layer", p)
        self.assertNotIn("单条动效描述", p)

    def test_no_motion_elements_no_extra_block(self):
        plan, shot = _plan_shot()  # 不传 motion_elements
        p = sb.shot_prompt(plan, shot, 1)
        self.assertNotIn("POST-PRODUCTION MOTION LAYER", p)

    def test_empty_motion_elements_list_no_extra_block(self):
        plan, shot = _plan_shot(motion_elements=[])
        p = sb.shot_prompt(plan, shot, 1)
        self.assertNotIn("POST-PRODUCTION MOTION LAYER", p)


class TestVideoEngineDefaultNegative(unittest.TestCase):
    def test_default_negative_suppresses_text_and_kinetic_typography(self):
        for term in ("字幕", "文字", "水印", "kinetic typography", "悬浮文字",
                    "逐字动画文字", "数据标签快闪", "字幕条", "slogan文字"):
            self.assertIn(term, ve.DEFAULT_NEGATIVE)

    def test_storyboard_rules_explain_annotation_colors_without_rendering_them(self):
        rules = ve.STORYBOARD_VIDEO_RULES
        for term in ("RED arrows = body / subject movement",
                     "BLUE arrows = camera movement",
                     "GREEN marks = framing and composition notes",
                     "ORANGE marks = lighting direction",
                     "PURPLE marks = sound and emotional emphasis",
                     "BLACK text = short shot notes and panel labels"):
            self.assertIn(term, rules)
        self.assertIn("Do NOT render any arrows", rules)

    def test_storyboard_negative_blocks_annotation_artifacts(self):
        negative = ve._storyboard_negative("")
        for term in ("红色箭头", "蓝色箭头", "绿色构图标记", "橙色灯光标记",
                     "紫色声音标记", "黑色镜头笔记", "面板标签"):
            self.assertIn(term, negative)


if __name__ == "__main__":
    unittest.main()
