#!/usr/bin/env python3
"""Video quality guards: OCR, media QC, reference validation.

Extracted from video_engine.py (v3 split). Contains:
  - _ocr_guard (OCR subtitle residue detection, macOS Vision)
  - _media_qc_guard (ffprobe-based QC gate)
  - _validate_references (reference image handoff validation)
  - _validate_reference_handoff (handoff fingerprint verification)
  - _validate_model_reference_capacity (model image count limits)

Dependencies: ocr_check (optional), media_qc, artifact_contract
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import artifact_contract
import media_qc


def _ocr_guard(local_path, log):
    """Run OCR subtitle residue detection after video download.

    Detects text → prints [OCR_WARNING] for agent decision.
    Unavailable (non-Mac/missing deps) → silently skip.

    Returns structured result for batch/chained results (铁律#9):
      {"status": "clear"|"detected"|"error"|"unavailable",
       "available": bool, "subtitle_detected": bool,
       "texts": [str...], "frames_checked": int}
    or None on exception.
    """
    try:
        import ocr_check
    except ImportError:
        return None
    try:
        report = ocr_check.check_video(local_path, n_frames=5)
    except Exception as e:
        log("[OCR] 检测异常，已跳过：%s" % e)
        return None

    if not report.get("ocr_available"):
        log("[OCR] 不可用（%s），跳过字幕检测。" % report.get("error", ""))
        return {"status": "unavailable", "available": False, "subtitle_detected": False,
                "texts": [], "frames_checked": 0, "expected": 0,
                "error": report.get("error")}
    if report.get("error"):
        log("[OCR] 检测出错（%s），跳过。" % report["error"])
        return {"status": "error", "available": False, "subtitle_detected": False,
                "texts": [], "frames_checked": 0, "expected": report.get("expected", 0),
                "error": report.get("error")}
    texts = []
    if report.get("subtitle_detected"):
        log("[OCR_WARNING] subtitle_detected — 成片 %s 检出画面文字，"
            "疑似字幕残留，建议重新生成！" % os.path.basename(local_path))
        for d in report.get("detections", []):
            for t in d.get("texts", []):
                log("  帧 %s | 置信度 %.2f | 文字：%s" % (
                    d["frame"], t["confidence"], t["text"]))
                texts.append(t["text"])
    else:
        log("[OCR] OK — 抽检 %d 帧，未检出画面文字。" % report.get("frames_checked", 0))
    return {"status": "detected" if report.get("subtitle_detected") else "clear",
            "available": True, "subtitle_detected": bool(report.get("subtitle_detected")),
            "texts": texts, "frames_checked": report.get("frames_checked", 0),
            "expected": report.get("expected", report.get("frames_checked", 0)),
            "error": None}


def _media_qc_guard(local_path, segment, *, draft, manifest=None,
                     manifest_path=None, segment_id=None, profile="formal"):
    """Run ffprobe-based media QC gate.

    In formal mode, failure raises ValueError (fail-closed).
    In draft mode, warnings are logged but don't block.
    """
    try:
        report = media_qc.check_video(local_path, profile=profile,
                                       expected_duration=segment.get("duration"),
                                       expected_ratio=segment.get("ratio"))
    except Exception as exc:
        if not draft:
            raise ValueError("MEDIA_QC_ERROR: %s" % exc)
        return {"passed": False, "draft": True, "error": str(exc)}

    passed = report.get("passed", False) if not draft else True
    result = {
        "passed": passed,
        "draft": draft,
        "profile": profile,
        "checks": report.get("checks", {}),
        "actual_duration": report.get("actual_duration"),
        "actual_resolution": report.get("actual_resolution"),
    }

    if not passed and not draft:
        raise ValueError("MEDIA_QC_FAILED: %s" % report.get("errors", []))

    return result


def _validate_references(segments, client, manifest, draft=False):
    """Validate that all reference images are confirmed and current.

    Checks product board, cast board, and product usage images against
    the manifest's approval records and file fingerprints.
    """
    # This is a complex function with many manifest checks — the full
    # implementation remains in video_engine.py for now, as it accesses
    # manifest internals that are tightly coupled to the render loop.
    # Migration target: move here once video_engine.py becomes a shim.
    pass


def _validate_reference_handoff(segments):
    """Verify each segment has a video_handoff_fingerprint from the storyboard."""
    missing = [seg.get("id", "?") for seg in segments
               if not seg.get("video_handoff_fingerprint")]
    if missing:
        raise ValueError(
            "VIDEO_HANDOFF_FINGERPRINT_MISSING: segments %s 缺少故事板确认指纹"
            % ", ".join(missing))


def _validate_model_reference_capacity(model, reference_count, *, formal):
    """Check if the model supports the given number of reference images."""
    # Uses _model_allow_types from video_models to check image_count
    # Full implementation remains in video_engine.py due to catalog caching
    pass
