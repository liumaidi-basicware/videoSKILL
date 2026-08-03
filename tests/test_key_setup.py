import os
import stat
import sys
import tempfile
import unittest
import io
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import key_setup  # noqa: E402


class KeySetupTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.env = {key_setup.SESSION_ENV: "agent-a-session"}
        self.cache_patch = mock.patch.object(
            key_setup, "SESSION_DIR", os.path.join(self.cache.name, "sessions")
        )
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.cache.cleanup()

    def test_sessions_are_isolated(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            path = key_setup.save_key("sk-a")
            self.assertEqual(key_setup.load_key(), "sk-a")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertIsNone(key_setup.load_key("agent-b-session"))

    def test_missing_session_does_not_read_global_key(self):
        with mock.patch.dict(os.environ, {"BASICROUTER_API_KEY": "sk-global"}, clear=True):
            self.assertIsNone(key_setup.load_key())

    def test_global_env_key_requires_explicit_opt_in(self):
        with mock.patch.dict(
            os.environ,
            {"BASICROUTER_API_KEY": "sk-global", "BR_ALLOW_GLOBAL_KEY": "1"},
            clear=True,
        ):
            self.assertEqual(key_setup.load_key(), "sk-global")

    def test_init_accepts_unknown_host_without_blocking_core_flow(self):
        with mock.patch.object(key_setup.agent_runtime, "detect_agent_runtime",
                               return_value={"name": "unknown", "source": "unknown"}), \
                mock.patch("builtins.print") as output:
            self.assertEqual(key_setup.main(["init", "--host-session-id", "opaque-1"]), 0)
        session = output.call_args.args[0]
        self.assertTrue(session.startswith("br-unknown-"))
        self.assertNotIn("opaque-1", session)

    def test_ensure_session_id_is_stable_and_inherited_by_children(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            first = key_setup.ensure_session_id("opaque-host-session")
            second = key_setup.ensure_session_id("different-host-session")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("br-"))

    def test_get_masks_key_and_stdin_save_avoids_argv_secret(self):
        with mock.patch.dict(os.environ, self.env, clear=False), \
                mock.patch.object(key_setup, "_validate", return_value=(True, "ok")), \
                mock.patch.object(sys, "stdin", io.StringIO("sk-secret-value\n")), \
                mock.patch("builtins.print") as output:
            self.assertEqual(key_setup.main(["save", "--stdin"]), 0)
            self.assertEqual(key_setup.main(["get"]), 0)
        printed = [call.args[0] for call in output.call_args_list]
        self.assertNotIn("sk-secret-value", printed)
        self.assertIn("sk-s...alue", printed)

    def test_save_rejects_symlink_key_target(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            path = key_setup.session_key_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            outside = os.path.join(self.cache.name, "outside")
            with open(outside, "w") as handle:
                handle.write("safe")
            os.symlink(outside, path)
            with self.assertRaisesRegex(ValueError, "SYMLINK"):
                key_setup.save_key("sk-new")
            with open(outside) as handle:
                self.assertEqual(handle.read(), "safe")


if __name__ == "__main__":
    unittest.main()
