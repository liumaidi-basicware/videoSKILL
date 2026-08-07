#!/usr/bin/env python3
"""Remotion 运镜/编排引擎包装器 —— 数字人讲解型/产品展示型成片的底层。

分工：Remotion 负责**运镜**（推拉摇移/Ken Burns）+ **PPT 内容页序列** + 数字人
画中画布局槽；HyperFrames 负责最上层字幕/特效；Kling 出会说话的数字人。

本脚本把一份 shotlist JSON 喂给 remotion_engine/ 工程渲成背景+运镜 MP4。

依赖：node/npx + Remotion 工程依赖（首次 npm install）+ ffmpeg/ffprobe(static-ffmpeg)
     + Chrome Headless Shell（Remotion 自带，首次下载；本机自带解压有 bug，doctor/render
       会自动手动 unzip + chmod 兜底）。

shotlist JSON（客户无关，由引导 skill 生成）：
{
  "width":1080,"height":1920,"fps":30,"brandPrimary":"#E60012",
  "shots":[
    {"durationInFrames":90,"move":"ken_burns","image":"assets/x/factory.jpg",
     "title":"智能工厂","bullets":["全自动产线","出货前100%质检"],"humanSlot":"right"},
    {"durationInFrames":60,"move":"push_in","video":"output/clip.mp4","transition":"fade"}
  ]
}
move: ken_burns/push_in/pull_out/pan_left/pan_right/tilt_up/tilt_down/still
humanSlot: none/left/right/full/corner（留给 fuse.py 叠抠像数字人）

CLI:
  python3 remotion_engine.py render --shotlist shots.json --out output/bg.mp4
  python3 remotion_engine.py doctor        # 检测+修复 node/依赖/Chrome
"""
import os
import sys
import json
import zipfile
import shutil
import glob
import argparse
import subprocess
import platform
import hashlib
import tempfile
import copy
import ipaddress
import socket
import urllib.request
import urllib.error
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "remotion_engine")
COMPOSITION = "Shots"
CONTENT_COMPOSITION = "Content"  # 文档/PPT 内容动效（remotion-com-skills 组件库）
KINETIC_COMPOSITION = "HorizontalKinetic"  # 横版口播：字幕+章节+卡片+PIP
SHOTCRAFT_COMPOSITION = "Shotcraft"
REMOTE_MEDIA_ALLOWLIST_ENV = "REMOTION_MEDIA_ALLOWLIST"


def _has(cmd):
    return shutil.which(cmd) is not None


def ensure_ffmpeg_on_path():
    """把 ffmpeg+ffprobe 放上 PATH（Remotion 拼接音视频需要）。优先系统，否则 static-ffmpeg。"""
    if _has("ffmpeg") and _has("ffprobe"):
        return True, "系统已有 ffmpeg+ffprobe"
    bundled_dir = os.path.join(ROOT, "offline-assets", "ffmpeg")
    if os.path.isdir(bundled_dir):
        for current, _dirs, files in os.walk(bundled_dir):
            if "ffmpeg" in files and "ffprobe" in files:
                os.environ["PATH"] = current + os.pathsep + os.environ.get("PATH", "")
                if _has("ffmpeg") and _has("ffprobe"):
                    return True, "已注入包内离线 ffmpeg: " + current
    try:
        from static_ffmpeg import run
        ff, fp = run.get_or_fetch_platform_executables_else_raise()
        os.environ["PATH"] = os.path.dirname(ff) + os.pathsep + os.environ.get("PATH", "")
        if _has("ffmpeg") and _has("ffprobe"):
            return True, "已注入 static-ffmpeg: " + os.path.dirname(ff)
    except Exception as e:  # noqa
        try:
            import static_ffmpeg
            root = os.path.dirname(os.path.abspath(static_ffmpeg.__file__))
            matches = glob.glob(os.path.join(root, "bin", "**", "ffmpeg"), recursive=True)
            probes = glob.glob(os.path.join(root, "bin", "**", "ffprobe"), recursive=True)
            if matches and probes and os.path.dirname(matches[0]) == os.path.dirname(probes[0]):
                os.environ["PATH"] = os.path.dirname(matches[0]) + os.pathsep + os.environ.get("PATH", "")
                if _has("ffmpeg") and _has("ffprobe"):
                    return True, "已直接注入 static-ffmpeg 二进制: " + os.path.dirname(matches[0])
        except Exception:
            pass
        return False, "static-ffmpeg 不可用: %s" % e
    return False, "未找到 ffmpeg/ffprobe"


def _use_bundled_node():
    bundled = os.path.join(ROOT, "offline-assets", "node-runtime", "bin")
    node = os.path.join(bundled, "node")
    if os.path.isfile(node):
        os.environ["PATH"] = bundled + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def normalize_input(input_path, output_path):
    """Normalize a talking-head source for the 1920x1080 kinetic composition."""
    ok, message = ensure_ffmpeg_on_path()
    if not ok:
        raise SystemExit("ERROR: " + message)
    if not os.path.isfile(input_path):
        raise SystemExit("ERROR: 输入口播视频不存在: %s" % input_path)
    ffmpeg = shutil.which("ffmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    output_path = os.path.abspath(output_path)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % os.path.basename(output_path),
                                     suffix=os.path.splitext(output_path)[1] or ".mp4",
                                     dir=os.path.dirname(output_path) or ".")
    os.close(fd)
    os.unlink(temporary)
    cmd = [ffmpeg, "-y", "-i", input_path,
           "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", temporary]
    result = subprocess.run(cmd)
    if result.returncode != 0 or not _media_output_ok(temporary):
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise SystemExit("ERROR: 口播视频标准化失败: %s" % output_path)
    os.replace(temporary, output_path)
    print(json.dumps({"ok": True, "out": os.path.abspath(output_path), "format": "1920x1080 H.264/AAC"}, ensure_ascii=False))
    return output_path


def _npx():
    return shutil.which("npx")


def fix_chrome_headless_shell():
    """兜底修复：Remotion 自带解压在部分机器坏了（zip 在但没解出可执行）。
    手动 unzip + chmod +x。返回 (fixed_or_ok, msg)。"""
    base = os.path.join(ENGINE, "node_modules", ".remotion", "chrome-headless-shell")
    if not os.path.isdir(base):
        return True, "Chrome 尚未下载（首次 render 时下载）"
    # 找 zip 与目标平台目录
    fixed = []
    for root, dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".zip"):
                zpath = os.path.join(root, f)
                # 解压到 zip 同目录
                try:
                    with zipfile.ZipFile(zpath) as z:
                        z.extractall(root)
                    fixed.append(f)
                except Exception:
                    pass
    # 给所有 chrome-headless-shell 可执行文件加执行权限
    made = 0
    for root, dirs, files in os.walk(base):
        for f in files:
            if f in ("chrome-headless-shell", "chrome-headless-shell.exe"):
                p = os.path.join(root, f)
                try:
                    os.chmod(p, 0o755)
                    made += 1
                except Exception:
                    pass
    if made:
        return True, "Chrome 就位（手动解压%d个zip, chmod %d个可执行）" % (len(fixed), made)
    return False, "Chrome 解压后未找到可执行文件"


def find_chrome_executable():
    """定位已解压的 chrome-headless-shell 可执行文件（用于 --browser-executable，
    避免每次 render 都重新下载/校验 Chrome，网络不稳时尤其关键）。找不到返回 None。"""
    base = os.path.join(ENGINE, "node_modules", ".remotion", "chrome-headless-shell")
    if not os.path.isdir(base):
        return None
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f in ("chrome-headless-shell", "chrome-headless-shell.exe"):
                p = os.path.join(root, f)
                if os.access(p, os.X_OK):
                    return p
    return None


def _host_allowed(host, allowlist):
    host = (host or "").lower().rstrip(".")
    for item in allowlist:
        item = item.lower().strip().rstrip(".")
        if item.startswith("*.") and host.endswith(item[1:]) and host != item[2:]:
            return True
        if host == item:
            return True
    return False


def _validate_remote_media(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("ERROR: REMOTE_MEDIA_REJECTED: 仅允许白名单 HTTPS 媒体")
    allowlist = [x for x in os.environ.get(REMOTE_MEDIA_ALLOWLIST_ENV, "").split(",") if x.strip()]
    if not _host_allowed(parsed.hostname, allowlist):
        raise SystemExit("ERROR: REMOTE_MEDIA_NOT_ALLOWLISTED: %s" % parsed.hostname)
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443,
                                                               type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise SystemExit("ERROR: REMOTE_MEDIA_DNS_FAILED: %s" % exc)
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise SystemExit("ERROR: REMOTE_MEDIA_PRIVATE_ADDRESS: %s" % address)
    return url


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_media(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _stage_remote_media(url, public_dir, max_bytes=2 * 1024 * 1024 * 1024):
    """Download allowlisted remote media so Chromium never accesses the network."""
    _validate_remote_media(url)
    opener = urllib.request.build_opener(_ValidatedRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "BasicRouter-Remotion/1"})
    try:
        response = opener.open(request, timeout=600)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise SystemExit("ERROR: REMOTE_MEDIA_DOWNLOAD_FAILED: %s" % exc)
    with response:
        final_url = response.geturl()
        _validate_remote_media(final_url)
        try:
            peer = response.fp.raw._sock.getpeername()[0]
            if not ipaddress.ip_address(peer.split("%", 1)[0]).is_global:
                raise SystemExit("ERROR: REMOTE_MEDIA_PRIVATE_PEER: %s" % peer)
        except AttributeError:
            raise SystemExit("ERROR: REMOTE_MEDIA_PEER_UNVERIFIED")
        expected = response.headers.get("Content-Length")
        if expected and int(expected) > max_bytes:
            raise SystemExit("ERROR: REMOTE_MEDIA_TOO_LARGE")
        suffix = os.path.splitext(urlsplit(final_url).path)[1].lower() or ".bin"
        fd, temporary = tempfile.mkstemp(prefix=".remote-", suffix=suffix, dir=public_dir)
        digest = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise SystemExit("ERROR: REMOTE_MEDIA_TOO_LARGE")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if expected and received != int(expected):
                raise SystemExit("ERROR: REMOTE_MEDIA_TRUNCATED")
            target = os.path.join(public_dir, digest.hexdigest()[:24] + suffix)
            os.replace(temporary, target)
            return target
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def _stage_local_media(shotlist, stage_dir=None):
    """Copy local shot media into Remotion's public directory.

    Remotion's staticFile() only serves files below ``remotion_engine/public``.
    Shotlists are normally authored from the project root, so passing a valid
    project-relative path directly produces a late browser 404 during render.
    Stage files once per content hash and rewrite only local media references;
    URLs, data URLs and CSS backgrounds remain untouched.
    """
    public_root = os.path.join(ENGINE, "public")
    os.makedirs(public_root, exist_ok=True)
    stage_dir = stage_dir or tempfile.mkdtemp(prefix="render-", dir=public_root)
    public_dir = os.path.abspath(stage_dir)
    if os.path.commonpath([public_root, public_dir]) != os.path.abspath(public_root):
        raise SystemExit("ERROR: REMOTION_STAGE_ESCAPE")
    public_rel = os.path.relpath(public_dir, public_root).replace(os.sep, "/")
    staged = {}
    media_objects = list(shotlist.get("shots") or [])
    media_objects.append(shotlist)
    for shot in media_objects:
        for field in ("image", "video", "videoPath", "pipVideoPath", "audioPath", "source"):
            value = shot.get(field)
            if not isinstance(value, str) or value.startswith("data:"):
                continue
            if value.startswith(("http://", "https://")):
                source = _stage_remote_media(value, public_dir)
                shot[field] = public_rel + "/" + os.path.basename(source)
                continue
            local_value = value[len("file://"):] if value.startswith("file://") else value
            candidates = [local_value, os.path.join(ROOT, local_value),
                          os.path.join(ENGINE, "public", local_value)]
            source = next((p for p in candidates if os.path.isfile(p)), None)
            if not source:
                raise SystemExit("ERROR: Remotion 素材不存在: %s" % value)
            digest = hashlib.sha256()
            with open(source, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            name = digest.hexdigest()[:24] + os.path.splitext(source)[1].lower()
            target = os.path.join(public_dir, name)
            if not os.path.isfile(target):
                os.makedirs(public_dir, exist_ok=True)
                shutil.copy2(source, target)
            staged[value] = public_rel + "/" + name
            shot[field] = staged[value]
        assets = shot.get("assets")
        if isinstance(assets, list):
            staged_assets = []
            for value in assets:
                if not isinstance(value, str) or value.startswith("@"):
                    staged_assets.append(value)
                    continue
                local_value = value[len("file://"):] if value.startswith("file://") else value
                source = next((p for p in (local_value, os.path.join(ROOT, local_value),
                                           os.path.join(ENGINE, "public", local_value)) if os.path.isfile(p)), None)
                if not source:
                    raise SystemExit("ERROR: Remotion Shotcraft 素材不存在: %s" % value)
                digest = hashlib.sha256(open(source, "rb").read()).hexdigest()
                name = digest[:24] + os.path.splitext(source)[1].lower()
                target = os.path.join(public_dir, name)
                if not os.path.isfile(target):
                    shutil.copy2(source, target)
                staged_assets.append(public_rel + "/" + name)
            shot["assets"] = staged_assets
    return shotlist


def _media_output_ok(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
         "-of", "default=nw=1:nk=1", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    return result.returncode == 0 and "video" in result.stdout.splitlines()


def ensure_deps(verbose=True):
    """确保 Remotion 工程依赖已装（node_modules 存在）。首次会 npm install。"""
    _use_bundled_node()
    if not _npx():
        raise SystemExit("ERROR: 未找到 node/npx。请先运行 deploy 脚本自动安装 Node。")
    nm = os.path.join(ENGINE, "node_modules", ".bin", "remotion")
    # A packaged node_modules tree is platform-specific. Never try to execute
    # the bundled macOS ARM binaries on Windows/Linux; npm ci will replace it.
    bundled_ok = (platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"))
    if os.path.exists(nm) and bundled_ok:
        # Prefer a verified local archive/executable. Calling `remotion browser
        # ensure` unconditionally causes a network download even when the
        # offline package is already installed.
        fixed, fix_msg = fix_chrome_headless_shell()
        local_chrome = find_chrome_executable()
        if local_chrome:
            if verbose:
                print("[chrome] 使用本地 Headless Shell: %s" % local_chrome, flush=True)
            return
        if verbose:
            print("检查 Remotion Chrome Headless Shell（本地包缺失，准备联网兜底）...", flush=True)
        subprocess.run([_remotion_bin(), "browser", "ensure"], cwd=ENGINE, check=True)
        return
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("ERROR: 未找到 npm。")
    if verbose and os.path.exists(nm) and not bundled_ok:
        print("包内 Node 依赖属于 macOS ARM，当前平台为 %s/%s，改用 npm ci 安装平台依赖..." %
              (platform.system(), platform.machine()), flush=True)
    if verbose:
        print("首次准备 Remotion 依赖（npm install，可能几分钟）...", flush=True)
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ENGINE, check=True)
    fixed, fix_msg = fix_chrome_headless_shell()
    if not find_chrome_executable():
        subprocess.run([_remotion_bin(), "browser", "ensure"], cwd=ENGINE, check=True)


def _remotion_bin():
    return os.path.join(ENGINE, "node_modules", ".bin", "remotion")


def render(shotlist_path, out_path, quality="high", composition=COMPOSITION):
    ensure_deps()
    okff, msgff = ensure_ffmpeg_on_path()
    if not okff:
        raise SystemExit("ERROR: " + msgff)
    print("[ffmpeg] " + msgff, flush=True)

    with open(shotlist_path, "r", encoding="utf-8") as f:
        shotlist = json.load(f)

    public_root = os.path.join(ENGINE, "public")
    os.makedirs(public_root, exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix="render-", dir=public_root)
    shotlist = _stage_local_media(copy.deepcopy(shotlist), stage_dir=stage_dir)

    # ── 兼容 guide_scaffold compile_shots 输出的 resolution:[w,h] 格式 ──
    # Root.tsx 的 calculateMetadata 读顶层 width/height，而非 resolution 数组。
    # 若只有 resolution 数组则拆开，保证横屏/自定义尺寸能正确渲染，不回落默认 1080×1920。
    if "resolution" in shotlist and ("width" not in shotlist or "height" not in shotlist):
        res = shotlist["resolution"]
        if isinstance(res, (list, tuple)) and len(res) == 2:
            shotlist.setdefault("width", int(res[0]))
            shotlist.setdefault("height", int(res[1]))

    # props 必须是文件（Remotion --props 接受 json 文件路径）
    props_fd, props_path = tempfile.mkstemp(
        prefix="_props_", suffix=".json", dir=ENGINE)
    with os.fdopen(props_fd, "w", encoding="utf-8") as f:
        json.dump(shotlist, f, ensure_ascii=False)

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd, render_path = tempfile.mkstemp(prefix=".%s." % os.path.basename(out_path),
                                       suffix=os.path.splitext(out_path)[1] or ".mp4",
                                       dir=os.path.dirname(out_path) or ".")
    os.close(fd)
    os.unlink(render_path)

    def _run(browser_exe=None):
        cmd = [_remotion_bin(), "render", "src/index.ts", composition, render_path,
               "--props", props_path, "--log=error"]
        if quality == "high":
            cmd += ["--jpeg-quality", "100"]
        if browser_exe:
            cmd += ["--browser-executable", browser_exe]
        return subprocess.run(cmd, cwd=ENGINE)

    # 优先复用已解压的 Chrome，跳过每次 render 的重复下载/校验（网络不稳时关键）。
    chrome = find_chrome_executable()
    if chrome:
        print("[chrome] 复用已解压 Chrome: %s" % chrome, flush=True)
    def _produced_ok():
        # 出片校验：文件必须真实存在且非空。Remotion 偶发 returncode=0 却没落盘
        # （Chrome 解压不完整/浏览器崩溃等），只看返回码会误报成功，必须查文件。
        return _media_output_ok(render_path)

    try:
        p = _run(browser_exe=chrome)
        if p.returncode != 0 or not _produced_ok():
            why = "返回码 %s" % p.returncode if p.returncode != 0 else "未产出成片文件"
            print("首次渲染失败（%s），尝试修复 Chrome Headless Shell 后重试..." % why, flush=True)
            fixed, msg = fix_chrome_headless_shell()
            print("[chrome] " + msg, flush=True)
            p = _run(browser_exe=find_chrome_executable())
        if p.returncode != 0:
            raise SystemExit("ERROR: Remotion 渲染失败（见上方日志）")
        if not _produced_ok():
            raise SystemExit(
                "ERROR: Remotion 返回成功但未生成可探测成片：%s（可能 Chrome 未就绪或渲染中断）" % out_path)
        os.replace(render_path, out_path)
    finally:
        try:
            os.remove(props_path)
        except BaseException:
            pass
        try:
            os.unlink(render_path)
        except BaseException:
            pass
        try:
            shutil.rmtree(stage_dir, ignore_errors=True)
        except BaseException:
            pass
    print(json.dumps({"ok": True, "out": out_path}, ensure_ascii=False))
    return out_path


def render_shotcraft(spec_path, out_path, quality="high"):
    """Render a validated Shotcraft spec through the existing deterministic Shots composition.

    Shotcraft cards are deliberately compiled to clean media-only shots here;
    exact typography remains owned by HyperFrames so generated and deterministic
    layers never compete for text ownership.
    """
    sys.path.insert(0, HERE)
    import shotcraft_qc
    with open(spec_path, encoding="utf-8") as handle:
        spec = json.load(handle)
    report = shotcraft_qc.check(spec)
    if not report["passed"]:
        raise SystemExit("ERROR: SHOTCRAFT_QC_FAILED: %s" % ", ".join(report["errors"]))
    shotlist = copy.deepcopy(spec)
    fd, temporary = tempfile.mkstemp(prefix=".shotcraft-", suffix=".json", dir=os.path.dirname(os.path.abspath(out_path)) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(shotlist, handle, ensure_ascii=False)
        return render(temporary, out_path, quality, composition=SHOTCRAFT_COMPOSITION)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def doctor():
    print("=== Remotion 引擎体检 ===")
    print("node:", shutil.which("node") or "缺失")
    print("npx :", _npx() or "缺失")
    okff, msgff = ensure_ffmpeg_on_path()
    print("ffmpeg+ffprobe:", ("OK — " + msgff) if okff else ("缺失 — " + msgff))
    nm = os.path.exists(os.path.join(ENGINE, "node_modules", ".bin", "remotion"))
    print("Remotion 依赖:", "已装" if nm else "未装（首次 render 自动 npm install）")
    if nm:
        fixed, msg = fix_chrome_headless_shell()
        local_chrome = find_chrome_executable()
        if local_chrome:
            print("Chrome Headless Shell: 使用本地包 —", local_chrome)
        else:
            print("Chrome Headless Shell:", msg)
            try:
                subprocess.run([_remotion_bin(), "browser", "ensure"], cwd=ENGINE, check=True)
            except subprocess.CalledProcessError as exc:
                print("Chrome 预热失败，退出码:", exc.returncode)
    ok = bool(shutil.which("node")) and okff
    print("结论:", "READY" if ok else "需补依赖（跑 deploy）")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Remotion 运镜/编排引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="按 shotlist 渲染运镜背景 MP4")
    r.add_argument("--shotlist", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--quality", default="high", choices=["draft", "standard", "high"])
    rc = sub.add_parser("render-content",
                        help="按内容动效 spec 渲染文档/PPT 型动效 MP4（remotion-com-skills 组件库）")
    rc.add_argument("--spec", required=True, help="内容动效 scene spec JSON（content_scaffold.py 生成）")
    rc.add_argument("--out", required=True)
    rc.add_argument("--quality", default="high", choices=["draft", "standard", "high"])
    rk = sub.add_parser("render-kinetic", help="渲染横版口播增强模板（字幕/章节/卡片/PIP）")
    rk.add_argument("--spec", required=True, help="HorizontalKinetic props JSON")
    rk.add_argument("--out", required=True)
    rk.add_argument("--quality", default="high", choices=["draft", "standard", "high"])
    rs = sub.add_parser("render-shotcraft", help="渲染已校验的 Shotcraft 确定性包装 spec")
    rs.add_argument("--spec", required=True)
    rs.add_argument("--out", required=True)
    rs.add_argument("--quality", default="high", choices=["draft", "standard", "high"])
    ni = sub.add_parser("normalize-input", help="将口播源统一为1920x1080 H.264/AAC")
    ni.add_argument("--input", required=True)
    ni.add_argument("--out", required=True)
    sub.add_parser("doctor", help="检测并修复依赖/Chrome")
    a = ap.parse_args()
    if a.cmd == "render":
        render(a.shotlist, a.out, a.quality, composition=COMPOSITION)
    elif a.cmd == "render-content":
        render(a.spec, a.out, a.quality, composition=CONTENT_COMPOSITION)
    elif a.cmd == "render-kinetic":
        render(a.spec, a.out, a.quality, composition=KINETIC_COMPOSITION)
    elif a.cmd == "render-shotcraft":
        render_shotcraft(a.spec, a.out, a.quality)
    elif a.cmd == "normalize-input":
        normalize_input(a.input, a.out)
    elif a.cmd == "doctor":
        sys.exit(doctor())


if __name__ == "__main__":
    main()
