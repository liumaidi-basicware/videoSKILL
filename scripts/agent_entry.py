#!/usr/bin/env python3
"""Host-neutral preparation helper for the /basicrouter-video conversation entry."""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import key_setup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _run(command, env=None):
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode

def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare the common Agent workflow")
    parser.add_argument("command", choices=("prepare",))
    parser.parse_args(argv)
    key_setup.ensure_session_id()
    check = _run([sys.executable, "scripts/setup_env.py", "full-check"])
    if check == 0:
        print("READY")
        return 0
    if os.name == "nt":
        print("BOOTSTRAP_REQUIRED: use the inline PowerShell command from AGENT_ENTRY_PROTOCOL.md")
        return 2
    env = os.environ.copy()
    env["AGENT_INLINE_BOOTSTRAP"] = "1"
    bootstrap = _run(["bash", "deploy.sh"], env=env)
    if bootstrap != 0:
        return bootstrap
    return _run([sys.executable, "scripts/setup_env.py", "full-check"])

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
