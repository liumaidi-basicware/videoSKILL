#!/usr/bin/env python3
"""Audio-mode contract and master narration identity for formal video runs."""
import argparse
import hashlib
import json
import os

AUDIO_MODES = {"talking_presenter", "voiceover", "music_only"}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(contract):
    if not isinstance(contract, dict) or contract.get("audio_mode") not in AUDIO_MODES:
        raise ValueError("AUDIO_MODE_REQUIRED")
    mode = contract["audio_mode"]
    if mode != "music_only" and not contract.get("voice_brief"):
        raise ValueError("VOICE_BRIEF_REQUIRED")
    if mode == "voiceover" and contract.get("allow_visible_speech"):
        raise ValueError("VOICEOVER_VISIBLE_SPEECH_FORBIDDEN")
    master = contract.get("master_audio")
    if master:
        path = master.get("path")
        if not path or not os.path.isfile(path):
            raise ValueError("MASTER_AUDIO_MISSING")
        if master.get("sha256") != _sha256(path):
            raise ValueError("MASTER_AUDIO_STALE")
    return contract


def create(mode, *, voice_brief=None, master_path=None):
    contract = {"schema_version": 1, "audio_mode": mode,
                "voice_brief": voice_brief or {}, "allow_visible_speech": mode == "talking_presenter"}
    if master_path:
        absolute = os.path.abspath(master_path)
        contract["master_audio"] = {"path": absolute, "sha256": _sha256(absolute)}
    return validate(contract)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create or validate a formal audio contract")
    parser.add_argument("--mode", choices=sorted(AUDIO_MODES), required=True)
    parser.add_argument("--voice-brief")
    parser.add_argument("--master-audio")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    brief = None
    if args.voice_brief:
        with open(args.voice_brief, encoding="utf-8") as handle:
            brief = json.load(handle)
    contract = create(args.mode, voice_brief=brief, master_path=args.master_audio)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(contract, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
