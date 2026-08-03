#!/usr/bin/env python3
"""Best-effort host-agent detection for diagnostics, never authorization."""
import os
import re


RUNTIME_ENV = "BASICROUTER_AGENT_RUNTIME"

# Host variables are intentionally signals rather than requirements. Agents may
# add or rename variables, so callers must always support the unknown fallback.
_HOST_SIGNALS = (
    ("kilo", ("KILO_SESSION_ID", "KILO_AGENT_ID", "KILO_CONFIG_DIR")),
    ("codex", ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME")),
    ("hermes", ("HERMES_SESSION_ID", "HERMES_AGENT_ID", "HERMES_HOME")),
)


def _normalize(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-._")
    return value or "unknown"


def detect_agent_runtime(explicit=None, environ=None):
    """Return agent runtime diagnostics without assuming a specific host.

    Explicit/canonical values accept future or private agent names. Host signal
    detection only provides a convenience fallback and is never a security gate.
    """
    env = os.environ if environ is None else environ
    if explicit and str(explicit).strip():
        return {"name": _normalize(explicit), "source": "explicit"}
    configured = env.get(RUNTIME_ENV)
    if configured and configured.strip():
        return {"name": _normalize(configured), "source": RUNTIME_ENV}
    for name, variables in _HOST_SIGNALS:
        matched = next((variable for variable in variables if env.get(variable)), None)
        if matched:
            return {"name": name, "source": matched}
    return {"name": "unknown", "source": "unknown"}
