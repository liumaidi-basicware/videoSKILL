import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import agent_runtime  # noqa: E402


class AgentRuntimeTests(unittest.TestCase):
    def test_explicit_runtime_supports_unlisted_agents(self):
        self.assertEqual(
            {"name": "future-agent", "source": "explicit"},
            agent_runtime.detect_agent_runtime("Future Agent", {}))

    def test_canonical_environment_precedes_host_signals(self):
        runtime = agent_runtime.detect_agent_runtime(environ={
            "BASICROUTER_AGENT_RUNTIME": "company-agent",
            "KILO_SESSION_ID": "kilo-session",
        })
        self.assertEqual("company-agent", runtime["name"])
        self.assertEqual("BASICROUTER_AGENT_RUNTIME", runtime["source"])

    def test_known_hosts_are_detected_from_runtime_signals(self):
        cases = (
            ("kilo", {"KILO_SESSION_ID": "1"}),
            ("codex", {"CODEX_THREAD_ID": "1"}),
            ("hermes", {"HERMES_SESSION_ID": "1"}),
        )
        for expected, environ in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    expected,
                    agent_runtime.detect_agent_runtime(environ=environ)["name"])

    def test_unknown_runtime_is_valid_fallback(self):
        self.assertEqual(
            {"name": "unknown", "source": "unknown"},
            agent_runtime.detect_agent_runtime(environ={}))


if __name__ == "__main__":
    unittest.main()
