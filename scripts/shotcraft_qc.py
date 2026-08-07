#!/usr/bin/env python3
"""Static fail-closed validation for a compiled Shotcraft spec before rendering."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotcraft_compile
import schema_validate


def check(spec, *, registry_path=shotcraft_compile.REGISTRY):
    schema_validate.enforce(spec, "shotcraft-spec", context="shotcraft_qc.check")
    cards = shotcraft_compile._cards(registry_path)
    errors = []
    for shot in spec.get("shots") or []:
        card = cards.get(shot.get("card_id"))
        if not card:
            errors.append("UNKNOWN_CARD:%s" % shot.get("card_id"))
        if not shot.get("qa_frames"):
            errors.append("QA_FRAMES_REQUIRED:%s" % shot.get("id"))
        if any(not isinstance(value, int) or value < 0 or value >= shot["durationInFrames"]
               for value in shot.get("qa_frames") or []):
            errors.append("QA_FRAME_OUT_OF_RANGE:%s" % shot.get("id"))
        post = shot.get("postproduction") or {}
        if post.get("text") or post.get("subtitle"):
            errors.append("TEXT_MUST_USE_HYPERFRAMES:%s" % shot.get("id"))
    return {"schema_version": 1, "passed": not errors, "errors": errors,
            "shot_count": len(spec.get("shots") or [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a Shotcraft spec")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    with open(args.spec, encoding="utf-8") as handle:
        report = check(json.load(handle))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
