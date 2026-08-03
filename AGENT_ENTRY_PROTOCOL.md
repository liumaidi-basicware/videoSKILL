# Agent-Independent Entry Protocol

This is the public host contract for Kilo, Codex, Hermes, and any other compatible Agent.

## Customer entry

The customer enters `/basicrouter-video` in the current Agent conversation. The host adapter must load this protocol and `AGENTS.md`; it must not duplicate the creative workflow.

## Package root

Locate the directory containing `AGENTS.md`, `scripts/setup_env.py`, `START_HERE_AGENT.py`, and `deploy.sh`. All commands below run from that directory.

## Preparation

1. Run `python3 scripts/setup_env.py full-check`.
2. If the check fails, tell the customer the environment is being prepared, then run `AGENT_INLINE_BOOTSTRAP=1 bash deploy.sh` on macOS/Linux or the equivalent inline PowerShell command on Windows.
3. Run `python3 scripts/setup_env.py full-check` again.
4. If the second check fails, stop with a customer-readable error. Do not continue, pretend success, create a venv in inline mode, or change the host Agent.

## Conversation handoff

After preparation, read and execute `AGENTS.md` and `.codex/prompts/basicrouter-video.md` as the common business workflow. The host adapter must:

- establish an Agent-independent public session ID;
- run the common key gate without putting keys in arguments, logs, or files;
- ask for `CLIENT` when it is not known;
- use the common staged confirmation and handoff protocol;
- preserve the same run when the customer changes Agent hosts;
- record host name only as diagnostic metadata, with unknown fallback.

If the host has no slash-command support, it must execute the equivalent conversation workflow from this file rather than ask the customer to use a terminal.

## Forbidden host behavior

No host may authorize, skip, downgrade, recover, or reject a run based only on the host Agent name. No host may bypass environment, key, asset, script, storyboard, reference, OCR, or final delivery gates.
