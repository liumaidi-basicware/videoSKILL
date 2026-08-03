#!/usr/bin/env python3
"""Kinetic typography engine: render commercial-grade animated text/subtitles
and burn them into a video — all offline via the bundled ffmpeg (libass).

Input = a JSON scenes spec (usually written by the guided skill from the
confirmed script). Output = a video with animated text baked in.

Scenes spec (JSON):
{
  "resolution": [1080,1920], "fps": 30,
  "base": {"type": "video", "path": "output/clip.mp4"}         # or
  "base": {"type": "color", "color": "0x0B1220", "duration": 6},
  "brand": {"primary": "#E60012", "font": "Arial"},            # optional
  "scenes": [
    {"text": "65W 快充", "start": 0.0, "end": 2.5, "preset": "fade_up", "size": 96, "pos": "center"},
    {"text": "僅 320g 輕巧", "start": 2.5, "end": 5.0, "preset": "slide_left", "size": 72, "pos": "lower"},
    {"text": "隨時滿電", "start": 5.0, "end": 7.0, "preset": "typewriter", "size": 110, "pos": "center"}
  ]
}

Presets (commercial motion): fade_up, slide_left, slide_right, pop, typewriter, fade.

CLI:
  python3 text_anim.py render --spec scenes.json --out output/promo.mp4
  python3 text_anim.py preview-ass --spec scenes.json   # print the generated .ass
"""
import os
import sys
import json
import shutil
import argparse
import subprocess
import tempfile
import locale

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit("ffmpeg not found. Run: pip3 install imageio-ffmpeg")


def _font_family(spec):
    configured = (spec.get("brand") or {}).get("font")
    if configured and configured.lower() not in {"arial", "sans", "sans-serif"}:
        return configured
    try:
        import cjk_font
        return cjk_font.family()
    except Exception:
        return "PingFang SC" if sys.platform == "darwin" else "Noto Sans CJK SC"


def _hex_to_ass(color):
    """#RRGGBB -> ASS &HBBGGRR (ASS uses BGR)."""
    c = color.lstrip("#")
    if len(c) != 6:
        return "&H00FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return "&H00%s%s%s" % (b.upper(), g.upper(), r.upper())


def _t(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return "%d:%02d:%05.2f" % (h, m, s)


# position -> ASS \pos anchor uses alignment 5 (center); we move via \pos/\move
def _center(W, H, pos):
    x = W // 2
    y = {"center": int(H * 0.5), "upper": int(H * 0.28),
         "lower": int(H * 0.72)}.get(pos, int(H * 0.5))
    return x, y


def _preset_tags(preset, W, H, x, y, dur_ms):
    """Return ASS override tags implementing the motion preset."""
    if preset == "fade_up":
        return "{\\fad(350,300)\\move(%d,%d,%d,%d)}" % (x, y + 60, x, y)
    if preset == "slide_left":
        return "{\\fad(200,250)\\move(%d,%d,%d,%d)}" % (W + 300, y, x, y)
    if preset == "slide_right":
        return "{\\fad(200,250)\\move(%d,%d,%d,%d)}" % (-300, y, x, y)
    if preset == "pop":
        return "{\\fad(120,200)\\t(0,180,\\fscx120\\fscy120)\\t(180,320,\\fscx100\\fscy100)}"
    if preset == "fade":
        return "{\\fad(350,350)}"
    # typewriter handled separately (per-char), default fade
    return "{\\fad(300,300)}"


def _build_ass(spec):
    W, H = spec.get("resolution", [1080, 1920])
    brand = spec.get("brand", {})
    font = _font_family(spec)
    primary = _hex_to_ass(brand.get("primary", "#FFFFFF"))
    header = [
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: %d" % W, "PlayResY: %d" % H, "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
         "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        ("Style: Main,%s,80,%s,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,3,5,"
         "60,60,60,1" % (font, primary)),
        "",
        "[Events]",
        ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"),
    ]
    events = []
    for sc in spec.get("scenes", []):
        text = sc["text"].replace("\n", "\\N")
        start, end = float(sc["start"]), float(sc["end"])
        size = int(sc.get("size", 80))
        pos = sc.get("pos", "center")
        preset = sc.get("preset", "fade")
        x, y = _center(W, H, pos)
        fs = "\\fs%d" % size
        if preset == "typewriter":
            # reveal char-by-char using \k-like staged \alpha per char is complex;
            # emulate with a simple per-substring reveal across the duration.
            n = max(1, len(text))
            step = (end - start) / n
            for i in range(1, n + 1):
                seg = text[:i]
                s = start + (i - 1) * step
                e = end if i == n else start + i * step + 0.02
                tag = "{\\pos(%d,%d)%s}" % (x, y, fs)
                events.append("Dialogue: 0,%s,%s,Main,,0,0,0,,%s%s"
                              % (_t(s), _t(e), tag, seg))
        else:
            tags = _preset_tags(preset, W, H, x, y, int((end - start) * 1000))
            # inject font size into the tag block
            tags = tags[:-1] + fs + "}" if tags.endswith("}") else tags + "{%s}" % fs
            events.append("Dialogue: 0,%s,%s,Main,,0,0,0,,%s%s"
                          % (_t(start), _t(end), tags, text))
    return "\n".join(header + events) + "\n"


def render(spec, out_path):
    ff = _ffmpeg()
    W, H = spec.get("resolution", [1080, 1920])
    fps = spec.get("fps", 30)
    base = spec.get("base", {"type": "color", "color": "0x0B1220", "duration": 6})
    tmp = tempfile.mkdtemp(prefix="txtanim_")
    ass_path = os.path.join(tmp, "s.ass")
    # UTF-8 BOM makes libass/ffmpeg reliably recognize Chinese on macOS and
    # Windows even when the process locale is not UTF-8.
    with open(ass_path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(_build_ass(spec))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # escape path for the ass filter
    ass_arg = ass_path.replace("\\", "/").replace(":", "\\:")
    try:
        if base.get("type") == "video":
            src = base["path"] if os.path.isabs(base["path"]) else os.path.join(ROOT, base["path"])
            if not os.path.isfile(src):
                raise SystemExit("base video not found: %s" % src)
            cmd = [ff, "-y", "-i", src, "-vf", "ass=%s" % ass_arg,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", out_path]
        else:
            dur = base.get("duration", 6)
            color = base.get("color", "0x0B1220")
            cmd = [ff, "-y", "-f", "lavfi",
                   "-i", "color=c=%s:s=%dx%d:d=%s:r=%d" % (color, W, H, dur, fps),
                   "-vf", "ass=%s" % ass_arg,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            raise SystemExit("ffmpeg failed:\n" + p.stdout.decode("utf-8", "replace")[-1500:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def main(argv):
    p = argparse.ArgumentParser(description="kinetic typography / animated subtitles")
    sub = p.add_subparsers(dest="cmd")
    pr = sub.add_parser("render")
    pr.add_argument("--spec", required=True)
    pr.add_argument("--out", required=True)
    pa = sub.add_parser("preview-ass")
    pa.add_argument("--spec", required=True)
    args = p.parse_args(argv)

    if args.cmd in ("render", "preview-ass"):
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
    if args.cmd == "render":
        out = render(spec, args.out)
        print(json.dumps({"ok": True, "out": out}))
    elif args.cmd == "preview-ass":
        print(_build_ass(spec))
    else:
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
