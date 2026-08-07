#!/usr/bin/env python3
"""Compile approved storyboard postproduction plans into a deterministic Shotcraft spec."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import schema_validate

REGISTRY = os.path.join(ROOT, "remotion_engine", "shotcraft", "registry.json")


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _ratio(width, height):
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"


def _cards(path=REGISTRY):
    data = _load(path)
    return {card["id"]: card for card in data.get("cards") or []}


def compile_spec(plan, design_tokens, *, registry_path=REGISTRY):
    """Compile only declared Shotcraft cards; no implicit decorative animation."""
    shots = plan.get("shots") or []
    if not shots:
        raise ValueError("SHOTCRAFT_PLAN_NO_SHOTS")
    width, height = plan.get("width", 1080), plan.get("height", 1920)
    fps = int(plan.get("fps", 30))
    ratio = _ratio(width, height)
    cards = _cards(registry_path)
    result = []
    for index, shot in enumerate(shots, 1):
        post = shot.get("postproduction") or {}
        if post.get("engine") != "shotcraft":
            continue
        card_id = post.get("card_id")
        card = cards.get(card_id)
        if not card:
            raise ValueError("SHOTCRAFT_CARD_UNKNOWN: %s" % card_id)
        if ratio not in card.get("ratios", []):
            raise ValueError("SHOTCRAFT_CARD_RATIO_UNSUPPORTED: %s/%s" % (card_id, ratio))
        assets = post.get("assets") or []
        if not isinstance(assets, list):
            raise ValueError("SHOTCRAFT_ASSETS_INVALID: %s" % card_id)
        duration = shot.get("duration") or shot.get("seconds") or 5
        frames = max(1, int(round(float(duration) * fps)))
        result.append({
            "id": str(shot.get("id") or "shot_%02d" % index),
            "card_id": card_id,
            "card_version": post.get("card_version", "1"),
            "durationInFrames": frames,
            "source": shot.get("expanded_image") or shot.get("video") or shot.get("image"),
            "postproduction": post,
            "assets": assets,
            "safe_zones": post.get("safe_zones") or shot.get("video_safe_zones") or [],
            "qa_frames": post.get("qa_frames") or [max(0, int(frames * x)) for x in card.get("qa_offsets", [])],
        })
    if not result:
        raise ValueError("SHOTCRAFT_NO_DECLARED_SHOTS")
    spec = {"schema_version": 1, "width": width, "height": height, "fps": fps,
            "theme": design_tokens, "shots": result}
    schema_validate.enforce(spec, "shotcraft-spec", context="shotcraft_compile.compile_spec")
    return spec


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile approved Shotcraft postproduction specs")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--style-tokens", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--registry", default=REGISTRY)
    args = parser.parse_args(argv)
    spec = compile_spec(_load(args.plan), _load(args.style_tokens), registry_path=args.registry)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out), "shots": len(spec["shots"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
