# Hermes / Other Agent Thin Entry

When the customer enters `/basicrouter-video`, read `AGENT_ENTRY_PROTOCOL.md`, then `AGENTS.md` and `.codex/prompts/basicrouter-video.md`. Do not copy the workflow into this adapter.

Use `python3 scripts/agent_entry.py prepare` from the package root for the common environment check and inline bootstrap. Continue the common workflow in the conversation after it reports `READY`.

If the host has no slash-command support, treat this file as the equivalent entry. The customer must not be sent to a terminal. Host identity is diagnostic only and must never change authorization, approvals, recovery, model selection, or asset handoff.
