#!/usr/bin/env python3
"""v21 单元测试 —— script_splitter.derive_captions() 连续性反推字幕/动效脚本。

背景（本轮流程断点）：底片(basecut.mp4)拼完后，此前没有任何一步把「剧本各段台词 + 各段
实际时长」连续性反推成字幕时间轴(SRT/lines.json)和动效脚本骨架，导致 subtitle_overlay
需要的 lines.json 只能人工手写。derive_captions 按 segments 顺序累加时间轴产出三样东西，
并标记 needs_confirmation=True（不自动往下走，等用户确认时间轴/断句/动效关键词）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import script_splitter as ss


def _segs():
    return {"segments": [
        {"id": "s1", "dialogue": "大家好。今天介绍新品。", "duration": 4},
        {"id": "s2", "dialogue": "它超薄超轻。", "duration": 3},
        {"id": "s3", "dialogue": "", "duration": 2},          # 纯画面段，无台词
        {"id": "s4", "dialogue": "现在下单立减一百。", "duration": 3},
    ]}


class TestDeriveCaptions(unittest.TestCase):

    def test_timeline_is_continuous_and_cumulative(self):
        r = ss.derive_captions(_segs())
        self.assertTrue(r["ok"])
        self.assertTrue(r["needs_confirmation"])   # 不自动往下走
        self.assertEqual(r["total_seconds"], 12.0)  # 4+3+2+3
        # 逐句字幕时间轴单调不重叠、首句从 0 开始
        lines = r["lines"]
        self.assertEqual(lines[0]["start"], 0.0)
        for a, b in zip(lines, lines[1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-6)

    def test_empty_dialogue_segment_makes_no_caption_but_keeps_motion_window(self):
        r = ss.derive_captions(_segs())
        # s3 无台词 → 不产字幕，但 motion_plan 仍保留其时间窗
        seg_ids_with_caption = {l["seg_id"] for l in r["lines"]}
        self.assertNotIn("s3", seg_ids_with_caption)
        motion_ids = {m["seg_id"] for m in r["motion_plan"]}
        self.assertEqual(motion_ids, {"s1", "s2", "s3", "s4"})
        s3 = next(m for m in r["motion_plan"] if m["seg_id"] == "s3")
        self.assertEqual(s3["start"], 7.0)   # 4+3
        self.assertEqual(s3["end"], 9.0)     # +2

    def test_per_sentence_splits_multi_sentence_dialogue(self):
        r = ss.derive_captions(_segs(), per_sentence=True)
        s1_lines = [l for l in r["lines"] if l["seg_id"] == "s1"]
        # "大家好。今天介绍新品。" → 两句
        self.assertEqual(len(s1_lines), 2)
        self.assertEqual(s1_lines[0]["start"], 0.0)
        self.assertAlmostEqual(s1_lines[-1]["end"], 4.0, places=3)

    def test_whole_segment_mode_one_caption_per_segment(self):
        r = ss.derive_captions(_segs(), per_sentence=False)
        s1_lines = [l for l in r["lines"] if l["seg_id"] == "s1"]
        self.assertEqual(len(s1_lines), 1)
        self.assertEqual(s1_lines[0]["start"], 0.0)
        self.assertEqual(s1_lines[0]["end"], 4.0)

    def test_srt_format_valid(self):
        r = ss.derive_captions(_segs(), per_sentence=False)
        srt = r["srt"]
        self.assertIn("00:00:00,000 --> 00:00:04,000", srt)
        # 序号从 1 递增
        self.assertTrue(srt.startswith("1\n"))

    def test_lines_feed_subtitle_overlay_build_scenes(self):
        """反推出的 lines 必须能被 subtitle_overlay.build_scenes 直接吃下（字段契约对齐）。"""
        import subtitle_overlay as so
        r = ss.derive_captions(_segs())
        sz = {"safe_zone": {"bottom_px": 200, "left_px": 40, "right_px": 40, "max_height_px": 300}}
        spec = so.build_scenes(r["lines"], sz, width=1080, height=1920, fps=30)
        self.assertIn("scenes", spec)
        self.assertEqual(len(spec["scenes"]), len(r["lines"]))

    def test_empty_segments_raises(self):
        with self.assertRaises(ValueError):
            ss.derive_captions({"segments": []})

    def test_srt_timestamp_formatting(self):
        self.assertEqual(ss._fmt_srt_ts(0), "00:00:00,000")
        self.assertEqual(ss._fmt_srt_ts(3661.5), "01:01:01,500")


if __name__ == "__main__":
    unittest.main()
