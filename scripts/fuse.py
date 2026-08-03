#!/usr/bin/env python3
"""视频合成 —— 纯机械叠加/拼接，本地零模型。

定位铁律：本地不跑任何模型。数字人与背景的融合（抠像/换背景/人景合一）一律由
外部模型完成——首选路线 A（video_engine.py --type 4/5 把场景图作参考图，模型直接
生成人已在场景中的视频），或路线 C（matte.py compose 用 img2img 外部合成人景图再
驱动成视频）。**成片里数字人本就在背景中**，无需本地抠像。

本脚本只做 ffmpeg 能干的无模型活：
  1) 画中画角窗叠加（overlay）：把一段【不透明】的解说小窗叠到主画面角落（slot=corner）。
     用于「主画面是产品运镜，右下角放一段单独生成的数字人画外音小窗」这类布局。
     注意：叠的是不透明视频，不做透明抠像；要人景融合请走路线 A/C。
  2) 分段拼接（见 compose.py concat）：多段外部生成的成片按顺序接起来。

字幕/特效层：把成片作为 hf_engine.py 的 background.video 再叠字幕，中文不乱码。

slot 定位（画中画角窗）：
  corner 右下角小窗（默认，画外音/角落解说）
  left   左下贴边   right 右下贴边   full 底部居中放大

依赖：ffmpeg/ffprobe(static-ffmpeg)，无模型、无 GPU。

CLI:
  python3 fuse.py overlay --bg output/main.mp4 --human output/narrator.mp4 \
      --slot corner --out output/final.mp4 [--scale 0.9]
  python3 fuse.py doctor
"""
import os
import sys
import shutil
import argparse
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _has(cmd):
    return shutil.which(cmd) is not None


def ensure_ffmpeg_on_path():
    if _has("ffmpeg") and _has("ffprobe"):
        return shutil.which("ffmpeg")
    from static_ffmpeg import run
    ff, fp = run.get_or_fetch_platform_executables_else_raise()
    os.environ["PATH"] = os.path.dirname(ff) + os.pathsep + os.environ.get("PATH", "")
    return ff


def _probe_wh(ff, video):
    import json as _json
    fp = shutil.which("ffprobe")
    out = subprocess.check_output([fp, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", video]).decode()
    s = _json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _has_audio(video):
    fp = shutil.which("ffprobe")
    result = subprocess.run(
        [fp, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=index", "-of", "csv=p=0", video],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode == 0 and bool(result.stdout.strip())


def _probe_duration(video):
    fp = shutil.which("ffprobe")
    result = subprocess.run(
        [fp, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", video], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        duration = 0
    if result.returncode != 0 or duration <= 0:
        raise SystemExit("无法读取主视频时长: %s" % video)
    return duration


def _valid_video(path):
    fp = shutil.which("ffprobe")
    result = subprocess.run(
        [fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    return (result.returncode == 0 and "video" in result.stdout and
            os.path.isfile(path) and os.path.getsize(path) > 0)


def _slot_geometry(slot, W, H, scale):
    """返回 (human_target_w, human_target_h, overlay_x, overlay_y)。
    数字人按高度铺满 slot 区域，横向居中/靠边。"""
    if slot == "full":
        hw, hh = int(W * scale), int(H * scale)
        return hw, hh, "(W-w)/2", "H-h"          # 底部居中
    if slot == "corner":
        hh = int(H * 0.42 * scale)
        return -1, hh, "W-w-40", "H-h-40"         # 右下角
    if slot == "left":
        hh = int(H * 0.92 * scale)
        return -1, hh, "40", "H-h"                # 左下贴边
    # right（默认）
    hh = int(H * 0.92 * scale)
    return -1, hh, "W-w-40", "H-h"                # 右下贴边


def overlay(bg, human, out_path, slot="right", scale=0.9, mix_audio=True):
    """把不透明解说小窗叠到主画面角落。

    mix_audio=True（默认）：amerge 混合主画面音轨(BGM/旁白)和数字人音轨，
      两路都保留——解决「叠数字人后 BGM 丢失」问题。
    mix_audio=False：仅保留数字人音轨（旧行为，适合主画面本无声音的情况）。
    """
    ff = ensure_ffmpeg_on_path()
    W, H = _probe_wh(ff, bg)
    tw, th, ox, oy = _slot_geometry(slot, W, H, scale)
    bg_duration = _probe_duration(bg)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(out_path), suffix=os.path.splitext(out_path)[1],
        dir=os.path.dirname(out_path) or ".")
    os.close(fd)
    os.unlink(temp_path)
    # Full-frame narration should fill the composition without distorting a
    # portrait source. Other slots preserve their existing height-based fit.
    scale_expr = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" %
                  (tw, th, tw, th) if slot == "full"
                  else "scale=-1:%d" % th)
    bg_audio, human_audio = _has_audio(bg), _has_audio(human)
    if mix_audio and bg_audio and human_audio:
        fc = (
            "[1:v]%s[hum];"
            "[0:v][hum]overlay=%s:%s:format=auto:eof_action=pass:shortest=0[v];"
            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[a]"
            % (scale_expr, ox, oy)
        )
        cmd = [ff, "-hide_banner", "-y", "-i", bg, "-i", human,
               "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-t", "%.6f" % bg_duration, temp_path]
    else:
        fc = ("[1:v]%s[hum];[0:v][hum]overlay=%s:%s:format=auto:eof_action=pass:shortest=0[v]"
              % (scale_expr, ox, oy))
        audio_map = "1:a" if human_audio else "0:a" if bg_audio else None
        cmd = [ff, "-hide_banner", "-y", "-i", bg, "-i", human,
               "-filter_complex", fc, "-map", "[v]"]
        if audio_map:
            cmd.extend(["-map", audio_map])
        cmd.extend([
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-t", "%.6f" % bg_duration, temp_path])
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise SystemExit("ffmpeg overlay 失败:\n" + p.stdout.decode("utf-8", "replace")[-1500:])
    if not _valid_video(temp_path):
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise SystemExit("ffmpeg overlay 未生成有效文件")
    os.replace(temp_path, out_path)
    import json as _json
    print(_json.dumps({"ok": True, "out": out_path, "canvas": [W, H], "slot": slot,
                       "mix_audio": mix_audio}, ensure_ascii=False))
    return out_path


def doctor():
    print("=== 合成引擎体检 ===")
    try:
        ff = ensure_ffmpeg_on_path()
        print("ffmpeg+ffprobe: OK")
    except Exception as e:
        print("ffmpeg+ffprobe: 缺失 —", e); return 1
    print("结论: READY")
    return 0


def main():
    ap = argparse.ArgumentParser(description="视频合成（画中画角窗叠加，纯ffmpeg无模型）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("overlay", help="把不透明解说小窗叠到主画面角落（无抠像）")
    o.add_argument("--bg", required=True, help="主画面视频（Remotion 运镜/产品展示）")
    o.add_argument("--human", required=True, help="要叠的解说小窗视频（不透明，外部生成）")
    o.add_argument("--out", required=True)
    o.add_argument("--slot", default="corner", choices=["left", "right", "corner", "full"])
    o.add_argument("--scale", type=float, default=0.9)
    o.add_argument("--no-mix-audio", dest="no_mix_audio", action="store_true",
                   help="仅保留数字人音轨，丢弃主画面音轨（默认：amix 混合两路音轨）")
    sub.add_parser("doctor", help="检测 ffmpeg")
    a = ap.parse_args()
    if a.cmd == "overlay":
        overlay(a.bg, a.human, a.out, a.slot, a.scale, mix_audio=not a.no_mix_audio)
    elif a.cmd == "doctor":
        sys.exit(doctor())


if __name__ == "__main__":
    main()
