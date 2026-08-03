#!/usr/bin/env python3
"""AI disclosure compliance: add AI-generated content badge to final videos.

Meta/TikTok/YouTube require or recommend disclosing AI-generated content.
This module renders a 1s transparent corner badge using HyperFrames and
overlays it onto the final video via ffmpeg — no model calls, pure local.

The disclosure is ON by default (compliance-first). The client can opt out
per-run via manifest flag or CLI --no-disclosure.

Manifest records: {"disclosure": {"applied": true, "style": "corner-badge",
                                  "duration_ms": 1000, "lang": "zh-CN", "text": "AI 生成内容"}}

CLI:
  python3 ai_disclosure.py apply --video output/final.mp4 --out output/final_disclosed.mp4
  python3 ai_disclosure.py apply --video output/final.mp4 --out output/final_disclosed.mp4 --lang en
  python3 ai_disclosure.py apply --video output/final.mp4 --out output/final_disclosed.mp4 --no-disclosure
"""
import os
import sys
import json
import argparse
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import proc_utils

# ── Disclosure text by language ─────────────────────────────────────────
DISCLOSURE_TEXT = {
    "zh-CN": "AI 生成内容",
    "zh-TW": "AI 生成內容",
    "en": "AI-generated content",
    "ja": "AI生成コンテンツ",
    "ko": "AI 생성 콘텐츠",
}

# Default badge scene spec for HyperFrames
def _badge_scene_spec(text, resolution, duration_sec=1.0):
    """Build a HyperFrames scene JSON for the disclosure badge."""
    w, h = resolution
    return {
        "resolution": [w, h],
        "fps": 30,
        "duration": duration_sec,
        "background": {"type": "transparent"},
        "brand": {"primary": "#666666"},
        "scenes": [
            {
                "text": text,
                "start": 0,
                "end": duration_sec,
                "preset": "fade",
                "size": min(32, max(20, int(w * 0.022))),
                "pos": "lower",
                "color": "#999999",
            }
        ],
    }


def apply_disclosure(video_path, out_path, *, lang="zh-CN", enabled=True, resolution=None):
    """Apply AI disclosure badge to a final video.

    Args:
        video_path: path to the final video
        out_path: output path for the disclosed video
        lang: language code for the disclosure text
        enabled: if False, just copy the file (no-op)
        resolution: (w, h) tuple; if None, auto-detect via ffprobe

    Returns:
        {"applied": bool, "style": str, "duration_ms": int,
         "lang": str, "text": str, "out_path": str}
    """
    text = DISCLOSURE_TEXT.get(lang, DISCLOSURE_TEXT["en"])

    if not enabled:
        # No-op: just copy
        import shutil
        shutil.copy2(video_path, out_path)
        return {"applied": False, "style": "none", "duration_ms": 0,
                "lang": lang, "text": "", "out_path": out_path}

    # Detect resolution if not provided
    if resolution is None:
        resolution = _detect_resolution(video_path)

    w, h = resolution
    duration_sec = 1.0

    # Build the badge as a 1s transparent overlay using ffmpeg drawtext
    # (Simple approach: no need for full HyperFrames for a text badge)
    # For production quality, use hf_engine to render the badge with GSAP.
    # Here we use ffmpeg's drawtext for a zero-dependency path.

    import tempfile

    # Get font path for CJK
    font_path = _find_cjk_font()

    tmp_fd, tmp_overlay = tempfile.mkstemp(suffix=".mp4", prefix="ai_disclosure_")
    os.close(tmp_fd)

    try:
        # Generate 1s transparent video with drawtext
        font_opt = ":fontfile='%s'" % font_path if font_path else ""
        drawtext = (
            "drawtext=%s:text='%s'"
            ":fontcolor=0x999999:fontsize=%d"
            ":x=w-tw-30:y=h-th-30:alpha='if(lt(t,0.2),t/0.2,if(gt(t,0.8),(1-t)/0.2,1))'"
            % (font_opt, text, min(32, max(20, int(w * 0.022)))))

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "color=c=black@0.0:s=%dx%d:d=%.1f:r=30" % (w, h, duration_sec),
            "-vf", drawtext,
            "-c:v", "libx264", "-pix_fmt", "yuva420p",
            "-t", "%.1f" % duration_sec,
            tmp_overlay,
        ]
        proc_utils.run_cmd(cmd, timeout=60)

        # Overlay the badge onto the last 1s of the video
        # Get video duration
        vid_duration = _detect_duration(video_path)

        cmd2 = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", tmp_overlay,
            "-filter_complex",
            "[0:v][1:v]overlay=eof_action=endshortest:x=W-w-30:y=H-h-30:enable='gte(t,%f)'" % max(0, vid_duration - duration_sec),
            "-c:v", "libx264",
            "-c:a", "copy",
            "-shortest",
            out_path,
        ]
        proc_utils.run_cmd(cmd2, timeout=120)

    finally:
        if os.path.exists(tmp_overlay):
            os.remove(tmp_overlay)

    return {
        "applied": True,
        "style": "corner-badge",
        "duration_ms": int(duration_sec * 1000),
        "lang": lang,
        "text": text,
        "out_path": out_path,
    }


def _detect_resolution(video_path):
    """Detect video resolution via ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path]
        result = proc_utils.run_cmd(cmd, timeout=30)
        parts = result.strip().split(",")
        return (int(parts[0]), int(parts[1]))
    except Exception:
        return (1080, 1920)  # default vertical


def _detect_duration(video_path):
    """Detect video duration via ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", video_path]
        result = proc_utils.run_cmd(cmd, timeout=30)
        return float(result.strip())
    except Exception:
        return 10.0


def _find_cjk_font():
    """Find a system CJK font for ffmpeg drawtext."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def main():
    parser = argparse.ArgumentParser(description="AI disclosure compliance badge")
    sub = parser.add_subparsers(dest="cmd")

    ap = sub.add_parser("apply", help="Apply AI disclosure badge to final video")
    ap.add_argument("--video", required=True, help="Path to final video")
    ap.add_argument("--out", required=True, help="Output path")
    ap.add_argument("--lang", default="zh-CN", choices=list(DISCLOSURE_TEXT.keys()),
                    help="Disclosure text language")
    ap.add_argument("--no-disclosure", action="store_true",
                    help="Skip disclosure (copy file only)")
    ap.add_argument("--resolution", type=str, default=None,
                    help="Override resolution as WxH (e.g. 1080x1920)")

    args = parser.parse_args()

    if args.cmd == "apply":
        resolution = None
        if args.resolution:
            parts = args.resolution.lower().split("x")
            resolution = (int(parts[0]), int(parts[1]))

        result = apply_disclosure(
            args.video, args.out,
            lang=args.lang,
            enabled=not args.no_disclosure,
            resolution=resolution)

        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
