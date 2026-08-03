#!/usr/bin/env python3
"""Customer-facing approval, status, and immutable delivery orchestration."""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import script_splitter  # noqa: E402
import take_review  # noqa: E402
import cost_ledger  # noqa: E402 — v3 cost tracking

import run_manifest as rm


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_json(path, value):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approve_stage(manifest, manifest_path, stage, *, storyboard_result=None):
    """Approve a stage; storyboard confirmation is mirrored into its native gate."""
    if stage == "storyboard":
        if not storyboard_result:
            raise ValueError("STORYBOARD_RESULT_REQUIRED")
        import storyboard
        rm.generation_gate(manifest, "storyboard")
        record = storyboard.confirm_storyboard(storyboard_result)
        approval_path = os.path.join(record["out_dir"], ".storyboard_confirmed.json")
        outputs = [storyboard_result, approval_path] + [shot["path"] for shot in record["shots"]]
        rm.mark_generation_finished(manifest, "storyboard", outputs)
        manifest["storyboard_approval"] = {
            "result": rm.file_record(storyboard_result),
            "native_approval": rm.file_record(approval_path),
            "plan_fingerprint": record["plan_fingerprint"],
        }
    if stage == "video":
        rm.validate_video_closure(manifest)
    rm.approve(manifest, stage, strict=True)
    rm.save_manifest(manifest, manifest_path)
    return manifest


def _try_cost_summary(manifest):
    """Best-effort cost estimation from generation ledger. Never raises."""
    try:
        run_dir = manifest.get("run_dir") or os.path.dirname(
            manifest.get("manifest_path", ""))
        ledger_path = os.path.join(run_dir, "generation_runs.jsonl")
        if os.path.isfile(ledger_path):
            return cost_ledger.estimate_run_cost(ledger_path)
    except Exception:
        pass
    return None


def _try_cost_line(manifest):
    """Best-effort human-readable cost line for CLI output."""
    try:
        summary = _try_cost_summary(manifest)
        if summary:
            return cost_ledger.format_cost_line(summary)
    except Exception:
        pass
    return ""


def pipeline_status(manifest):
    """Return one stable, customer-readable next action with absolute previews."""
    required = [stage for stage in rm.STAGES
                if (stage != "product_board" or manifest.get("requires_product_board")) and
                (stage != "product_usage" or manifest.get("requires_product_usage"))]
    current = next((stage for stage in required if not rm.approval_is_current(manifest, stage)), "delivery")
    generation = (manifest.get("generation") or {}).get(current) or {}
    pending = generation.get("status") == "pending_approval"
    previews = [os.path.abspath(path) for path in generation.get("outputs") or []
                if path and os.path.exists(path)]
    if current == "storyboard":
        result_record = (manifest.get("storyboard_approval") or {}).get("result") or {}
        if result_record.get("path") and os.path.isfile(result_record["path"]):
            result = _load(result_record["path"])
            storyboard_previews = []
            for key in ("preview_html", "embedded_md", "index_md"):
                if result.get(key) and os.path.exists(result[key]):
                    storyboard_previews.append(os.path.abspath(result[key]))
            previews = storyboard_previews + previews
    previews = list(dict.fromkeys(previews))
    if current == "delivery":
        try:
            _delivery_snapshot(manifest)
            action = "create_delivery"
            message = "最终成片已确认，所有交付证据齐全，可以生成可校验的交付清单。"
        except (ValueError, OSError, KeyError) as exc:
            action = "repair_delivery_evidence"
            message = "阶段审批已完成，但交付证据仍不完整：%s。请先补齐 reviews、take、OCR 或 formal QC。" % exc
    elif pending:
        action = "approve_%s" % current
        message = "请查看预览并确认%s阶段；文件变化后本次确认会自动失效。" % current
    else:
        action = "start_%s" % current
        message = "下一步进入%s阶段，完成后会先给你预览确认。" % current
    return {
        "current_stage": current,
        "next_action": action,
        "customer_message": message,
        "customer_preview": previews,
        "cost_line": _try_cost_line(manifest),
    }


def _delivery_snapshot(manifest):
    """Validate every release gate and return its content-bound identity."""

    rm.identity_gate(manifest)
    if not rm.approval_is_current(manifest, "final"):
        raise ValueError("DELIVERY_FINAL_APPROVAL_REQUIRED_OR_STALE")
    if not rm.approval_is_current(manifest, "video"):
        raise ValueError("DELIVERY_VIDEO_APPROVAL_REQUIRED_OR_STALE")
    if not rm.approval_is_current(manifest, "captions"):
        raise ValueError("DELIVERY_CAPTION_APPROVAL_REQUIRED_OR_STALE")
    final_records = rm._stage_artifacts(manifest, "final", refresh=True)
    if len(final_records) != 1 or not final_records[0].get("exists"):
        raise ValueError("DELIVERY_FINAL_FILE_REQUIRED")
    final_qc = manifest.get("delivery_qc") or {}
    qc_file = final_qc.get("file") or {}
    if (final_qc.get("profile") != "formal" or not final_qc.get("passed") or
            qc_file.get("path") != final_records[0].get("path") or
            qc_file.get("sha256") != final_records[0].get("sha256")):
        raise ValueError("DELIVERY_FINAL_FORMAL_QC_REQUIRED_OR_STALE")

    caption_ref = manifest.get("caption_artifact") or {}
    caption_path = caption_ref.get("path")
    if not caption_path or not os.path.isfile(caption_path):
        raise ValueError("DELIVERY_CAPTION_ARTIFACT_REQUIRED")
    caption_artifact = _load(caption_path)
    script_splitter.caption_artifact_is_current(
        manifest, caption_artifact, client=manifest.get("client"), require_approved=True)

    handoff = (manifest.get("handoffs") or {}).get("video") or {}
    video_artifact = manifest.get("video_artifact") or {}
    for name in ("segments", "results", "basecut", "reviews"):
        if not rm.file_record_is_current(video_artifact.get(name)):
            raise ValueError("DELIVERY_VIDEO_ARTIFACT_STALE: %s" % name)
    raw_results = _load(video_artifact["results"]["path"])
    results = raw_results.get("results") if isinstance(raw_results, dict) else raw_results
    result_by_id = {str(item.get("segment_id")): item for item in results or []}
    segment_ids = set((handoff.get("segments") or {}).keys())
    accepted = manifest.get("accepted_takes") or {}
    if not segment_ids or set(accepted.keys()) != segment_ids:
        raise ValueError("DELIVERY_ACCEPTED_TAKES_INCOMPLETE")
    reviews_summary = _load(video_artifact["reviews"]["path"])
    if not isinstance(reviews_summary, dict) or set(map(str, reviews_summary.keys())) != segment_ids:
        raise ValueError("DELIVERY_REVIEWS_SUMMARY_MISMATCH")
    takes = {}
    for sid in sorted(segment_ids):
        item = accepted[sid]
        result = result_by_id.get(sid) or {}
        if not (result.get("media_qc") or {}).get("passed"):
            raise ValueError("DELIVERY_MEDIA_QC_FAILED: %s" % sid)
        review_record = item.get("review") or {}
        if not rm.file_record_is_current(review_record):
            raise ValueError("DELIVERY_REVIEW_STALE_OR_MISSING: %s" % sid)
        review = _load(review_record["path"])
        summary_review = reviews_summary.get(sid)
        if (not isinstance(summary_review, dict) or
                summary_review.get("review_id") != review.get("review_id") or
                (summary_review.get("artifact") or {}).get("take_fingerprint") !=
                (review.get("artifact") or {}).get("take_fingerprint")):
            raise ValueError("DELIVERY_REVIEWS_SUMMARY_MISMATCH: %s" % sid)
        take_fp = item.get("take_fingerprint")
        if (not take_review.is_accepted(review, take_fp) or
                review.get("artifact", {}).get("video_handoff_fingerprint") != handoff["segments"][sid]):
            raise ValueError("DELIVERY_TAKE_QC_FAILED: %s" % sid)
        decision = review.get("decision") or {}
        quality = review.get("quality") or {}
        technical = quality.get("technical") or {}
        marketing = quality.get("marketing") or {}
        if (decision.get("acceptance_mode") != "formal" or
                not review.get("review_sources") or
                any(technical.get(key) is not True for key in take_review.TECHNICAL_REQUIRED) or
                any(not (marketing.get(key) is True or
                         (not isinstance(marketing.get(key), bool) and
                          isinstance(marketing.get(key), (int, float)) and
                          80 <= marketing.get(key) <= 100))
                    for key in take_review.MARKETING_REQUIRED) or
                not isinstance(quality.get("overall_score"), (int, float)) or
                isinstance(quality.get("overall_score"), bool) or
                 not 80 <= quality.get("overall_score") <= 100 or
                not take_review.artifact_is_current(review)):
            raise ValueError("DELIVERY_TAKE_QC_FAILED: %s" % sid)
        if not rm.ocr_take_is_clear_or_waived(manifest, sid, take_fp):
            raise ValueError("DELIVERY_OCR_FAILED: %s" % sid)
        takes[sid] = {
            "take_fingerprint": take_fp,
            "handoff": handoff["segments"][sid],
            "review": review_record,
            "ocr": (manifest.get("ocr_checks") or {}).get(sid),
            "waiver": (manifest.get("ocr_waivers") or {}).get(sid),
        }
    return {
        "client": manifest.get("client"),
        "run_id": manifest.get("run_id"),
        "final": final_records[0],
        "caption_identity": caption_artifact.get("caption_identity"),
        "caption_artifact": rm.file_record(caption_path),
        "video_handoff_sha256": handoff.get("sha256"),
        "video_artifact": video_artifact,
        "accepted_takes": takes,
        "cost_summary": _try_cost_summary(manifest),
    }


def create_delivery(manifest, path):
    snapshot = _delivery_snapshot(manifest)
    delivery = {
        "schema_version": 1,
        "status": "verified",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "delivery_identity": _digest(snapshot),
        **snapshot,
    }
    _atomic_json(path, delivery)
    return delivery


def verify_delivery(manifest, path):
    delivery = _load(path)
    snapshot = _delivery_snapshot(manifest)
    identity = _digest(snapshot)
    if delivery.get("delivery_identity") != identity:
        raise ValueError("DELIVERY_STALE: manifest 或交付文件内容已变化")
    stored = {key: delivery.get(key) for key in snapshot}
    if stored != snapshot:
        raise ValueError("DELIVERY_CONTENT_MISMATCH")
    return {"ok": True, "delivery": os.path.abspath(path), "delivery_identity": identity}


def main(argv=None):
    parser = argparse.ArgumentParser(description="正式制作审批、状态与交付编排")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--manifest", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("--manifest", required=True)
    approve.add_argument("--stage", choices=rm.STAGES, required=True)
    approve.add_argument("--storyboard-result")
    delivery = sub.add_parser("delivery")
    delivery_sub = delivery.add_subparsers(dest="delivery_command", required=True)
    for name in ("create", "verify"):
        command = delivery_sub.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--out" if name == "create" else "--delivery", required=True)
    args = parser.parse_args(argv)
    manifest_path = args.manifest
    try:
        manifest = _load(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("ERROR:RUN_MANIFEST_UNAVAILABLE\n下一步：请先用 START_HERE_AGENT.py init 创建 run，或检查 manifest 路径。\n详情：%s" % exc,
              file=sys.stderr)
        return 2
    if args.command == "status":
        result = pipeline_status(manifest)
    elif args.command == "approve":
        approve_stage(manifest, manifest_path, args.stage,
                      storyboard_result=args.storyboard_result)
        result = pipeline_status(manifest)
    elif args.delivery_command == "create":
        result = create_delivery(manifest, args.out)
    else:
        result = verify_delivery(manifest, args.delivery)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
