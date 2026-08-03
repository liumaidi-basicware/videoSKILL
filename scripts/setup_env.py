#!/usr/bin/env python3
"""Environment initializer for a zero-plugin client machine.

Self-healing: detects Python, checks each dependency, installs what's missing
via pip (no admin needed, --user fallback), then verifies everything imports
and the ffmpeg binary resolves. Prints a clear READY / NOT READY report.

CLI:
  python3 setup_env.py check     # report only, no install
  python3 setup_env.py full-check # report all local engines; nonzero if any is missing
  python3 setup_env.py install   # install missing + verify (default)
"""
import sys
import subprocess
import os
import shutil
import warnings

# urllib3 v2 emits a noisy LibreSSL warning on the macOS system Python. The
# project uses BasicRouter through its own retry/HTTPS policy; expose this as a
# capability check rather than polluting the customer setup transcript.
warnings.filterwarnings(
    "ignore", message="urllib3 v2 only supports OpenSSL.*", module="urllib3")

# (import_name, pip_spec, purpose)
DEPS = [
    ("pptx", "python-pptx>=0.6.21", "解析 .pptx"),
    ("docx", "python-docx>=0.8.11", "解析/生成 .docx"),
    ("fitz", "pymupdf>=1.23.0", "解析 .pdf"),
    ("openpyxl", "openpyxl>=3.0.0", "解析 .xlsx"),
    ("imageio_ffmpeg", "imageio-ffmpeg>=0.4.9", "视频拼接/Logo水印(自带ffmpeg)"),
    ("static_ffmpeg", "static-ffmpeg>=2.5", "HyperFrames/Remotion的ffmpeg+ffprobe"),
]
# macOS-only：OCR 兜底检测依赖（Vision 框架）。非 macOS 平台静默跳过检测/安装。
MACOS_DEPS = [
    ("Vision", "pyobjc-framework-Vision>=10.0", "出片后 OCR 字幕兜底检测(macOS Vision)"),
    ("Quartz", "pyobjc-framework-Quartz>=10.0", "Vision 依赖的 Quartz 绑定"),
]


def bundled_ffmpeg_paths():
    """Return project-bundled ffmpeg paths, if the offline package contains them."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    system = sys.platform
    machine = __import__("platform").machine().lower()
    if system == "darwin":
        tag = "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64"
    elif system == "win32":
        tag = "win-arm64" if machine in ("arm64", "aarch64") else "win-x64"
    else:
        tag = "linux-arm64" if machine in ("arm64", "aarch64") else "linux-x64"
    directory = os.path.join(root, "offline-assets", "ffmpeg", tag)
    ffmpeg = os.path.join(directory, "ffmpeg" + (".exe" if system == "win32" else ""))
    ffprobe = os.path.join(directory, "ffprobe" + (".exe" if system == "win32" else ""))
    return (ffmpeg, ffprobe) if os.path.isfile(ffmpeg) and os.path.isfile(ffprobe) else (None, None)


def bundled_node_bin():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "offline-assets", "node-runtime", "bin")
    return path if os.path.isfile(os.path.join(path, "node")) else None
# 注：抠像/换背景/人景融合一律走外部模型（BasicRouter），本地不装 rembg/onnxruntime，保持轻交付。


def _importable(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _pip_install(specs):
    """Try normal install, then --user on failure."""
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    r = subprocess.run(base + specs)
    if r.returncode != 0:
        print("  normal install failed, retrying with --user …")
        r = subprocess.run(base + ["--user"] + specs)
    return r.returncode == 0


def check(verbose=True, include_optional=True):
    """检测核心依赖。返回 missing（仅核心项，影响 READY 退出码）。

    OCR 依赖（MACOS_DEPS）是**可选**的——ocr_check.py 缺 Vision 时静默降级，
    不阻塞出片。所以即使装不上（部分 macOS 编译 pyobjc 失败）也不算 NOT READY，
    只提示可选功能不可用，避免总入口反复触发自举死循环。
    """
    missing = []
    for mod, spec, purpose in DEPS:
        ok = _importable(mod)
        if verbose:
            print("  [%s] %-16s %s" % ("OK" if ok else "--", mod, purpose))
        if not ok:
            missing.append((mod, spec, purpose))
    # 可选：OCR 兜底（仅 macOS，缺失不影响 READY）
    if include_optional and sys.platform == "darwin":
        for mod, spec, purpose in MACOS_DEPS:
            ok = _importable(mod)
            if verbose:
                print("  [%s] %-16s %s%s" % ("OK" if ok else "~~", mod, purpose,
                                             "" if ok else "  (可选,缺失则OCR跳过)"))
    return missing


def missing_optional():
    """返回缺失的可选依赖（OCR）。仅用于提示，不影响 READY。"""
    if sys.platform != "darwin":
        return []
    return [(m, s, p) for m, s, p in MACOS_DEPS if not _importable(m)]


def verify_ffmpeg():
    bundled = bundled_ffmpeg_paths()
    if all(bundled):
        return True
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        import os
        return bool(exe) and os.path.exists(exe)
    except Exception:
        return False


def verify_hyperframes_engine(verbose=True):
    """HyperFrames 主引擎就绪度：node/npx + ffmpeg&ffprobe（static-ffmpeg 静态二进制）。

    返回 (node_ok, ff_ok)。任一不满足时打印如何补齐；引擎缺失不阻塞基础出片，
    但动态文字会回退 libass 兜底，且必须向交付流程标注体验降级。
    """
    import shutil
    node_ok = bool(shutil.which("node") and shutil.which("npx")) or bool(bundled_node_bin())
    ff_ok = False
    try:
        bundled = bundled_ffmpeg_paths()
        if all(bundled):
            ff_ok = True
            return (node_ok, ff_ok)
        from static_ffmpeg import run
        ff, fp = run.get_or_fetch_platform_executables_else_raise()
        import os
        ff_ok = os.path.isfile(ff) and os.path.isfile(fp)
    except Exception:
        ff_ok = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    if verbose:
        print("  [%s] Node/npx           (HyperFrames 字幕/动效引擎)" % ("OK" if node_ok else "--"))
        print("  [%s] ffmpeg+ffprobe     (HyperFrames 渲染硬依赖)" % ("OK" if ff_ok else "--"))
        if not node_ok:
            print("     → 缺 Node.js：动态文字将回退到 libass 兜底；已自动选择系统 CJK 字体，仍建议装 Node 后启用 HyperFrames 主引擎。")
    return node_ok, ff_ok


def verify_video_engines(verbose=True):
    """视频编排就绪度：Remotion 运镜（本地无模型）+ 外部融合能力（BasicRouter）。

    返回 (remotion_ok, fuse_ok)。抠像/换背景/人景融合全部走外部模型——本地不跑，
    所以这里只确认：Remotion 运镜引擎在，以及外部融合的 API Key 已配（就绪即可用
    matte.py compose 路线C / video_engine --type 4/5 路线A）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    # Remotion：node + 工程依赖已装（纯排版渲染，无模型）
    remotion_dep = os.path.exists(os.path.join(root, "remotion_engine", "node_modules", ".bin", "remotion"))
    remotion_ok = bool(shutil.which("node")) and remotion_dep
    # 外部人景融合：只需 API Key（能力在 BasicRouter 云端，本地零依赖）
    fuse_ok = False
    try:
        sys.path.insert(0, here)
        import key_setup  # noqa
        fuse_ok = bool(key_setup.load_key())
    except Exception:
        fuse_ok = False
    if verbose:
        print("  [%s] Remotion 运镜引擎   (数字人+PPT内容页/推拉摇移，本地无模型)" % ("OK" if remotion_ok else "--"))
        if not remotion_dep and shutil.which("node"):
            print("     → Remotion 依赖未装：首次出片会自动 npm install，或重跑 deploy 预热。")
        print("  [%s] 外部人景融合能力   (抠像/换背景走 BasicRouter，需 API Key)" % ("OK" if fuse_ok else "--"))
        if not fuse_ok:
            print("     → 未配 API Key：配好后即可用路线A(参考图)/路线C(img2img)做人景融合，本地不跑模型。")
    return remotion_ok, fuse_ok


def verify_remotion_chrome(verbose=True):
    """Check whether Remotion's Chrome Headless Shell is actually executable."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, "remotion_engine", "node_modules", ".remotion",
                        "chrome-headless-shell")
    found = None
    if os.path.isdir(base):
        for current, _dirs, files in os.walk(base):
            for filename in ("chrome-headless-shell", "chrome-headless-shell.exe"):
                if filename in files:
                    candidate = os.path.join(current, filename)
                    if sys.platform == "win32" or os.access(candidate, os.X_OK):
                        found = candidate
                        break
            if found:
                break
    ready = bool(found)
    if verbose:
        print("  [%s] Remotion Chrome Headless Shell" % ("OK" if ready else "--"))
        if not ready:
            print("     → 首次渲染前会由 deploy/remotion doctor 下载并修复 Chrome。")
    return ready


def capability_status():
    """Return machine-readable capability state for routing decisions."""
    missing = check(verbose=False, include_optional=False)
    node_ok, hf_ff = verify_hyperframes_engine(verbose=False)
    remotion_ok, key_present = verify_video_engines(verbose=False)
    chrome_ok = verify_remotion_chrome(verbose=False)
    engine_imports = True
    for module in ("video_engine", "hf_engine", "remotion_engine"):
        try:
            __import__(module)
        except Exception:
            engine_imports = False
    ocr_ok = (sys.platform == "darwin" and
              all(_importable(mod) for mod, _, _ in MACOS_DEPS))
    status = {
        "python_core": not missing,
        "ffmpeg": verify_ffmpeg(),
        "hyperframes": bool(node_ok and hf_ff),
        "remotion": bool(remotion_ok),
        "engine_imports": engine_imports,
        "remotion_chrome": bool(chrome_ok),
        # OCR is optional off macOS because Vision is macOS-only and ocr_check
        # intentionally degrades to a no-op on other platforms.
        "ocr": bool(ocr_ok) if sys.platform == "darwin" else True,
        "basicrouter_key_present": bool(key_present),
    }
    status["dependencies_ready"] = all(status[name] for name in
                                        ("python_core", "ffmpeg", "hyperframes", "remotion", "engine_imports", "remotion_chrome", "ocr"))
    status["formal_generation_ready"] = bool(status["dependencies_ready"] and key_present)
    return status


def _print_capabilities(caps):
    print("\n能力状态:")
    for name, ready in caps.items():
        print("  [%s] %s" % ("OK" if ready else "--", name))


def main(argv):
    mode = argv[0] if argv else "install"
    print("Python: %s" % sys.version.split()[0])
    if sys.version_info < (3, 7):
        print("WARNING: Python 3.7+ recommended.")

    print("依赖检测:")
    missing = check()

    if mode in ("check", "full-check"):
        print("HyperFrames 引擎:")
        verify_hyperframes_engine()
        print("数字人视频引擎:")
        verify_video_engines()
        caps = capability_status()
        _print_capabilities(caps)
        print("\n结果: %s" % ("核心环境 READY" if not missing else
                               "缺 %d 个核心依赖，运行 `python3 setup_env.py install` 安装" % len(missing)))
        if mode == "full-check":
            return 0 if caps["dependencies_ready"] else 1
        return 0 if not missing else 1

    # install mode
    if missing:
        print("\n安装缺失依赖: %s" % ", ".join(m[1] for m in missing))
        if not _pip_install([m[1] for m in missing]):
            print("\nNOT READY: pip 安装失败。请检查网络或手动 `pip3 install -r requirements.txt`。")
            return 1
        print("安装完成，重新校验…")
        missing = check(verbose=False)

    # 可选依赖（OCR）best-effort 安装：失败不阻塞 READY
    opt = missing_optional()
    if opt:
        print("\n安装可选依赖(OCR字幕检测): %s" % ", ".join(m[1] for m in opt))
        if not _pip_install([m[1] for m in opt]):
            print("  可选依赖安装失败(不影响出片)：OCR 字幕检测将自动跳过。")

    ff = verify_ffmpeg()
    print("  [%s] ffmpeg 二进制可用" % ("OK" if ff else "--"))

    print("HyperFrames 引擎:")
    node_ok, hf_ff = verify_hyperframes_engine()

    print("数字人视频引擎:")
    remotion_ok, matte_ok = verify_video_engines()

    ready = (not missing) and ff
    print("\n结果: %s" % ("环境就绪 READY ✅" if ready else "NOT READY ❌"))
    if ready and not (node_ok and hf_ff):
        print("提示: 核心出片就绪；但 HyperFrames 主引擎未完全就绪，动态文字/字幕将走 libass 兜底。")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
