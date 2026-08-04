#!/usr/bin/env python3
"""Agent-agnostic, session-scoped BasicRouter key onboarding.

The host agent must provide a stable ``BASICROUTER_SESSION_ID`` for the whole
conversation.  The key is shared by subprocesses through a 600-permission
cache file tied to that ID, but is never reused by a new session by default.

CLI:
  python3 key_setup.py init                  -> print a new session ID
  python3 key_setup.py check                 -> prints STORED / MISSING
  python3 key_setup.py gate                  -> STORED(exit0) or a client-facing
                                                 reminder + BLOCKED(exit1). Every
                                                 guided skill calls this FIRST; on
                                                 BLOCKED the skill must stop and not
                                                 start any 引导. Key is entered once
                                                 per session.
  python3 key_setup.py save <sk-...>         -> validate + save for this session
  python3 key_setup.py clear                 -> remove this session's key
  python3 key_setup.py get                   -> prints key or MISSING
"""
import os
import sys
import stat
import hashlib
import secrets
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import br_client          # noqa: E402 — hoisted from inline import (v3 cleanup)

import agent_runtime
from project_utils import FileLock

SESSION_ENV = "BASICROUTER_SESSION_ID"
API_KEY_ENV = "BASICROUTER_API_KEY"
SESSION_DIR = os.path.join(os.path.expanduser("~/.cache"), "basicrouter", "sessions")
# Cross-process handoff is opt-in. A machine-global default would allow a new
# conversation to accidentally reuse the previous conversation's session ID.
SESSION_STATE_FILE = os.environ.get("BASICROUTER_SESSION_STATE_FILE")


def ensure_session_id(host_session_id=None):
    """Ensure this process has a public session ID for child processes.

    Host adapters should set BASICROUTER_SESSION_ID explicitly. The fallback is
    intentionally derived only from an opaque host signal, or generated once
    for this process; it never reads another session's key.
    """
    current = session_id()
    if current:
        return current
    # Agent command adapters may launch each command in a fresh shell. Keep
    # only the opaque public ID here, never the API key, so those subprocesses
    # can select the same session cache without a hard-coded command prefix.
    if SESSION_STATE_FILE:
        try:
            with open(SESSION_STATE_FILE, encoding="utf-8") as handle:
                current = session_id(handle.read())
                if current:
                    os.environ[SESSION_ENV] = current
                    return current
        except OSError:
            pass
    host = host_session_id or os.environ.get("BASICROUTER_HOST_SESSION_ID")
    if not host:
        runtime = agent_runtime.detect_agent_runtime()
        for variable in ("KILO_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                         "HERMES_SESSION_ID"):
            if os.environ.get(variable):
                host = os.environ[variable]
                break
        if not host:
            # Use project directory as stable host signal so that every
            # command in the same project resolves to the same session ID.
            # Previously used secrets.token_urlsafe(24) which generated a
            # new random ID per shell, breaking save/load key pairing.
            project_dir = os.path.dirname(os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..")))
            host = runtime["name"] + ":" + project_dir
    digest = hashlib.sha256(str(host).encode("utf-8")).hexdigest()[:32]
    value = "br-%s-%s" % (agent_runtime.detect_agent_runtime()["name"], digest)
    os.environ[SESSION_ENV] = value
    if SESSION_STATE_FILE:
        directory = os.path.dirname(SESSION_STATE_FILE) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".session-", dir=directory)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, SESSION_STATE_FILE)
            os.chmod(SESSION_STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    return value


def session_id(explicit=None):
    value = explicit or os.environ.get(SESSION_ENV)
    return value.strip() if value and value.strip() else None


def session_key_path(explicit_session_id=None):
    sid = session_id(explicit_session_id)
    if not sid:
        return None
    # Hash the opaque host identifier so it cannot create path components.
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()
    return os.path.join(SESSION_DIR, digest, "key")


def load_key(explicit_session_id=None):
    """Return the current session key, or an explicitly allowed env key."""
    path = session_key_path(explicit_session_id)
    if path:
        with FileLock(path + ".lock"):
            if os.path.exists(path):
                if os.path.islink(path):
                    raise ValueError("SESSION_KEY_SYMLINK_BLOCKED")
                with open(path) as f:
                    k = f.read().strip()
                    return k or None
    # Global environment keys are intentionally opt-in for CI/development.
    if os.environ.get("BR_ALLOW_GLOBAL_KEY") == "1":
        env = os.environ.get(API_KEY_ENV)
        return env.strip() if env else None
    return None


def save_key(key, explicit_session_id=None):
    """Persist a key for the current session with 600 perms."""
    key = key.strip()
    path = session_key_path(explicit_session_id)
    if not path:
        raise ValueError("BASICROUTER_SESSION_ID is required")
    directory = os.path.dirname(path)
    with FileLock(path + ".lock"):
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.path.islink(directory) or os.path.islink(path):
            raise ValueError("SESSION_KEY_SYMLINK_BLOCKED")
        os.chmod(directory, stat.S_IRWXU)
        fd, temporary = tempfile.mkstemp(prefix=".key-", dir=directory)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w") as f:
                f.write(key)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    return path


def clear_key(explicit_session_id=None):
    path = session_key_path(explicit_session_id)
    if path:
        with FileLock(path + ".lock"):
            if os.path.exists(path):
                os.remove(path)
                try:
                    os.rmdir(os.path.dirname(path))
                except OSError:
                    pass
    return path


def _validate(key):
    # import here so `check`/`get` work even without network
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return br_client.validate_key(key)


def main(argv):
    if argv and argv[0] in ("--help", "-h"):
        print("用法：key_setup.py init|gate|save --stdin|get|clear --session-id <公共会话ID>")
        return 0
    if not argv:
        print("usage: key_setup.py {init|check|gate|save [--stdin]|get|clear} "
              "[--session-id ID] [--host-session-id ID]")
        return 2
    cmd = argv[0]
    explicit_sid = None
    host_sid = None
    if "--session-id" in argv:
        index = argv.index("--session-id")
        if index + 1 >= len(argv):
            print("ERROR: no session ID provided")
            return 2
        explicit_sid = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    if "--host-session-id" in argv:
        index = argv.index("--host-session-id")
        if index + 1 >= len(argv):
            print("ERROR: no host session ID provided")
            return 2
        host_sid = argv[index + 1].strip()
        argv = argv[:index] + argv[index + 2:]
    if cmd == "init":
        runtime = agent_runtime.detect_agent_runtime()
        if host_sid:
            digest = hashlib.sha256(host_sid.encode("utf-8")).hexdigest()[:32]
            print("br-%s-%s" % (runtime["name"], digest))
        else:
            print("br-unknown-" + secrets.token_urlsafe(24))
        return 0
    ensure_session_id()
    if cmd == "check":
        print("STORED" if load_key(explicit_sid) else "MISSING")
        return 0
    if cmd == "gate":
        if not session_id(explicit_sid):
            print("SESSION_REQUIRED\n请由当前 Agent 为本次会话设置 BASICROUTER_SESSION_ID。")
            return 1
        if load_key(explicit_sid):
            print("STORED")
            return 0
        print(
            "BLOCKED\n"
            "———\n"
            "使用数字人视频创作功能前，需要先填写你的 BasicRouter 密钥（本次会话填写一次即可）。\n"
            "请把你的密钥（以 sk- 开头）直接粘贴到对话框发给我，我校验后即可开始创作。\n"
            "还没有密钥？可到 BasicRouter 账户获取。\n"
            "（在密钥填好之前，暂时无法开始引导创作。）"
        )
        return 1
    if cmd == "get":
        k = load_key(explicit_sid)
        print((k[:4] + "..." + k[-4:]) if k and len(k) > 8 else ("STORED" if k else "MISSING"))
        return 0 if k else 1
    if cmd == "clear":
        clear_key(explicit_sid)
        print("CLEARED")
        return 0
    if cmd == "save":
        if not session_id(explicit_sid):
            print("ERROR: BASICROUTER_SESSION_ID is required")
            return 1
        if "--stdin" in argv or len(argv) < 2:
            key = sys.stdin.readline().strip()
        else:
            key = argv[1].strip()
            print("WARNING: 命令行参数可能进入终端历史；请改用 save --stdin。", file=sys.stderr)
        if not key:
            print("ERROR: no key provided")
            return 2
        if not key.startswith("sk-"):
            print("ERROR: 密钥格式不正确，请粘贴以 sk- 开头的 BasicRouter 密钥后重试。")
            return 2
        ok, msg = _validate(key)
        if not ok:
            print("INVALID: %s" % msg)
            return 1
        try:
            path = save_key(key, explicit_sid)
        except ValueError as exc:
            print("ERROR: %s" % exc)
            return 1
        print("SAVED: %s (validated ok)" % path)
        return 0
    print("unknown command: %s" % cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
