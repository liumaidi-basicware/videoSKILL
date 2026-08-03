#!/usr/bin/env python3
"""0-1 单测：storyboard.py load_plan_json() 的人话错误定位（铁律#14）。

真实场景复现：客户/Codex 在编写 storyboard_plan.json 时漏了一个收尾引号
（例如某个 panel_plan 字符串末尾少了 "），裸 json.load() 只会抛出偏移量式的
JSONDecodeError，既无法直接定位到第几行，又是英文技术堆栈，违反 AGENTS.md
铁律#14「错误说人话，不甩技术术语」。load_plan_json() 必须：
1. 精确报出出错的行号/列号；
2. 附带该行原文 + 列指示箭头，便于直接定位修复；
3. 给出人话的常见原因提示；
4. 包成 br_client.BRError，不是裸 JSONDecodeError。

无网络调用，无 API key 依赖。
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import storyboard  # noqa: E402
import br_client  # noqa: E402

VALID_PLAN = {
    "project_title": "test project",
    "client": "acme",
    "aspect_ratio": "9:16",
    "characters": [{"id": "host", "name": "host"}],
    "shots": [
        {"id": "s1", "duration": 8, "dialogue": "hello",
         "panel_plan": ["1 establish", "2 enter", "3 medium"]},
    ],
}


def _write(tmpdir, name, text):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestLoadPlanJsonHappyPath(unittest.TestCase):
    def test_valid_plan_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(d, "plan.json", json.dumps(VALID_PLAN, ensure_ascii=False, indent=2))
            plan = storyboard.load_plan_json(path)
            self.assertEqual(plan["client"], "acme")
            self.assertEqual(len(plan["shots"]), 1)


class TestLoadPlanJsonMissingFile(unittest.TestCase):
    def test_missing_file_raises_friendly_brerror(self):
        with self.assertRaises(br_client.BRError) as ctx:
            storyboard.load_plan_json("/tmp/definitely_does_not_exist_storyboard_plan.json")
        msg = str(ctx.exception)
        self.assertIn("剧本文件不存在", msg)
        self.assertNotIn("Traceback", msg)


class TestLoadPlanJsonSyntaxErrors(unittest.TestCase):
    def _assert_friendly(self, raw_text, tmpdir_name="bad.json"):
        with tempfile.TemporaryDirectory() as d:
            path = _write(d, tmpdir_name, raw_text)
            with self.assertRaises(br_client.BRError) as ctx:
                storyboard.load_plan_json(path)
            msg = str(ctx.exception)
            # 必须报行号
            self.assertRegex(msg, r"第 \d+ 行")
            # 必须报列号
            self.assertRegex(msg, r"第 \d+ 列")
            # 必须给出人话常见原因
            self.assertIn("常见原因", msg)
            # 不能是裸 Python 异常堆栈
            self.assertNotIn("Traceback (most recent call last)", msg)
            self.assertNotIn("JSONDecodeError", msg)
            return msg

    def test_missing_closing_quote_reports_correct_line(self):
        # 真实复现：panel_plan 数组里某一项漏了收尾引号
        good = json.dumps(VALID_PLAN, ensure_ascii=False, indent=2)
        lines = good.split("\n")
        target_idx = None
        for i, line in enumerate(lines):
            if "2 enter" in line:
                target_idx = i
                break
        self.assertIsNotNone(target_idx, "fixture 里没找到目标行，测试假设失效")
        # 去掉收尾引号，制造 unterminated string
        broken = lines[target_idx].rstrip()
        self.assertTrue(broken.endswith('",') or broken.endswith('"'))
        if broken.endswith('",'):
            lines[target_idx] = broken[:-2] + ","
        else:
            lines[target_idx] = broken[:-1]
        raw = "\n".join(lines)
        msg = self._assert_friendly(raw)
        # 出错行号必须命中目标行（1-indexed）附近，而不是整份文件报同一个默认行
        self.assertIn("第 %d 行" % (target_idx + 1), msg)

    def test_trailing_comma_reports_error(self):
        raw = '{\n  "a": 1,\n  "b": 2,\n}\n'
        self._assert_friendly(raw)

    def test_missing_comma_between_fields_reports_error(self):
        raw = '{\n  "a": 1\n  "b": 2\n}\n'
        self._assert_friendly(raw)


if __name__ == "__main__":
    unittest.main()
