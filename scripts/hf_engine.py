#!/usr/bin/env python3
"""HyperFrames 字幕/动效主引擎 — HTML/CSS/GSAP → MP4。

为什么用它：浏览器渲染真实字体，中文/粤语字幕不再乱码（旧 libass 静态
二进制无 CJK 字体→豆腐块）；GSAP 缓动做商业级动效，确定性布局不跑版。
本引擎是**字幕/动态文字/motion-graphics 的唯一主引擎**；text_anim.py(libass)
仅在无 Node 环境时兜底。

依赖：包内 Node/HyperFrames CLI + 固定离线 GSAP 运行时 + ffmpeg/ffprobe
（优先包内离线二进制）+ Chrome。渲染过程不从 CDN 加载动画脚本。

输入 = 场景 JSON（通常由引导 skill 从定稿脚本生成）；输出 = 烧好动态文字的 MP4。
场景 JSON（与 text_anim.py 兼容，便于平滑迁移）：
{
  "resolution": [1080,1920], "fps": 30, "duration": 7,
  "background": {"type":"color","color":"#0B1220"}   或
  "background": {"type":"video","path":"output/clip.mp4"},
  "brand": {"primary":"#E60012"},
  "scenes": [
    {"text":"65W 快充","start":0,"end":2.5,"preset":"fade_up","size":120,"pos":"center"},
    {"text":"僅 320g・隨時滿電","start":2.5,"end":5,"preset":"slide_left","size":92,"pos":"lower"}
  ]
}
预设(GSAP缓动): fade_up / slide_left / slide_right / pop / typewriter / fade。

CLI:
  python3 hf_engine.py render --spec scenes.json --out output/promo.mp4
  python3 hf_engine.py build  --spec scenes.json --dir output/_hf   # 只生成HTML工程
  python3 hf_engine.py doctor                                        # 检测依赖
"""
import os
import sys
import json
import shutil
import argparse
import subprocess
import tempfile
from proc_utils import run_cmd
import re
import base64
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GSAP_SOURCE = os.path.join(
    ROOT, "offline-assets", "hyperframes-runtime", "vendor", "gsap", "gsap-3.15.0.min.js")

# 跨平台系统中文字体（@font-face local()，无需字体文件）
CJK_LOCALS = ('local("PingFang SC"), local("PingFang HK"), '
              'local("Hiragino Sans GB"), local("Microsoft YaHei"), '
              'local("Noto Sans CJK SC"), local("Source Han Sans SC"), '
              'local("WenQuanYi Zen Hei")')
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
ALLOWED_PRESETS = {"fade_up", "slide_left", "slide_right", "pop", "typewriter", "fade",
                    "rise", "drop", "zoom_in", "bounce", "glow", "slide_up", "slide_down"}
ALLOWED_POSITIONS = {"center", "upper", "lower"}


def _number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("HF_SPEC_INVALID: %s 必须是数字" % label)
    value = float(value)
    if not minimum <= value <= maximum:
        raise ValueError("HF_SPEC_INVALID: %s 超出允许范围" % label)
    return value


def _hex_color(value, label):
    if not isinstance(value, str) or not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("HF_SPEC_INVALID: %s 必须是 #RRGGBB 或 #RRGGBBAA" % label)
    return value


def validate_spec(spec):
    """Fail closed before untrusted values are interpolated into HTML or CSS."""
    if not isinstance(spec, dict):
        raise ValueError("HF_SPEC_INVALID: spec 必须是对象")
    resolution = spec.get("resolution", [1080, 1920])
    if not isinstance(resolution, list) or len(resolution) != 2:
        raise ValueError("HF_SPEC_INVALID: resolution 必须是 [width,height]")
    _number(resolution[0], "resolution.width", 16, 8192)
    _number(resolution[1], "resolution.height", 16, 8192)
    _number(spec.get("fps", 30), "fps", 1, 240)
    if spec.get("duration") is not None:
        _number(spec["duration"], "duration", 0.01, 86400)
    brand = spec.get("brand") or {}
    if not isinstance(brand, dict):
        raise ValueError("HF_SPEC_INVALID: brand 必须是对象")
    for key, value in brand.items():
        if "color" in key.lower() or key in {"primary", "secondary", "accent"}:
            _hex_color(value, "brand.%s" % key)
    background = spec.get("background") or {"type": "color", "color": "#0B1220"}
    if not isinstance(background, dict) or background.get("type", "color") not in {
            "color", "transparent", "video"}:
        raise ValueError("HF_SPEC_INVALID: background.type 不受支持")
    if background.get("type", "color") == "color":
        _hex_color(background.get("color", "#0B1220"), "background.color")
    scenes = spec.get("scenes", [])
    if not isinstance(scenes, list) or len(scenes) > 10000:
        raise ValueError("HF_SPEC_INVALID: scenes 必须是有限数组")
    for index, scene in enumerate(scenes):
        label = "scenes[%d]" % index
        if not isinstance(scene, dict) or not isinstance(scene.get("text"), str):
            raise ValueError("HF_SPEC_INVALID: %s.text 必须是字符串" % label)
        if len(scene["text"]) > 20000 or "\x00" in scene["text"]:
            raise ValueError("HF_SPEC_INVALID: %s.text 无效" % label)
        start = _number(scene.get("start"), label + ".start", 0, 86400)
        end = _number(scene.get("end"), label + ".end", 0, 86400)
        if end <= start:
            raise ValueError("HF_SPEC_INVALID: %s.end 必须晚于 start" % label)
        _number(scene.get("size", 84), label + ".size", 1, 2048)
        if scene.get("preset", "fade") not in ALLOWED_PRESETS:
            raise ValueError("HF_SPEC_INVALID: %s.preset 不受支持" % label)
        if scene.get("pos", "center") not in ALLOWED_POSITIONS:
            raise ValueError("HF_SPEC_INVALID: %s.pos 不受支持" % label)
        for key in ("bottom_px", "top_px", "left_px", "right_px", "max_height_px"):
            if key in scene:
                _number(scene[key], "%s.%s" % (label, key), 0, 8192)
        for key, value in scene.items():
            if "color" in key.lower():
                _hex_color(value, "%s.%s" % (label, key))
    return spec


def _cjk_font_css():
    """Prefer a platform font known to contain Simplified Chinese glyphs."""
    try:
        import cjk_font
        chosen = cjk_font.family()
        return 'local("%s"), %s' % (chosen, CJK_LOCALS)
    except Exception:
        return CJK_LOCALS


def ensure_ffmpeg_on_path():
    """把 static-ffmpeg 的 ffmpeg+ffprobe 目录加进 PATH（HyperFrames 硬依赖二者）。

    返回 (ok, msg)。优先系统已有的 ffmpeg/ffprobe；否则用 static_ffmpeg 静态二进制。
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True, "系统已有 ffmpeg+ffprobe"
    try:
        from static_ffmpeg import run
        ff, fp = run.get_or_fetch_platform_executables_else_raise()
        bindir = os.path.dirname(ff)
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            return True, "已注入 static-ffmpeg: %s" % bindir
        return False, "static-ffmpeg 解出但 PATH 注入后仍缺 ffprobe"
    except Exception as e:
        return False, ("缺 ffmpeg/ffprobe 且 static-ffmpeg 不可用: %s。"
                       "请 `pip3 install static-ffmpeg`" % e)


def _has_node():
    bundled = os.path.join(ROOT, "offline-assets", "node-runtime", "bin")
    if os.path.isfile(os.path.join(bundled, "node")):
        os.environ["PATH"] = bundled + os.pathsep + os.environ.get("PATH", "")
    return bool(shutil.which("node") and shutil.which("npx"))


def _npx_command(args):
    """Use bundled npm prefix offline when present, otherwise normal npx."""
    bundled = os.path.join(ROOT, "offline-assets", "node-runtime")
    if os.path.isfile(os.path.join(bundled, "bin", "node")):
        return ["npx", "--offline", "--prefix", bundled] + args
    return ["npx", "--yes"] + args


def _pos_top(H, pos, size):
    """把 center/upper/lower 语义位置换成 top 像素（竖排文字大致居中）。"""
    frac = {"center": 0.5, "upper": 0.28, "lower": 0.72}.get(pos, 0.5)
    return int(H * frac - size * 0.6)


def _scene_position_css(sc, W, H, size):
    """把单条字幕的定位换成绝对定位 CSS 字符串。

    优先用视觉模型推荐的**精确像素**（bottom_px/left_px/right_px/max_height_px）——
    绕过 pos 三档(upper/center/lower)太粗的限制，字幕落在画面安全区。
    没给精确像素时回退旧的 pos 语义档位（top 百分比定位）。
    """
    has_precise = any(k in sc for k in ("bottom_px", "top_px", "left_px", "right_px"))
    if has_precise:
        css = ["position:absolute", "text-align:center"]
        if "bottom_px" in sc:
            css.append("bottom:%dpx" % int(sc["bottom_px"]))
        if "top_px" in sc:
            css.append("top:%dpx" % int(sc["top_px"]))
        # 左右边距默认给个安全内缩；模型给了就用模型的
        css.append("left:%dpx" % int(sc.get("left_px", 40)))
        css.append("right:%dpx" % int(sc.get("right_px", 40)))
        # 未显式给 left/right 时，width 交给 left/right 约束；不再钉死整宽
        css.append("width:auto")
        if "max_height_px" in sc:
            css.append("max-height:%dpx" % int(sc["max_height_px"]))
            css.append("overflow:hidden")
        return ";".join(css) + ";"
    # 回退：旧 pos 语义档位（整宽 + top 百分比）
    top = _pos_top(H, sc.get("pos", "center"), size)
    return "left:0;width:%dpx;text-align:center;top:%dpx;" % (W, top)


def _gsap_from(preset):
    """预设 → GSAP from() 参数（入场动效，缓动是商业级手感的关键）。"""
    table = {
        "fade_up":     '{ opacity:0, y:70, duration:0.7, ease:"power3.out" }',
        "slide_left":  '{ opacity:0, x:-160, duration:0.7, ease:"power3.out" }',
        "slide_right": '{ opacity:0, x:160, duration:0.7, ease:"power3.out" }',
        "pop":         '{ opacity:0, scale:0.5, duration:0.6, ease:"back.out(2.0)" }',
        "fade":        '{ opacity:0, duration:0.6, ease:"power2.out" }',
        "typewriter":  '{ opacity:0, duration:0.3, ease:"none" }',
        # 新增预设
        "rise":        '{ opacity:0, y:100, scale:0.9, duration:0.8, ease:"power4.out" }',
        "drop":        '{ opacity:0, y:-80, scale:0.95, duration:0.7, ease:"power3.out" }',
        "zoom_in":     '{ opacity:0, scale:0.3, duration:0.6, ease:"back.out(1.5)" }',
        "bounce":      '{ opacity:0, scale:0.4, y:60, duration:0.8, ease:"elastic.out(1, 0.5)" }',
        "glow":        '{ opacity:0, scale:0.8, duration:0.8, ease:"power2.out", '
                       'onComplete: function() { gsap.to(this.targets()[0], '
                       '{ textShadow: "0 0 20px rgba(255,255,255,0.5), 0 0 40px rgba(255,255,255,0.2)", '
                       'duration: 0.4, yoyo: true, repeat: 1 }); } }',
        "slide_up":    '{ opacity:0, y:50, duration:0.6, ease:"power2.out" }',
        "slide_down":  '{ opacity:0, y:-50, duration:0.6, ease:"power2.out" }',
    }
    return table.get(preset, table["fade"])


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(spec):
    """场景 JSON → HyperFrames composition HTML 字符串（含 CJK 字体 + GSAP 时间线）。"""
    validate_spec(spec)
    W, H = spec.get("resolution", [1080, 1920])
    fps = spec.get("fps", 30)
    scenes = spec.get("scenes", [])
    duration = spec.get("duration") or (max((float(s["end"]) for s in scenes), default=6))
    brand = spec.get("brand", {})
    primary = brand.get("primary", "#E60012")
    bg = spec.get("background", {"type": "color", "color": "#0B1220"})
    bg_type = bg.get("type")
    # transparent：字幕层专用，配合 --format mov/webm 出 alpha 通道，overlay 叠回成片
    if bg_type == "transparent":
        bg_css = "background:transparent;"
    elif bg_type == "color":
        bg_css = "background:%s;" % bg.get("color", "#0B1220")
    else:
        bg_css = "background:#000;"

    nonce = base64.b64encode(secrets.token_bytes(18)).decode("ascii")
    csp = ("default-src &#39;none&#39;; script-src &#39;self&#39; &#39;nonce-%s&#39;; "
           "style-src &#39;unsafe-inline&#39;; connect-src &#39;none&#39;; "
           "img-src &#39;none&#39;; media-src &#39;none&#39;; font-src &#39;self&#39;; "
           "object-src &#39;none&#39;; frame-src &#39;none&#39;; base-uri &#39;none&#39;; "
           "form-action &#39;none&#39;" % nonce)
    L = [
        '<!doctype html>', '<html lang="zh-Hant">', '<head>',
        '<meta charset="UTF-8" />',
         '<meta http-equiv="Content-Security-Policy" content="%s" />' % csp,
        '<meta name="viewport" content="width=%d, height=%d" />' % (W, H),
         '<script src="gsap.min.js"></script>',
        '<style>',
        '@font-face{font-family:"CJK";src:%s;}' % _cjk_font_css(),
        '*{margin:0;padding:0;box-sizing:border-box;}',
        'html,body{width:%dpx;height:%dpx;overflow:hidden;%sfont-family:"CJK",sans-serif;}' % (W, H, bg_css),
        '.cap{position:absolute;color:#fff;'
        'font-weight:700;letter-spacing:1px;'
        'text-shadow:0 0 1px rgba(0,0,0,0.9),0 2px 4px rgba(0,0,0,0.6),0 4px 16px rgba(0,0,0,0.4);'
        'padding:16px 32px;border-radius:12px;'
        'background:rgba(8,12,24,0.45);border:1px solid rgba(255,255,255,0.06);'
        'box-shadow:0 8px 32px rgba(0,0,0,0.3),inset 0 1px 0 rgba(255,255,255,0.05);}',
        '.accent{color:%s;font-weight:800;}' % primary,
        '</style>', '</head>', '<body>',
        '<div id="root" data-composition-id="main" data-start="0" data-duration="%s" '
        'data-width="%d" data-height="%d" data-fps="%d">' % (duration, W, H, fps),
    ]
    tl = []  # GSAP timeline lines
    for i, sc in enumerate(scenes):
        sid = "s%d" % i
        start, end = float(sc["start"]), float(sc["end"])
        size = int(sc.get("size", 84))
        pos_css = _scene_position_css(sc, W, H, size)
        preset = sc.get("preset", "fade")
        text = _esc(sc["text"]).replace("[[", '<span class="accent">').replace("]]", "</span>")
        L.append('<div id="%s" class="clip cap" data-start="%s" data-duration="%s" '
                  'data-track-index="1" style="%sfont-size:%dpx;">%s</div>'
                  % (sid, start, end - start, pos_css, size, text))
        tl.append('tl.from("#%s", %s, %s);' % (sid, _gsap_from(preset), start))
    L += [
        '</div>', '<script nonce="%s">' % nonce,
        'window.__timelines = window.__timelines || {};',
        'const tl = gsap.timeline({ paused: true });',
    ] + tl + [
        'window.__timelines["main"] = tl;',
        '</script>', '</body>', '</html>',
    ]
    return "\n".join(L) + "\n"


def build_project(spec, work_dir):
    """把场景写成一个 HyperFrames 工程目录，返回 index.html 路径。"""
    os.makedirs(work_dir, exist_ok=True)
    if not os.path.isfile(GSAP_SOURCE):
        raise SystemExit("NO_LOCAL_GSAP: 缺少固定离线 GSAP 运行时: %s" % GSAP_SOURCE)
    shutil.copyfile(GSAP_SOURCE, os.path.join(work_dir, "gsap.min.js"))
    html_path = os.path.join(work_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_html(spec))
    # 最小 hyperframes.json（render 命令可无项目文件运行，但保留占位便于 preview）
    with open(os.path.join(work_dir, "hyperframes.json"), "w", encoding="utf-8") as f:
        json.dump({"entry": "index.html"}, f)
    return html_path


def _run_hf(args, cwd, verbose=True):
    cmd = _npx_command(["hyperframes"] + args)
    p = run_cmd(cmd, cwd=cwd, timeout=600, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    if verbose:
        print(out[-1200:], flush=True)
    return p.returncode, out


def _fmt_from_out(out_path, fmt=None):
    """按显式 fmt 或输出扩展名推断 hyperframes --format。
    mov→ProRes 4444(alpha,Apple Silicon 硬件加速);webm→VP9(alpha,体积小);其余 mp4。"""
    if fmt:
        return fmt.lower()
    ext = os.path.splitext(out_path)[1].lower().lstrip(".")
    return ext if ext in ("mov", "webm", "mp4") else "mp4"


def _probe_pix_fmt(path):
    """ffprobe 视频流的 pix_fmt/codec_name。探测失败返回 (None, None)。"""
    try:
        ok, _msg = ensure_ffmpeg_on_path()
        if not ok:
            return None, None
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None, None
        p = run_cmd(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt,codec_name",
             "-of", "default=nk=1:nw=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        lines = p.stdout.decode("utf-8", "replace").strip().splitlines()
        codec = lines[0] if len(lines) > 0 else None
        pix_fmt = lines[1] if len(lines) > 1 else None
        return codec, pix_fmt
    except Exception:
        return None, None


def render(spec, out_path, work_dir=None, verbose=True, fmt=None, quality="high"):
    """场景 JSON → 视频。返回 out_path。缺 node 时抛错（由调用方决定是否走 libass 兜底）。

    fmt: None=按 out_path 扩展名推断；'mov'=ProRes 4444 alpha（字幕透明层，推荐，
      Apple Silicon 硬件加速无损）；'webm'=VP9 alpha（体积小但慢）；'mp4'=普通不透明。
    透明字幕层：spec.background.type='transparent' + fmt='mov'，再用 ffmpeg overlay 叠回成片。

    **透明层防呆（重要）**：当 spec.background.type=='transparent' 时，本函数强制要求
    render_fmt in ('mov','webm')——绝不允许静默把透明字幕层渲成 'mp4'。这是因为 mp4/h264
    不支持 alpha 通道：浏览器画布的 `background:transparent` CSS 在无 alpha 编码格式下会被
    合成成不透明黑底，字幕层退化成"铺满全屏的黑色矩形+白字"。后续 subtitle_overlay.compose()
    的 ffmpeg overlay 会把这块黑色矩形整块盖在底片上——症状正是"最终成片只剩字幕和声音，
    实际画面被完全遮盖"（真实复现过：.mp4 输出的字幕层 ffprobe 显示 codec=h264/pix_fmt=yuv420p，
    无 alpha；.mov 正确输出显示 codec=prores/pix_fmt=yuva444p12le，带 alpha）。
    渲染完成后额外用 ffprobe 校验实际产物确有 alpha 通道（yuva*/rgba*/argb 等），
    校验失败同样报错阻断，不静默交付一个"看起来渲染成功但没有透明度"的文件。
    """
    if not _has_node():
        raise SystemExit("NO_NODE: 未检测到 node/npx，HyperFrames 不可用。"
                         "请先装 Node.js，或改用 text_anim.py(libass) 兜底。")
    ok, msg = ensure_ffmpeg_on_path()
    if verbose:
        print("[ffmpeg] " + msg, flush=True)
    if not ok:
        raise SystemExit("NO_FFMPEG: " + msg)
    owned_work_dir = work_dir is None
    output_root = os.path.join(ROOT, "output")
    os.makedirs(output_root, exist_ok=True)
    work_dir = work_dir or tempfile.mkdtemp(prefix="hf-", dir=output_root)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    render_fmt = _fmt_from_out(out_path, fmt)
    fd, render_path = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(out_path), suffix="." + render_fmt,
        dir=os.path.dirname(out_path) or ".")
    os.close(fd)
    os.unlink(render_path)
    try:
        build_project(spec, work_dir)

        bg_type = (spec.get("background") or {}).get("type")
        needs_alpha = bg_type == "transparent"
        if needs_alpha and render_fmt not in ("mov", "webm"):
            raise SystemExit(
            "ALPHA_FORMAT_MISMATCH: spec.background.type='transparent' 要求透明字幕层，"
            "但推断/指定的输出格式是 '%s'（不支持 alpha 通道）。mp4/h264 无法保留透明度，"
            "渲染出的字幕层会变成不透明黑底，overlay 时整块盖住底片画面（真实故障模式，"
            "非理论假设）。请把 --out 后缀改成 .mov/.webm，或显式传 --format mov。" % render_fmt)

        rc, out = _run_hf(["lint"], work_dir, verbose=verbose)
        if rc != 0:
            raise SystemExit("HyperFrames lint 失败:\n" + out[-1000:])
        render_args = ["render", "--output", render_path, "--quality", quality,
                       "--format", render_fmt]
        rc, out = _run_hf(render_args, work_dir, verbose=verbose)
        if rc != 0 or not os.path.isfile(render_path) or os.path.getsize(render_path) == 0:
            raise SystemExit("HyperFrames render 失败:\n" + out[-1200:])

        codec, pix_fmt = _probe_pix_fmt(render_path)
        if not codec or not pix_fmt:
            raise SystemExit("MEDIA_VERIFY_FAILED: HyperFrames 产物无法由 ffprobe 识别")
        if needs_alpha:
            has_alpha = bool(pix_fmt) and ("yuva" in pix_fmt or "rgba" in pix_fmt
                                        or "argb" in pix_fmt or "abgr" in pix_fmt
                                        or "bgra" in pix_fmt or "gbrap" in pix_fmt)
            if not has_alpha:
                raise SystemExit(
                "ALPHA_VERIFY_FAILED: 渲染完成但实测产物无 alpha 通道 "
                "(codec=%s, pix_fmt=%s)。这个字幕层如果拿去 overlay 会整块盖住底片画面。"
                "不予交付——请检查 HyperFrames/ffmpeg 版本，或改用 --format mov 重渲。"
                    % (codec, pix_fmt))
            if verbose:
                print("[ffmpeg] alpha 通道校验通过 (codec=%s, pix_fmt=%s)" % (codec, pix_fmt),
                      flush=True)
        os.replace(render_path, out_path)
        return out_path
    finally:
        if os.path.exists(render_path):
            os.unlink(render_path)
        if owned_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def doctor():
    print("Node/npx:", "OK" if _has_node() else "缺失（HyperFrames 需要）")
    ok, msg = ensure_ffmpeg_on_path()
    print("ffmpeg+ffprobe:", ("OK — " + msg) if ok else ("缺 — " + msg))
    return 0 if (_has_node() and ok) else 1


def main(argv):
    p = argparse.ArgumentParser(description="HyperFrames 字幕/动效引擎")
    sub = p.add_subparsers(dest="cmd")
    pr = sub.add_parser("render"); pr.add_argument("--spec", required=True)
    pr.add_argument("--out", required=True); pr.add_argument("--dir", default=None)
    pr.add_argument("--format", dest="fmt", default=None,
                    choices=["mov", "webm", "mp4"],
                    help="mov=ProRes4444 alpha(透明字幕层,推荐);webm=VP9 alpha(体积小);"
                         "mp4=不透明。缺省按 --out 扩展名推断。")
    pr.add_argument("--quality", default="high")
    pb = sub.add_parser("build"); pb.add_argument("--spec", required=True)
    pb.add_argument("--dir", required=True)
    sub.add_parser("doctor")
    args = p.parse_args(argv)

    if args.cmd == "doctor":
        return doctor()
    if args.cmd in ("render", "build"):
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
    if args.cmd == "render":
        out = render(spec, args.out, work_dir=args.dir, fmt=args.fmt, quality=args.quality)
        print(json.dumps({"ok": True, "out": out}, ensure_ascii=False))
        return 0
    if args.cmd == "build":
        html = build_project(spec, args.dir)
        print(json.dumps({"ok": True, "html": html}, ensure_ascii=False))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
