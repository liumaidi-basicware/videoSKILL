#!/usr/bin/env python3
"""Generate an approval-bound design token contract for deterministic packaging."""
import argparse
import json
import os


def build_tokens(brand=None, *, direction="custom", energy="medium"):
    brand = brand or {}
    palette = brand.get("colors") or brand.get("palette") or {}
    return {
        "schema_version": 1,
        "approved_direction": direction,
        "brand": {
            "primary": palette.get("primary", "#0C66E4"),
            "accent": palette.get("accent", "#36B37E"),
            "background": palette.get("background", "#0B1020"),
            "text": palette.get("text", "#FFFFFF"),
        },
        "typography": {"title_font": brand.get("font", "sans-serif"), "body_font": brand.get("font", "sans-serif")},
        "motion": {"energy": energy, "hold_min_frames": 15, "transition_style": "shared_element"},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build styleframe design tokens")
    parser.add_argument("--brand")
    parser.add_argument("--direction", default="custom")
    parser.add_argument("--energy", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    brand = {}
    if args.brand:
        with open(args.brand, encoding="utf-8") as handle:
            brand = json.load(handle)
    tokens = build_tokens(brand, direction=args.direction, energy=args.energy)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(tokens, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
