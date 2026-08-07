#!/usr/bin/env python3
"""Formal approval-bound Shotcraft packaging stage."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import media_qc
import remotion_engine
import run_manifest as rm
import shotcraft_qc


def render_and_record(manifest, manifest_path, spec_path, out_path, *, client, quality="high"):
    rm.identity_gate(manifest, client=client)
    if not manifest.get("requires_shotcraft_packaging"):
        raise ValueError("SHOTCRAFT_PACKAGING_NOT_ENABLED")
    rm.generation_gate(manifest, "shotcraft_packaging", client=client)
    with open(spec_path, encoding="utf-8") as handle:
        spec = json.load(handle)
    report = shotcraft_qc.check(spec)
    if not report["passed"]:
        raise ValueError("SHOTCRAFT_QC_FAILED: %s" % ",".join(report["errors"]))
    rm.mark_generation_started(manifest, "shotcraft_packaging")
    remotion_engine.render_shotcraft(spec_path, out_path, quality=quality)
    qc_path = os.path.abspath(out_path) + ".qc.json"
    qc = media_qc.check(out_path, profile="formal", expected_ratio=("%s:%s" % (spec["width"], spec["height"])),
                        audio_required=False, report_path=qc_path)
    media_qc.require_pass(qc)
    rm.mark_generation_finished(manifest, "shotcraft_packaging", [out_path, qc_path])
    manifest["generation"]["shotcraft_packaging"].update({"spec": rm.file_record(spec_path), "qc": qc})
    rm.save_manifest(manifest, manifest_path)
    return manifest["generation"]["shotcraft_packaging"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render and record formal Shotcraft packaging")
    parser.add_argument("--client", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--quality", default="high", choices=["draft", "standard", "high"])
    args = parser.parse_args(argv)
    manifest = rm.load_manifest(args.manifest)
    result = render_and_record(manifest, args.manifest, args.spec, args.out, client=args.client, quality=args.quality)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
