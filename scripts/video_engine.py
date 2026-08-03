#!/usr/bin/env python3
"""Video engine: submit createVideo, poll, download to output/.

音画一体（NOT two steps）: kling-v3-omni-video 一次调用即产出「带配音+对口型」的
成片——`--text` 就是要念的台词脚本，模型自动配音、对口型、生成画面。没有"先出
视频再单独配音"的第二步，也不要再叫 TTS 后期。

异步=并行工作流: createVideo 返回 taskId 后可继续提交下一段，多段应「先全部提交，
再统一轮询」（见 render_batch / --batch），墙钟时间≈单段，不是 N× 串行。

Wraps br_client so scene skills just call one command. Reads the API key via
key_setup (agent session cache selected by BASICROUTER_SESSION_ID).

CLI examples:
  # 单段：文生（音画一体，text 即台词）
  python3 video_engine.py --text "一只狗在奔跑" --type 1 --duration 4 --ratio 16:9 --out output/demo.mp4
  # 单段：数字人参考图口播（text=粵語台词，模型自动配音对口型）
  python3 video_engine.py --text "主播粵語口播……" --type 4 \
      --urls https://.../portrait.png --ratio 9:16 --duration 8 --out output/broadcast.mp4
  # 多段并行（访谈/多镜头）：把段落写进 JSON，一次并行出片
   python3 video_engine.py --batch segments.json
   # segments.json = [{"text":"...","video_type":4,"urls":["..."],"duration":8,"ratio":"9:16","out_path":"output/seg1.mp4"}, ...]
   # script_splitter.split() 的完整输出也可直接传入（自动提取其中的 segments 数组）
"""
import os
import sys
import json
import argparse
import tempfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import br_client            # noqa: E402
import key_setup            # noqa: E402
import seedance_prompt      # noqa: E402
import ux                    # noqa: E402
import artifact_contract     # noqa: E402
import generation_ledger     # noqa: E402
import take_review           # noqa: E402
import media_qc              # noqa: E402
import run_manifest as _rm   # noqa: E402 — hoisted from 12 inline imports (v3 cleanup)
import cost_ledger          # noqa: E402 — v3 cost tracking in _persist_task


def _manifest_handoff_matches(manifest, segments):
    recorded = (manifest.get("handoffs") or {}).get("video") or {}
    expected = {seg.get("id"): seg.get("video_handoff_fingerprint") for seg in segments}
    if not recorded or not recorded.get("segments"):
        raise ValueError("VIDEO_HANDOFF_REQUIRED: manifest 未记录当前 segments 的 video handoff")
    if recorded.get("segments") != expected:
        raise ValueError("VIDEO_HANDOFF_MISMATCH: manifest recorded video handoff 与当前 segments 不一致")


def _reference_is_trusted(client, value, manifest=None):
    import asset_prep as _ap
    if _ap.is_confirmed(client, value):
        return True
    if not manifest or not isinstance(value, str) or not os.path.isfile(value):
        return False
    path = os.path.abspath(value)
    for record in (manifest.get("generation", {}).get("storyboard", {}).get("artifacts") or []):
        if os.path.abspath(record.get("path", "")) == path and record.get("exists"):
            return _rm.approval_is_current(manifest, "storyboard")
    try:
        import product_library as _pl
        product_root = os.path.abspath(os.path.join(HERE, os.pardir, "assets", client, "product"))
        if os.path.commonpath((path, product_root)) == product_root:
            relative = os.path.relpath(path, product_root).split(os.sep)
            if len(relative) >= 2:
                resolved = _pl.resolve(client, relative[0])
                if (resolved.get("product_board_confirmed")
                        and os.path.abspath(resolved.get("product_board", "")) == path):
                    return True
        for sku in {seg.get("product_sku") for seg in manifest.get("segments", [])
                    if isinstance(seg, dict) and seg.get("product_sku")}:
            resolved = _pl.resolve(client, sku)
            if resolved.get("product_board") and os.path.abspath(resolved["product_board"]) == path:
                return bool(resolved.get("product_board_confirmed"))
    except Exception:
        pass
    return False


def _validate_references(segments, client, manifest, draft=False):
    if draft:
        return
    for seg in segments:
        refs = list(seg.get("urls") or [])
        product_refs = set()
        if seg.get("product_sku"):
            try:
                import product_library as _pl
                resolved = _pl.resolve(client, seg["product_sku"])
                product_refs.update(os.path.abspath(p) for p in
                                    ([resolved.get("hero")] + list(resolved.get("refs") or [])) if p)
                if resolved.get("product_board_confirmed"):
                    product_refs.add(os.path.abspath(resolved["product_board"]))
            except Exception as error:
                raise ValueError("PRODUCT_SKU_RESOLVE_FAILED: %s (%s)" %
                                 (seg["product_sku"], error))
        bad = [ref for ref in refs if not _reference_is_trusted(client, ref, manifest)
               and (not isinstance(ref, str) or not os.path.isfile(ref)
                    or os.path.abspath(ref) not in product_refs)]
        if bad:
            raise ValueError("UNTRUSTED_VIDEO_REFERENCE: %s" % ", ".join(map(str, bad)))


def _validate_reference_handoff(segments):
    """Reject segments whose confirmed reference contract was reduced."""
    for seg in segments:
        required = set(seg.get("required_reference_types") or [])
        if not required:
            continue
        actual = {ref.get("type") for ref in seg.get("references") or []
                  if isinstance(ref, dict) and ref.get("type")}
        missing = sorted(required - actual)
        dropped = [item for item in seg.get("dropped_references") or []
                   if item.get("type") in required]
        if missing or dropped:
            detail = ", ".join(missing or [item.get("type", "unknown") for item in dropped])
            raise ValueError(
                "REFERENCE_HANDOFF_INCOMPLETE: segment %s 缺少已确认参考图类型 [%s]。"
                "视频请求已阻断；不能把产品使用板、人物板、产品板或故事板静默丢弃。"
                % (seg.get("id") or "unknown", detail))


def _validate_model_reference_capacity(model, reference_count, *, formal):
    """Fail before submission when the selected model cannot receive all refs."""
    if not formal:
        return
    record = (_model_catalog("video").get("records", {}).get(model) or {})
    max_count = record.get("image_count")
    if max_count is not None and reference_count > max_count:
        raise ValueError(
            "REFERENCE_COUNT_UNSUPPORTED: model %s supports at most %s reference image(s), "
            "but this confirmed handoff requires %s. No reference may be dropped or merged "
            "automatically; choose a model/endpoint whose declared capacity satisfies the "
            "confirmed handoff." % (model, max_count, reference_count))


def _expand_product_refs(segment, client):
    sku = segment.get("product_sku")
    if not sku or segment.get("urls"):
        return segment
    try:
        import product_library as _pl
        resolved = _pl.resolve(segment.get("client") or client or "", sku)
    except (Exception, SystemExit) as error:
        raise ValueError("PRODUCT_SKU_RESOLVE_FAILED: %s (%s)" % (sku, error))
    expanded = ([resolved.get("hero")] if resolved.get("hero") else [])
    expanded.extend(resolved.get("refs") or [])
    if resolved.get("product_board_confirmed") and resolved.get("product_board"):
        expanded.append(resolved["product_board"])
    expanded = list(dict.fromkeys(item for item in expanded if item))[:5]
    if not expanded:
        raise ValueError("PRODUCT_SKU_NO_REFERENCES: %s" % sku)
    copy = dict(segment, urls=expanded, video_type=5)
    return copy


def _atomic_json_write(path, value):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _media_qc_guard(local_path, segment, *, draft, manifest=None,
                    manifest_path=None, task=None):
    """Run content-bound QC after download and persist it before success."""
    audio_contract = segment.get("audio_contract") or {}
    report_path = os.path.abspath(local_path) + ".qc.json"
    report = media_qc.check(
        local_path, profile="draft" if draft else "formal",
        expected_duration=segment.get("duration"), expected_ratio=segment.get("ratio"),
        audio_required=audio_contract.get("track") == "required",
        report_path=report_path)
    report["audio_contract"] = audio_contract
    report["audio_contract_fingerprint"] = artifact_contract.sha256_json(audio_contract)
    report["semantic_audio_review_required"] = [
        key for key in ("voice", "language", "bgm", "sfx", "lip_sync")
        if audio_contract.get(key) not in (None, "", False)]
    report["report_path"] = report_path
    if manifest is not None:
        segment_id = str(segment.get("id") or "unknown")
        manifest.setdefault("media_qc", {}).setdefault("video", {})[segment_id] = report
        if task is not None:
            _persist_task(manifest, manifest_path, None, task,
                          "succeeded",
                          video_url=task.get("video_url"), media_qc=report,
                          media_qc_passed=bool(report.get("passed")),
                          actual_duration=(report.get("media") or {}).get("actual_duration"))
        elif manifest_path:
            _rm.save_manifest(manifest, manifest_path)
    return report


def _persist_task(manifest, manifest_path, ledger_path, task, status, **fields):
    # Signed CDN URLs are bearer capabilities. Persist only their digest; all
    # recovery paths query the durable taskId for a fresh URL.
    persisted_video_url = fields.pop("video_url", None)
    if persisted_video_url:
        fields["video_url_sha256"] = hashlib.sha256(
            str(persisted_video_url).encode("utf-8")).hexdigest()
    attempt = int(fields.pop("attempt", None) or task.get("attempt") or 1)
    # v3: auto-compute cost estimate for succeeded tasks (fail-safe, never blocks)
    if status == "succeeded" and "cost_estimate" not in fields:
        try:
            seg = task.get("segment") or {}
            seg_model = task.get("model") or DEFAULT_MODEL
            dur = seg.get("duration") or seg.get("duration_sec")
            fields["cost_estimate"] = cost_ledger.cost_estimate_for_task(
                seg_model, dur, "video", attempt)
        except Exception:
            pass  # cost estimation failure must never block task persistence
    if ledger_path:
        generation_ledger.append_event(ledger_path, "task_%s" % status, stage="video",
                                       unit_id=task.get("segment", {}).get("id"),
                                       task_id=task.get("task_id"), attempt=attempt, **fields)
    if manifest is not None:
        for name in ("request_id", "dependency_fingerprint", "generation_dependency",
                     "supersedes"):
            if task.get(name) is not None and name not in fields:
                fields[name] = task[name]
        _rm.upsert_task(manifest, dict(stage="video", unit_id=task.get("segment", {}).get("id"),
                                      handoff_fingerprint=task.get("handoff_fingerprint"),
                                       task_id=task.get("task_id"), model=task.get("model"),
                                      attempt=attempt, status=status, **fields))
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)


def _record_task_resume(manifest, manifest_path, ledger_path, task):
    if ledger_path:
        generation_ledger.append_event(
            ledger_path, "task_resumed", stage="video",
            unit_id=task.get("segment", {}).get("id"), task_id=task.get("task_id"),
            handoff_fingerprint=task.get("handoff_fingerprint"))
    if manifest is not None:
        _rm.upsert_task(manifest, {"stage": "video",
                                   "unit_id": task.get("segment", {}).get("id"),
                                   "handoff_fingerprint": task.get("handoff_fingerprint"),
                                    "task_id": task.get("task_id"), "model": task.get("model"),
                                    "attempt": task.get("attempt", 1),
                                    "status": "running"})
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)

DEFAULT_MODEL = "seedance-2.0"


# ── OCR 兜底检测（macOS Vision）─────────────────────────────────────────────────
# 出片后自动抽帧 OCR，检出画面文字则打印 [OCR_WARNING] subtitle_detected，
# 让 agent 感知并决定是否重出。非 macOS / 缺依赖时静默跳过，不阻塞主流程。

def _ocr_guard(local_path, log):
    """在下载完成后立即运行 OCR 兜底检测。
    检出字幕 → 打印 [OCR_WARNING] 供 agent 解析并决策（重新生成/人工确认）。
    不可用（非 macOS / 缺包）→ 静默跳过，不影响正常出片。

    返回结构化结果（写进 batch/chained 的 results，铁律#9：自动化链路也能看到警告，
    不只在 stdout 日志里——否则 assemble 会静默把带残留字幕的段拼进成片）：
      {"available": bool, "subtitle_detected": bool, "texts": [str...], "frames_checked": int}
    或 None（不可用/异常时）。
    """
    try:
        import ocr_check
    except ImportError:
        return None  # ocr_check.py 不在同目录，静默跳过
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


# ── 模型兜底降级表 ─────────────────────────────────────────────────────────────
# 实时可用性通过 /employee/models 校验；不可用时按顺序降级。模型名对齐真实目录。
# 默认首选 seedance-2.0（用户指定：出片更快/更省，覆盖 videoType[1,2,3,5]）→
#   kling-v3-omni-video（唯一支持全 videoType[1,2,3,4,5]，参考图4 只能它）→ wan2.7-i2v 通用备选。
VIDEO_MODEL_FALLBACK = [
    "seedance-2.0",
    "kling-v3-omni-video",
    "wan2.7-i2v",
]

# 各模型支持的 videoType 能力集（仅离线/查询失败时的兜底表；实际优先信任网关
# /employee/models 每个模型自带的权威 `allowVideoType` 字段——见 _model_allow_types）：
#   1=t2v 2=i2v(首帧) 3=首尾帧 4=单张参考图(数字人身份锚定) 5=多图(多主体/人景同框)
# ⚠️ 实测网关 allowVideoType（2026-07）：
#   seedance-2.0 / -fast / -white = [1,2,3,5]  → 支持多图 type5(多主体/人景同框)，但【不支持】单图参考 type4
#   kling-v3-omni-video          = [1,2,3,4,5] → 唯一支持单图参考 type4(数字人单张锚定)
#   wan2.7-i2v                   = [1,2]
# 所以：人景同框/多主体走 type5 时 seedance 自己就能做（更快省，不必回落 kling）；
#       只有单张参考图身份锚定 type4 才必须回落 kling。别把 4 和 5 混为一谈。
VIDEO_MODEL_CAPS = {
    "seedance-2.0": {1, 2, 3, 5},
    "kling-v3-omni-video": {1, 2, 3, 4, 5},
    "wan2.7-i2v": {1, 2},
}

VIDEO_MODEL_INTEGRATED_AUDIO = {
    "seedance-2.0": True,
    "kling-v3-omni-video": True,
    "kling-v3-omni": True,
    "wan2.7-i2v": False,
}

# ⚠️ 实测结论（2026-07）：kling-v3-omni-video 在数字人口播/产品带货(中文语境)画质
# 仍最强，也是唯一支持 videoType 4 的模型。Seedance 支持 type5 多图，但真人参考图
# 可能被网关隐私检测拒绝；这种明确拒绝会按原始剧本重建一次 Kling 请求。
# 提质靠 best-of-N + 1080p + 负向约束，不设"高级档"以免误切更差模型。

# 图像主力 seedream-5.0 → 更强兜底 nano banana pro / imagen 4 ultra → kling-img。
IMAGE_MODEL_FALLBACK = [
    "seedream-5.0",
    "nano banana pro",
    "imagen 4 ultra",
    "kling-v3-omni-image",
]


def _catalog_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "online", "active", "enabled", "available"}:
            return True
        if normalized in {"0", "false", "no", "off", "offline", "inactive", "disabled", "unavailable"}:
            return False
    return None


def _catalog_list(value, *, integers=False):
    if isinstance(value, str):
        try:
            value = json.loads(value.strip())
        except (TypeError, ValueError):
            value = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        value = [key for key, enabled in value.items() if _catalog_bool(enabled) is not False]
    if not isinstance(value, (list, tuple, set)):
        value = [value] if value not in (None, "") else []
    output = []
    for item in value:
        try:
            normalized = int(item) if integers else str(item).strip()
        except (TypeError, ValueError):
            continue
        if normalized not in output and normalized != "":
            output.append(normalized)
    return output


def _integrated_audio_value(record):
    capabilities = record.get("capabilities") if isinstance(record.get("capabilities"), dict) else {}
    audio = capabilities.get("audio") if isinstance(capabilities.get("audio"), dict) else {}
    for source, key in (
            (record, "integratedAudio"), (record, "integrated_audio"),
            (record, "supportsIntegratedAudio"), (record, "audioIntegrated"),
            (record, "supportsAudio"), (record, "audioSupport"),
            (record, "audio"), (record, "allowAudio"),
            (capabilities, "integratedAudio"), (audio, "integrated")):
        if key in source:
            return _catalog_bool(source.get(key))
    return None


def _normalize_model_catalog(models):
    """Normalize model identities while retaining duplicate/alias ambiguity."""
    records = {}
    for raw in models or []:
        if not isinstance(raw, dict):
            continue
        # modelId is the stable catalog identity. modelName/displayName are
        # aliases/submission candidates and must not replace it.
        model_id = str(raw.get("modelId") or raw.get("id") or
                       raw.get("modelName") or "").strip()
        if not model_id:
            continue
        aliases = {model_id}
        for key in ("modelName", "displayName", "name", "alias", "aliases"):
            aliases.update(_catalog_list(raw.get(key)))
        online, status = _catalog_bool(raw.get("online")), _catalog_bool(raw.get("status"))
        raw_types = raw.get("allowVideoType")
        candidate = {
            "id": model_id, "aliases": aliases,
            "submission_names": _catalog_submission_names(raw),
            "active": online is not False and status is not False and (online is True or status is True),
            "allow_types": (set(_catalog_list(raw_types, integers=True))
                            if raw_types not in (None, "") else None),
            "image_count": (int(raw.get("imageCount"))
                            if str(raw.get("imageCount", "")).strip().isdigit() else None),
            "integrated_audio": _integrated_audio_value(raw), "conflict": False,
        }
        current = records.get(model_id)
        if current is None:
            records[model_id] = candidate
            continue
        current["aliases"].update(aliases)
        current["submission_names"].update(_catalog_submission_names(raw))
        current["active"] = current["active"] or candidate["active"]
        for field in ("allow_types", "integrated_audio"):
            old, new = current[field], candidate[field]
            if old is None:
                current[field] = new
            elif new is not None and old != new:
                current[field] = None
                current["conflict"] = True
    alias_index = {}
    for model_id, record in records.items():
        for alias in record["aliases"]:
            alias_index.setdefault(alias.lower(), set()).add(model_id)
    return {"records": records, "aliases": alias_index}


def _catalog_submission_names(raw):
    """Return only names explicitly advertised by the provider catalog."""
    names = set()
    for key in ("modelId", "id", "modelName", "name", "displayName"):
        value = raw.get(key)
        if value:
            names.add(str(value).strip())
    for key in ("alias", "aliases"):
        names.update(_catalog_list(raw.get(key)))
    return {name for name in names if name}


def _model_catalog(category="video"):
    try:
        return _normalize_model_catalog(br_client.list_models(category=category))
    except Exception:
        return {"records": {}, "aliases": {}}


def _resolve_catalog_model(value, catalog):
    matches = catalog.get("aliases", {}).get(str(value or "").strip().lower(), set())
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_MODEL_ALIAS: %s -> %s" % (value, ", ".join(sorted(matches))))
    return next(iter(matches)) if matches else None


def _is_kling_video_model(model):
    if "kling-v3-omni" in str(model or "").lower():
        return True
    record = _model_catalog("video").get("records", {}).get(model) or {}
    return any("kling-v3-omni" in alias.lower() for alias in record.get("aliases", set()))


def _available_models_set(category="video"):
    catalog = _model_catalog(category)
    return {model_id for model_id, record in catalog["records"].items() if record["active"]}


def _model_allow_types(category="video"):
    """从网关 /employee/models 读取每个模型权威的 allowVideoType（缓存 60s）。

    返回 {modelName: set(int,...)}。网关字段形如 "[1,2,3,5]"（字符串）。
    这是最权威、不会过期的能力来源；硬编码 VIDEO_MODEL_CAPS 仅作离线兜底。
    """
    catalog = _model_catalog(category)
    return {model_id: record["allow_types"] for model_id, record in catalog["records"].items()
            if record["allow_types"] and not record["conflict"]}


def _model_supports_type(model, video_type):
    """model 是否支持该 videoType。

    优先信任网关权威字段 allowVideoType（实时、不过期）；网关查不到该模型时，
    退回硬编码 VIDEO_MODEL_CAPS 兜底表；两者都没有则默认放行（信任服务端）。
    """
    if not video_type:
        return True
    allow = _model_allow_types("video").get(model)
    if allow is not None:
        return int(video_type) in allow
    caps = VIDEO_MODEL_CAPS.get(model)
    if caps is None:
        return False
    return int(video_type) in caps


def _pick_video_model(preferred=None, video_type=None, dialogue=None, formal=False,
                      allow_fallback=True, reference_count=None, exclude_models=None):
    """按可用性 + videoType 能力从 VIDEO_MODEL_FALLBACK 选第一个可用模型。

    capability-aware（优先信任网关 allowVideoType 权威字段，见 _model_supports_type）：
    - videoType=4（单张参考图/数字人身份锚定）只有 kling 支持 → 这类段自动回落 kling。
    - videoType=5（多图/多主体/人景同框）seedance-2.0 自己就支持 → 保持默认 seedance
      （更快更省），不必回落 kling。别把 4 和 5 混为一谈。
    - preferred 只有在同时「可用」且「支持该 type」时才优先。
    - 查不到可用列表时：仍按 type 能力过滤 FALLBACK，取第一个支持的。
    """
    catalog = _model_catalog("video")
    excluded = {str(item).strip().lower() for item in (exclude_models or [])}
    has_catalog = bool(catalog["records"])
    available = None if has_catalog else _available_models_set("video")
    requested = ([preferred] if preferred else [DEFAULT_MODEL])
    if allow_fallback:
        requested += [item for item in VIDEO_MODEL_FALLBACK if item != preferred]
    ordered = []
    for name in requested:
        canonical = _resolve_catalog_model(name, catalog) or name
        if canonical not in ordered:
            ordered.append(canonical)

    for model_id in ordered:
        if str(model_id).lower() in excluded:
            continue
        record = catalog["records"].get(model_id) if has_catalog else None
        if has_catalog and record is None:
            continue
        if record:
            supports_type = (not video_type or record["allow_types"] is not None and
                             int(video_type) in record["allow_types"])
            supports_reference_count = (
                reference_count is None or record.get("image_count") is None or
                int(reference_count) <= int(record["image_count"]))
            integrated_audio = record["integrated_audio"]
            # Some live catalog records omit audio capability metadata. For
            # known models, omission falls back to the maintained capability
            # contract; an explicit catalog false value still wins.
            if integrated_audio is None:
                integrated_audio = VIDEO_MODEL_INTEGRATED_AUDIO.get(model_id)
            eligible = (record["active"] and not record["conflict"] and
                        supports_type and supports_reference_count)
        else:
            eligible = _model_supports_type(model_id, video_type)
            integrated_audio = VIDEO_MODEL_INTEGRATED_AUDIO.get(model_id)
            if available:
                eligible = eligible and model_id in available
        if eligible and not (formal and dialogue and integrated_audio is not True):
            return model_id
    raise ValueError("NO_CAPABLE_VIDEO_MODEL: type=%s integrated_audio=%s" %
                     (video_type, bool(formal and dialogue)))


def _pick_image_model(preferred=None):
    candidates = ([preferred] if preferred else []) + [
        m for m in IMAGE_MODEL_FALLBACK if m != preferred]
    avail = _available_models_set("image")
    if not avail:
        return candidates[0]
    for m in candidates:
        if m in avail:
            return m
    return candidates[0]


# 注：视频出片不做两遍清洗（首帧图生只会重生成、不保证一致，已移除）。
# 画质稳定靠 best-of-N 择优（render_candidates）。两遍清洗迁移到「文生图」阶段
# （见 asset_prep.py refine_image），因为静态图的二次精修可控且可让客户确认。


# 默认负向约束：AI 视频通病（畸形/糊/多手指/文字扭曲）一律压制，提升成片"干净高级"观感。
# 铁律#15：背景画面禁止出现字幕/文字/动效文字——这些一律走 HyperFrames 后期叠加层，
# 不应该由视频生成模型直接"画"出来（画出来的字体不可控、易乱码、无法二次编辑）。
# 实测网关接受 negativePrompt 字段；对不支持的模型会被忽略，无副作用。
DEFAULT_NEGATIVE = ("模糊, 畸形, 多余手指, 手部畸形, 面部扭曲, 文字乱码, 水印, "
                    "字幕, 文字, 悬浮文字, kinetic typography, 逐字动画文字, 数据标签快闪, "
                    "字幕条, 说明文字, slogan文字, 低分辨率, 噪点, 抖动, 画面撕裂, 多余肢体, 变形")

STORYBOARD_VIDEO_RULES = """
【12格故事板转视频硬性规则】
Use the uploaded FINAL 16:9 4x3 twelve-panel storyboard contact sheet as the primary visual reference.
The storyboard is a monochrome pencil/charcoal PREVIS for composition only, not the finished visual style.
The generated video MUST be photorealistic live-action / realistic commercial footage with natural skin,
real materials, real lighting and believable product scale. NEVER render the video as a sketch, pencil drawing,
charcoal drawing, illustration, monochrome storyboard, comic, anime or contact sheet.
【导演标注颜色系统：只读取语义，不渲染标注】
Interpret the storyboard annotations as director metadata while planning motion:
RED arrows = body / subject movement; BLUE arrows = camera movement;
GREEN marks = framing and composition notes; ORANGE marks = lighting direction;
PURPLE marks = sound and emotional emphasis; BLACK text = short shot notes and panel labels.
Use these annotations to guide the corresponding live-action movement, framing, lighting, sound and emotion.
Do NOT render any arrows, colored marks, annotation strokes, panel labels or storyboard notes in the final video.
Strictly keep the character / product / scene / lighting / story order consistent.
Do NOT animate the whole 12-panel image as a single flat picture.
Do NOT treat the twelve-panel grid itself as the video frame.
Generate continuous video following panel 1 → panel 2 → panel 3 → panel 4 → panel 5 → panel 6 → panel 7 → panel 8 → panel 9 → panel 10 → panel 11 → panel 12 in order.
Preserve the visible body momentum and camera-motion intent from every panel.
Do NOT add extra characters.
Do NOT change the plot.
Do NOT change wardrobe, props, product appearance, spatial relationships, scene layout, or lighting direction.
If this is part of a stitched multi-segment video, keep the same character identity, voice tone, background scene, lighting style, and BGM mood across all segments.
Maintain edit-friendly coverage: adjacent beats should still show 30°–50° camera/subject angle offsets or clear wide/medium/close-up variation.
""".strip()


def _storyboard_video_text(text, enabled=False):
    if not enabled:
        return text
    return STORYBOARD_VIDEO_RULES + "\n\n【本段台词/剧情】\n" + (text or "")


def _compile_seedance_text(segment, model):
    """Compile the Seedance-grade contract for Seedance and Kling fallback.

    Kling uses the same gateway fields but a different model capability profile.
    Preserve timeline, dialogue, reference roles and scope exclusions while using
    a Kling-specific prompt header rather than replaying a Seedance-branded prompt.
    """
    if not segment.get("seedance_native", True):
        return segment.get("text", "")
    if not (seedance_prompt.is_seedance_model(model) or _is_kling_video_model(model)):
        return segment.get("text", "")
    if not any(segment.get(k) for k in ("timeline", "action", "visual", "camera", "camera_movement")):
        return segment.get("text", "")
    prompt_model = ("kling-v3-omni-video" if _is_kling_video_model(model)
                    else model)
    return seedance_prompt.compile_prompt(
        segment, segment.get("references") or segment.get("reference_roles") or [],
        style=segment.get("style"), negative=segment.get("negative_prompt"),
        target_model=prompt_model)


def _privacy_fallback_allowed(model, video_type, ref_urls, allow_fallback=True):
    return (allow_fallback and seedance_prompt.is_seedance_model(model)
            and int(video_type or 1) in (2, 3, 5) and bool(ref_urls)
            and _model_supports_type("kling-v3-omni-video", video_type))


def _submission_text(segment, model, storyboard_ref=False, extend_url=None):
    """Compile from original segment for the target model, never from another model's prompt."""
    text = segment.get("approved_prompt_zh") or _storyboard_video_text(
        _compile_seedance_text(segment, model), storyboard_ref)
    if not segment.get("seedance_native", True):
        audio = segment.get("audio_contract") or {}
        render_plan = (segment.get("render_plan") or {}).get("content") or {}
        if audio:
            text += ("\n【音频执行契约】台词=%s；语言/口音=%s；声音人设=%s；背景音乐=%s；"
                     "音效=%s；口型同步=%s。必须实际生成并遵守，不得当作备注忽略。" % (
                         audio.get("dialogue") or "无", audio.get("language") or "无指定",
                         audio.get("voice") or "无指定", audio.get("bgm") or "无",
                         audio.get("sfx") or "无", "必须" if audio.get("lip_sync") else "不要求"))
        if render_plan:
            text += "\n【已确认渲染方案】%s。必须实际执行。" % json.dumps(
                render_plan, ensure_ascii=False, sort_keys=True)
    if extend_url:
        text = ("将上一段视频自然延长，不重新剪辑前段；保持人物、产品、场景、服装、光线和运镜逻辑完全连续。新增部分：\n"
                 + text)
    return _fit_video_prompt_limit(text, segment, model)


def _require_confirmed_prompt_review(path, stage, segments):
    with open(path, encoding="utf-8") as handle:
        review = json.load(handle)
    expected = {str(seg.get("id")) for seg in segments}
    actual = {str(item.get("shot_id")) for item in review.get("prompts") or []}
    expected_fingerprints = {seg.get("storyboard_plan_fingerprint") for seg in segments
                             if seg.get("storyboard_plan_fingerprint")}
    if (review.get("status") != "confirmed" or review.get("stage") != stage or
            not expected.issubset(actual) or
            (expected_fingerprints and review.get("plan_fingerprint") not in expected_fingerprints)):
        raise ValueError(
            "PROMPT_REVIEW_REQUIRED: %s 阶段的中文提示词必须先由用户确认；缺少=%s" %
            (stage, ",".join(sorted(expected - actual))))
    prompts = {str(item.get("shot_id")): item.get("prompt_zh")
               for item in review.get("prompts") or []}
    for segment in segments:
        if not prompts.get(str(segment.get("id"))):
            raise ValueError("PROMPT_REVIEW_REQUIRED: 缺少镜头 %s 的确认视频提示词" % segment.get("id"))
        segment["approved_prompt_zh"] = prompts[str(segment.get("id"))]


def _fit_video_prompt_limit(text, segment, model, limit=2400):
    """Keep provider prompt below the gateway's 2500-character hard limit.

    Storyboard rules can be large after the structured timeline is expanded.
    Preserve the business-critical dialogue, action, product identity and
    continuity contract while dropping duplicated explanatory prose first.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    audio = segment.get("audio_contract") or {}
    fields = [
        "Kling commercial video. Follow the confirmed storyboard shot order; do not add text, subtitles, logos or extra characters.",
        "Dialogue: %s" % (audio.get("dialogue") or segment.get("dialogue") or ""),
        "Visual: %s" % (segment.get("visual") or ""),
        "Action: %s" % (segment.get("character_action") or segment.get("action") or ""),
        "Camera: %s" % (segment.get("camera_movement") or segment.get("camera") or ""),
        "Scene: %s" % (segment.get("scene_prompt") or ""),
        "Continuity: %s" % (segment.get("continuity_in") or "same confirmed character, grey product, background and lighting"),
        "Product: preserve exact confirmed geometry, neutral grey color, magnetic face and ports.",
    ]
    compact = "\n".join(item for item in fields if item.split(": ", 1)[-1].strip())
    if len(compact) <= limit:
        return compact
    # Preserve the contract header and drop optional clauses at whole-line
    # boundaries. Never split CJK dialogue or omit no-text/storyboard rules.
    required = fields[:2] + [fields[6], fields[7]]
    optional = fields[2:6]
    chosen = list(required)
    for item in optional:
        candidate = "\n".join(chosen + [item])
        if len(candidate) <= limit:
            chosen.append(item)
    return "\n".join(chosen)


def _submit_video(api_key, segment, model, video_type, ref_urls, negative_prompt,
                   *, seed=None, storyboard_ref=False, extend_url=None,
                   request_id=None):
    if request_id is None:
        handoff = (segment.get("video_handoff_fingerprint") or
                   artifact_contract.build_video_handoff(segment)["fingerprint"])
        request_id = _submission_request_id(segment, model, video_type, handoff)
    text = _submission_text(segment, model, storyboard_ref, extend_url)
    task_id = br_client.create_video(
        api_key, text, model=model, video_type=video_type, urls=ref_urls,
        resolution=segment.get("resolution", "1080p"), ratio=segment.get("ratio", "9:16"),
        duration=segment.get("duration", 5), negative_prompt=negative_prompt,
        seed=seed, extend_video_url=extend_url, request_id=request_id)
    return task_id, text


def _submission_request_id(segment, model, video_type, handoff_fingerprint,
                           attempt=1, dependency_fingerprint=None):
    """Deterministic paid-request identity, stable across process restarts."""
    payload = {
        "stage": "video", "unit_id": segment.get("id"),
        "handoff_fingerprint": handoff_fingerprint, "model": model,
        "video_type": int(video_type or 1), "attempt": int(attempt),
        "dependency_fingerprint": dependency_fingerprint,
    }
    return "video-" + artifact_contract.sha256_json(payload)


def _persist_submission_intent(manifest, manifest_path, ledger_path, segment,
                               model, video_type, handoff_fingerprint, attempt=None,
                               dependency_fingerprint=None):
    if attempt is None:
        attempt = _rm.current_video_attempt(manifest or {}, segment.get("id"))
    request_id = _submission_request_id(
        segment, model, video_type, handoff_fingerprint, attempt,
        dependency_fingerprint)
    fields = {"stage": "video", "unit_id": segment.get("id"),
              "handoff_fingerprint": handoff_fingerprint,
              "attempt": attempt, "model": model,
              "dependency_fingerprint": dependency_fingerprint,
              "request_id": request_id, "status": "submitting"}
    if ledger_path:
        generation_ledger.append_event(
            ledger_path, "task_submitting", **fields)
    if manifest is not None:
        _rm.upsert_task(manifest, fields)
        if manifest_path:
            _rm.save_manifest(manifest, manifest_path)
    return request_id


def _current_attempt(manifest, segment_id):
    if manifest is None:
        return 1
    return _rm.current_video_attempt(manifest, segment_id)


def _completed_task(manifest, segment_id, handoff_fingerprint):
    """Reuse success only inside the currently authorized attempt."""
    if manifest is None:
        return None
    attempt = _current_attempt(manifest, segment_id)
    return next((item for item in reversed(manifest.get("tasks", []))
                 if item.get("stage") == "video" and item.get("unit_id") == segment_id
                 and item.get("handoff_fingerprint") == handoff_fingerprint
                 and int(item.get("attempt", 1)) == attempt
                  and item.get("status") == "succeeded" and item.get("task_id")), None)


def _task_video_url(api_key, task):
    info = br_client.get_video(api_key, task["task_id"])
    status = str(info.get("status") or "").lower()
    if status not in ("succeeded", "succeed", "success", "completed") or not info.get("videoUrl"):
        raise br_client.BRError("TASK_URL_REFRESH_FAILED: task %s" % task.get("task_id"))
    return info["videoUrl"]


def _refresh_completed_video_url(api_key, task_id, cached_url=None):
    """Refresh an expiring signed URL from the existing paid task."""
    try:
        info = br_client.get_video(api_key, task_id)
    except br_client.BRError:
        return cached_url
    status = str(info.get("status") or "").lower()
    if status in ("succeeded", "succeed", "success", "completed"):
        return info.get("videoUrl") or cached_url
    return cached_url


def _download_completed_video(api_key, task_id, url, out_path):
    """Download once, refreshing only the URL of the same task on auth expiry."""
    try:
        return br_client.download(url, out_path)
    except br_client.BRError as exc:
        message = str(exc).lower()
        if (getattr(exc, "http_status", None) not in (401, 403, 404) and
                not any(token in message for token in ("expired", "signature"))):
            raise
        refreshed = _refresh_completed_video_url(api_key, task_id, url)
        if not refreshed or refreshed == url:
            raise
        return br_client.download(refreshed, out_path)


def _fallback_metadata(initial_model, model, reason=None):
    used = model != initial_model
    return {"model": model, "initial_model": initial_model,
            "fallback_from": initial_model if used else None,
            "fallback_reason": reason if used else None,
            "retry_count": 1 if used else 0}


def _storyboard_negative(base):
    extra = ("12格故事板整图动画, 把12格当成一张图平移缩放, 分屏拼贴成片, "
             "素描风格, 铅笔画, 炭笔画, 黑白绘画, 插画, 动漫, 新增角色, 换装, "
             "换场景, 改产品外观, 改剧情顺序, 彩色导演箭头, 红色箭头, 蓝色箭头, "
             "绿色构图标记, 橙色灯光标记, 紫色声音标记, 黑色镜头笔记, 面板标签, "
             "故事板标注, 手写批注")
    if not base:
        return extra
    return base + ", " + extra


def render(text, video_type=1, urls=None, model=DEFAULT_MODEL,
           resolution="1080p", ratio="9:16", duration=5, out_path=None,
           api_key=None, verbose=True, negative_prompt=DEFAULT_NEGATIVE, seed=None,
           storyboard_ref=False, seedance_native=False, reference_roles=None,
             style=None, ocr_result=None, allow_model_fallback=True, draft=False,
              client=None, manifest=None, segment_id=None, handoff_fingerprint=None,
              manifest_path=None, ledger_path=None, max_wait=3600):
    """Submit -> poll -> download. Returns (videoUrl, local_path_or_None).

    默认 1080p（实测真出 1920x1080，画质翻倍）+ 默认负向约束（去畸形/糊）。
    seed 可选透传；⚠️ 实测 kling 不真正锁定画面（同 seed SSIM≈0.59），一致性
    不要依赖 seed，靠同参考图(type4)/首尾帧(type3)。
    """
    key_setup.ensure_session_id()
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise br_client.BRError("No API key. Run key onboarding first (paste your sk- key).")

    def log(m):
        if verbose:
            print(m, flush=True)

    if not draft:
        if not client or manifest is None:
            raise ValueError("FORMAL_VIDEO_REQUIRES_CLIENT_MANIFEST: 单段正式入口需要 client、manifest")
        if ledger_path and os.path.isfile(ledger_path):
            _rm.reconcile_tasks_from_ledger(manifest, ledger_path)
            _rm.save_manifest(manifest, manifest_path)
        _rm.identity_gate(manifest, client=client)
        _rm.generation_gate(manifest, "video", client=client)
        recorded = ((manifest.get("handoffs") or {}).get("video") or {}).get("segments") or {}
        if not segment_id or not handoff_fingerprint or recorded.get(segment_id) != handoff_fingerprint:
            raise ValueError("VIDEO_HANDOFF_MISMATCH: 单段必须提供 manifest 中已记录的 segment/handoff")
        if out_path is None:
            raise ValueError("OUT_PATH_REQUIRED: 正式视频入口必须提供 out_path")
        bad = [ref for ref in (urls or []) if not _reference_is_trusted(client, ref, manifest)]
        if bad:
            raise ValueError("UNTRUSTED_VIDEO_REFERENCE: %s" % ", ".join(map(str, bad)))

    # local file paths -> hosted URLs (video endpoint rejects large data-URL bodies)
    if urls:
        log("[refs] normalizing %d image ref(s)…" % len(urls))
        ref_urls = [br_client.to_image_ref(u, api_key=api_key, prefer_hosted=True) for u in urls]
    else:
        ref_urls = urls
    _validate_model_reference_capacity(model, len(ref_urls or []), formal=not draft)

    log("[submit] model=%s type=%s dur=%ss ratio=%s res=%s refs=%s"
        % (model, video_type, duration, ratio, resolution, len(ref_urls) if ref_urls else 0))
    log("[体验提示] %s" % ux.progress_hint("submit"))
    if storyboard_ref:
        negative_prompt = _storyboard_negative(negative_prompt)
    segment = {"id": segment_id, "text": text, "dialogue": text,
               "audio_contract": {"track": "required" if text else "none",
                                  "speech": bool(text), "dialogue": text,
                                  "language": None, "voice": None, "bgm": None,
                                  "sfx": None, "lip_sync": bool(text)},
               "video_handoff_fingerprint": handoff_fingerprint,
               "action": text, "duration": duration, "ratio": ratio, "resolution": resolution,
               "seedance_native": seedance_native, "reference_roles": reference_roles or [],
               "style": style, "negative_prompt": negative_prompt}

    def tick(status, waited):
        log("[poll] %ss status=%s | %s" % (waited, status, ux.progress_hint("poll")))

    initial_model = model
    submission_attempt = _current_attempt(manifest, segment_id)
    resumed = completed = intent = None
    if manifest is not None and segment_id and handoff_fingerprint:
        resumed = _rm.find_resumable_task(manifest, "video", segment_id, handoff_fingerprint)
        intent = _rm.find_submission_intent(manifest, "video", segment_id, handoff_fingerprint)
        completed = _completed_task(manifest, segment_id, handoff_fingerprint)
    try:
        if completed:
            task_id, video_url = completed["task_id"], _task_video_url(api_key, completed)
        else:
            if resumed:
                task_id = resumed["task_id"]
                _record_task_resume(
                    manifest, manifest_path, ledger_path,
                    {"segment": segment, "task_id": task_id, "model": model,
                     "handoff_fingerprint": handoff_fingerprint})
            else:
                attempt = submission_attempt
                request_id = ((intent or {}).get("request_id") or
                              _persist_submission_intent(
                                  manifest, manifest_path, ledger_path, segment,
                                  model, video_type, handoff_fingerprint, attempt=attempt))
                task_id, _ = _submit_video(
                    api_key, segment, model, video_type, ref_urls,
                    negative_prompt, seed=seed, storyboard_ref=storyboard_ref,
                    request_id=request_id)
                _persist_task(
                    manifest, manifest_path, ledger_path,
                    {"segment": segment, "task_id": task_id, "model": model,
                      "handoff_fingerprint": handoff_fingerprint,
                      "request_id": request_id, "attempt": attempt}, "submitted", request_id=request_id)
            log("[submit] taskId=%s" % task_id)
            video_url = br_client.wait_video(
                api_key, task_id, interval=8, max_wait=max_wait, on_tick=tick)
    except br_client.BRVideoReferencePrivacyError:
        if not _privacy_fallback_allowed(initial_model, video_type, ref_urls, allow_model_fallback):
            raise
        model = _pick_video_model(
            "kling-v3-omni-video", video_type=video_type,
            dialogue=text if not draft else None, formal=not draft,
            allow_fallback=False, reference_count=len(ref_urls or []))
        submission_attempt += 1
        log("[privacy-fallback] Seedance 真人参考图被隐私检测拒绝，按原剧本改用 Kling 重试一次")
        request_id = _persist_submission_intent(
            manifest, manifest_path, ledger_path, segment,
            model, video_type, handoff_fingerprint,
            attempt=submission_attempt)
        task_id, _ = _submit_video(api_key, segment, model, video_type, ref_urls,
                                   negative_prompt, seed=seed, storyboard_ref=storyboard_ref,
                                   request_id=request_id)
        _persist_task(
            manifest, manifest_path, ledger_path,
            {"segment": segment, "task_id": task_id, "model": model,
              "handoff_fingerprint": handoff_fingerprint, "request_id": request_id},
              "submitted", request_id=request_id,
              attempt=submission_attempt,
            fallback_reason="reference_real_person_privacy")
        log("[submit] taskId=%s model=%s" % (task_id, model))
        video_url = br_client.wait_video(api_key, task_id, interval=8, max_wait=max_wait, on_tick=tick)
    except br_client.BRError as submit_error:
        if not (allow_model_fallback and br_client.is_video_model_not_found(submit_error)):
            raise
        failed_model = model
        model = _pick_video_model(video_type=video_type, dialogue=text if not draft else None,
                                  formal=not draft, exclude_models=[failed_model],
                                  reference_count=len(ref_urls or []))
        log("[model-fallback] %s 不可用，改用 %s 重试一次" % (failed_model, model))
        submission_attempt += 1
        request_id = _persist_submission_intent(
            manifest, manifest_path, ledger_path, segment, model, video_type,
            handoff_fingerprint, attempt=submission_attempt)
        task_id, _ = _submit_video(api_key, segment, model, video_type, ref_urls,
                                   negative_prompt, seed=seed, storyboard_ref=storyboard_ref,
                                   request_id=request_id)
        video_url = br_client.wait_video(api_key, task_id, interval=8,
                                         max_wait=max_wait, on_tick=tick)
    log("[done] video task completed; signed URL withheld (taskId=%s)" % task_id)
    if manifest is not None:
        _persist_task(
            manifest, manifest_path, ledger_path,
             {"segment": segment, "task_id": task_id, "model": model,
              "handoff_fingerprint": handoff_fingerprint},
             "succeeded", attempt=submission_attempt,
             video_url=video_url)

    local = None
    if out_path:
        log("[体验提示] %s" % ux.progress_hint("download"))
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        _download_completed_video(api_key, task_id, video_url, out_path)
        local = out_path
        log("[saved] %s" % local)
        qc = _media_qc_guard(local, segment, draft=draft, manifest=manifest,
                             manifest_path=None)
        media_qc.require_pass(qc)
        ocr = _ocr_guard(local, log)
        if ocr_result is not None:
            ocr_result.update(ocr or {"available": False,
                                      "subtitle_detected": False,
                                      "texts": [], "frames_checked": 0})
            ocr_result["media_qc"] = qc
    return video_url, local


def _extract_last_frame(video_path, log):
    """抽视频最后一帧存为 png，返回路径。用于尾帧串联(跨段一致性正解)。"""
    import shutil, subprocess, tempfile
    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            from static_ffmpeg import run as sfrun
            ff, _ = sfrun.get_or_fetch_platform_executables_else_raise()
        except Exception:
            log("[chain] 无 ffmpeg，无法抽尾帧，串联降级为独立生成")
            return None
    fd, out = tempfile.mkstemp(suffix="_lastframe.png")
    os.close(fd)
    os.remove(out)
    # -sseof -0.3 定位到结尾前 0.3s，取最后一帧
    r = subprocess.run([ff, "-hide_banner", "-y", "-sseof", "-0.3",
                        "-i", video_path, "-vframes", "1", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    log("[chain] 抽尾帧失败")
    return None


def render_chained(segments, model=None, verbose=True,
                   negative_prompt=DEFAULT_NEGATIVE, allow_model_fallback=True,
                   client=None, manifest=None, manifest_path=None, ledger_path=None,
                     results_out=None, allow_ocr_warning=False, draft=False,
                     max_wait=3600, prompt_review=None):
    """尾帧串联出片（跨段一致性正解，实测 SSIM≈0.96 衔接自然）。

    ⚠️ 与 render_batch 的关键区别：这是**串行**的，因为第 N+1 段要用第 N 段的
    尾帧作首帧参考(videoType=2)。用于「同一个数字人/场景要连贯贯穿多段」的长视频、
    访谈、讲解——避免各段独立生成导致的跳脸/换景。

    实测依据：A 尾帧→B 首帧 SSIM≈0.956（比单段内首尾 0.807 还高），说明 B 是从 A
    结束画面无缝长出来的。seed 锁一致性已证伪(SSIM 0.59)，尾帧串联才是正解。

    第 1 段按其自身 video_type（通常 t2v/参考图）出；后续段自动改用 video_type=2、
    urls=[上一段尾帧的托管URL]。段里显式给了 urls 的则尊重段设置（不覆盖）。
    代价：串行 → 墙钟≈N×单段（不能并行）。只在需要连贯性时用；镜头相互独立时用 render_batch。

    segments: [{text, video_type?, urls?, resolution?, ratio?, duration?, out_path, model?}]
    返回 list of {ok, videoUrl, localPath, absPath, error}，对齐输入顺序。
    """
    non_oral_extensions = [seg.get("id") for seg in segments
                           if (seg.get("extend_video") or seg.get("extend_from_previous"))
                           and not seg.get("oral_broadcast")]
    if non_oral_extensions:
        raise ValueError(
            "VIDEO_EXTENSION_BLOCKED: 只有 oral-broadcast 口播场景允许使用模型视频延长；"
            "其他场景必须独立分段生成后拼接：%s" % ", ".join(map(str, non_oral_extensions)))
    if prompt_review and not draft:
        _require_confirmed_prompt_review(prompt_review, "video", segments)
    if not draft:
        if not client or manifest is None or not manifest_path or not results_out:
            raise ValueError("FORMAL_VIDEO_REQUIRES_CLIENT_MANIFEST: chain 正式入口需要 client、manifest、results_out")
        if ledger_path and os.path.isfile(ledger_path):
            _rm.reconcile_tasks_from_ledger(manifest, ledger_path)
            _rm.save_manifest(manifest, manifest_path)
        _rm.identity_gate(manifest, client=client)
        _rm.generation_gate(manifest, "video", client=client)
        _manifest_handoff_matches(manifest, segments)
        _validate_references(segments, client, manifest)
        if any(not seg.get("out_path") for seg in segments):
            raise ValueError("OUT_PATH_REQUIRED: 正式 chain 每段必须提供 out_path")
    api_key = key_setup.load_key()
    if not api_key:
        raise br_client.BRError("No API key. Run key onboarding first (paste your sk- key).")

    def log(m):
        if verbose:
            print(m, flush=True)

    default_model = _pick_video_model(
        model or DEFAULT_MODEL, allow_fallback=allow_model_fallback)
    stale = []
    for segment in segments:
        handoff = artifact_contract.verify_video_handoff(segment)
        if handoff["strict"] and not handoff["ok"]:
            stale.append(segment.get("id", "?"))
    if stale:
        raise ValueError("STALE_VIDEO_HANDOFF: 串联段生成契约已变化，请重新 split：%s" %
                         ", ".join(stale))
    results = []
    prev_tail_url = None  # 上一段尾帧的托管 URL
    prev_tail_path = None
    prev_video_url = None
    prev_result = None

    for i, seg in enumerate(segments):
        # Keep all submission state iteration-local. A failed segment must not
        # inherit task or model details from the preceding segment's audit row.
        seg_model = initial_model = tid = ref_urls = None
        seg_vtype = seg.get("video_type", 1)
        submission_attempt = None
        try:
            attempt = _current_attempt(manifest, seg.get("id"))
            handoff_fp = seg.get("video_handoff_fingerprint") or artifact_contract.build_video_handoff(seg)["fingerprint"]
            seg = _expand_product_refs(seg, client)
            resumed = None
            completed = None
            intent = None
            if manifest is not None:
                resumed = _rm.find_resumable_task(manifest, "video", seg.get("id"), handoff_fp)
                intent = _rm.find_submission_intent(
                    manifest, "video", seg.get("id"), handoff_fp)
                completed = _completed_task(manifest, seg.get("id"), handoff_fp)
            # 第 1 段用自身设置；后续段的分支取决于 urls 来源：
            #   - 段自己显式给的 urls（非 locked_refs 注入）→ 尊重段设置，不串尾帧
            #   - locked_refs 注入的 urls（seg["_locked_urls"]=True）→ 仍然串尾帧，
            #     并把尾帧图和锁定参考图合并传入，而不是让 locked_refs 完全替代尾帧
            #     （旧 bug：只要 seg.get("urls") 非空就整段跳过 tail-frame 分支，导致
            #     --chain + --locked-refs 一起用时尾帧串联被静默完全禁用）
            #   - 都没有 → 用上一段尾帧串联
            has_own_explicit_urls = bool(seg.get("urls")) and not seg.get("_locked_urls")
            if i == 0 or has_own_explicit_urls:
                vtype = seg.get("video_type", 1)
                urls = seg.get("urls")
                ref_urls = ([br_client.to_image_ref(u, api_key=api_key, prefer_hosted=True)
                             for u in urls] if urls else None)
            elif seg.get("_locked_urls"):
                # locked_refs 注入段：尾帧 + 锁定参考图合并（尾帧优先，串联权重更高）
                locked_urls = seg.get("urls") or []
                ref_urls = ([br_client.to_image_ref(u, api_key=api_key, prefer_hosted=True)
                             for u in locked_urls] if locked_urls else [])
                if prev_tail_url:
                    ref_urls = [prev_tail_url] + [u for u in ref_urls if u != prev_tail_url]
                    if not draft and len(ref_urls) > 4:
                        raise ValueError(
                            "REFERENCE_COUNT_UNSUPPORTED: chain segment %s needs %d references; "
                            "formal flow will not truncate confirmed refs." %
                            (seg.get("id") or "unknown", len(ref_urls)))
                    vtype = 5 if len(ref_urls) > 1 else 4
                    log("[chain %d] 尾帧串联 + 锁定参考图合并注入" % (i + 1))
                else:
                    vtype = seg.get("video_type", 4 if len(ref_urls) == 1 else 5)
                if not ref_urls:
                    vtype = seg.get("video_type", 1)
            else:
                vtype = 2  # 首帧图生：从上一段尾帧接着长
                ref_urls = [prev_tail_url] if prev_tail_url else None
                if not ref_urls:
                    vtype = seg.get("video_type", 1)  # 尾帧没拿到→降级独立生成
                    log("[chain %d] 无上段尾帧，降级独立生成" % (i + 1))
            # vtype 定了再按能力选模型（type4 参考图段自动回落 kling）
            pick_args = {"video_type": vtype}
            if not draft:
                pick_args.update(dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                           or seg.get("dialogue")),
                                 formal=True)
            seg_model = _pick_video_model(
                seg.get("model") or default_model,
                allow_fallback=allow_model_fallback,
                reference_count=len(ref_urls or []), **pick_args)
            _validate_model_reference_capacity(seg_model, len(ref_urls or []), formal=not draft)

            storyboard_ref = bool(seg.get("storyboard_ref") or seg.get("storyboard_ref_mode") or seg.get("use_storyboard_reference"))
            seg["product_identity_lock"] = bool(seg.get("product_identity_lock") or seg.get("product_sku"))
            seg["character_identity_lock"] = bool(seg.get("character_identity_lock") or seg.get("actor"))
            seg_negative = seg.get("negative_prompt", negative_prompt)
            if storyboard_ref:
                seg_negative = _storyboard_negative(seg_negative)
            extend_url = prev_video_url if (i > 0 and seg.get("extend_from_previous")) else None
            dependency = artifact_contract.build_generation_dependency(
                prev_result, tail_path=prev_tail_path, extend_source=extend_url) if i > 0 else None
            if i > 0:
                declared = (seg.get("chain_contract") or {}).get("predecessor_segment_id")
                actual = (prev_result or {}).get("segment_id")
                if declared and str(declared) != str(actual):
                    raise ValueError("CHAIN_PREDECESSOR_MISMATCH: %s != %s" % (declared, actual))
            log("[chain %d/%d] model=%s type=%s %s" % (
                i + 1, len(segments), seg_model, vtype,
                "(视频延长)" if (i > 0 and seg.get("extend_video") and extend_url)
                else "(尾帧串联)" if (i > 0 and vtype in (2, 4, 5) and prev_tail_url
                              and (not seg.get("urls") or seg.get("_locked_urls"))) else ""))
            log("[体验提示] 正在处理第 %d/%d 段；串联模式按顺序生成，每段通常需要 1–3 分钟。" %
                (i + 1, len(segments)))
            if extend_url:
                log("[chain %d] 使用视频延长，新增时长=%ss" % (i + 1, seg.get("duration", 5)))

            def tick(status, waited):
                log("[chain %d poll] %ss %s" % (i + 1, waited, status))
            initial_model = seg_model
            try:
                if completed:
                    tid, url = completed["task_id"], _task_video_url(api_key, completed)
                    task = dict(completed)
                    log("[chain %d resume-download] taskId=%s" % (i + 1, tid))
                else:
                    if resumed:
                        tid = resumed["task_id"]
                        seg_model = resumed.get("model") or seg_model
                        task = dict(resumed)
                        log("[chain %d resume] taskId=%s" % (i + 1, tid))
                        _record_task_resume(
                            manifest, manifest_path, ledger_path,
                            {"segment": seg, "task_id": tid, "model": seg_model,
                             "handoff_fingerprint": handoff_fp})
                    else:
                        attempt = _current_attempt(manifest, seg.get("id"))
                        request_id = ((intent or {}).get("request_id") or
                                      _persist_submission_intent(
                                           manifest, manifest_path, ledger_path, seg,
                                           seg_model, vtype, handoff_fp, attempt=attempt,
                                           dependency_fingerprint=(dependency or {}).get("fingerprint")))
                        tid, _ = _submit_video(api_key, seg, seg_model, vtype, ref_urls, seg_negative,
                                                seed=seg.get("seed"), storyboard_ref=storyboard_ref,
                                                extend_url=extend_url, request_id=request_id)
                        task = {"segment": seg, "task_id": tid, "model": seg_model,
                                "handoff_fingerprint": handoff_fp,
                                "request_id": request_id, "attempt": attempt,
                                "dependency_fingerprint": (dependency or {}).get("fingerprint"),
                                "generation_dependency": (dependency or {}).get("payload")}
                        _persist_task(manifest, manifest_path, ledger_path, task, "submitted",
                                      request_id=request_id)
                    url = br_client.wait_video(api_key, tid, interval=8, max_wait=max_wait, on_tick=tick)
                    if not url:
                        raise br_client.BRError("COMPLETED_WITHOUT_URL")
            except br_client.BRVideoReferencePrivacyError:
                if not _privacy_fallback_allowed(initial_model, vtype, ref_urls, allow_model_fallback):
                    raise
                seg_model = _pick_video_model(
                    "kling-v3-omni-video", video_type=vtype,
                    dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                              or seg.get("dialogue")), formal=not draft,
                    allow_fallback=False)
                log("[chain %d privacy-fallback] Seedance 真人参考图被拒，按原剧本改用 Kling 重试一次" % (i + 1))
                request_id = _persist_submission_intent(
                    manifest, manifest_path, ledger_path, seg,
                    seg_model, vtype, handoff_fp, attempt=attempt + 1,
                    dependency_fingerprint=(dependency or {}).get("fingerprint"))
                tid, _ = _submit_video(api_key, seg, seg_model, vtype, ref_urls, seg_negative,
                                       seed=seg.get("seed"), storyboard_ref=storyboard_ref,
                                       extend_url=extend_url, request_id=request_id)
                task = {"segment": seg, "task_id": tid, "model": seg_model,
                        "handoff_fingerprint": handoff_fp, "request_id": request_id,
                        "attempt": attempt + 1,
                        "dependency_fingerprint": (dependency or {}).get("fingerprint"),
                        "generation_dependency": (dependency or {}).get("payload")}
                _persist_task(manifest, manifest_path, ledger_path, task, "submitted",
                              fallback_reason="reference_real_person_privacy",
                              request_id=request_id, attempt=attempt + 1)
                url = br_client.wait_video(api_key, tid, interval=8, max_wait=max_wait, on_tick=tick)
            prev_video_url = url

            task = {"segment": seg, "task_id": tid, "model": seg_model,
                    "handoff_fingerprint": handoff_fp,
                    "attempt": task.get("attempt", attempt),
                    "dependency_fingerprint": (dependency or {}).get("fingerprint"),
                    "generation_dependency": (dependency or {}).get("payload")}
            _persist_task(manifest, manifest_path, ledger_path, task, "succeeded", video_url=url)

            local = None
            op = seg.get("out_path")
            if op:
                os.makedirs(os.path.dirname(os.path.abspath(op)), exist_ok=True)
                try:
                    _download_completed_video(api_key, tid, url, op)
                    local = op
                except Exception as download_error:
                    results.append({"ok": False, "videoUrl": url, "localPath": None,
                                    "absPath": None, "error": "DOWNLOAD_PENDING: %s" % download_error,
                                    "taskId": tid, "segment_id": seg.get("id"),
                                    "resume_available": True})
                    prev_tail_url = None
                    continue
            qc_task = dict(task, video_url=url)
            qc = _media_qc_guard(local, seg, draft=draft, manifest=manifest,
                                 manifest_path=manifest_path, task=qc_task) if local else None
            if qc and not qc.get("passed"):
                results.append({"ok": False, "videoUrl": url, "localPath": local,
                                "absPath": os.path.abspath(local),
                                "error": "MEDIA_QC_FAILED: %s" % ", ".join(qc.get("errors") or []),
                                "taskId": tid, "segment_id": seg.get("id"),
                                "actual_duration": (qc.get("media") or {}).get("actual_duration"),
                                "media_qc": qc, "media_qc_report": qc.get("report_path")})
                prev_tail_url = None
                continue
            ocr = _ocr_guard(local, log) if local else None
            result = {"ok": True, "videoUrl": url, "localPath": local,
                            "absPath": os.path.abspath(local) if local else None, "error": None,
                                 "ocr_warning": bool(ocr and ocr.get("subtitle_detected")),
                                 "ocr_texts": (ocr.get("texts") if ocr else []),
                                 "ocr_status": ocr.get("status") if ocr else "unavailable",
                                 "ocr_available": bool(ocr and ocr.get("available")),
                                 "ocr_frames_checked": ocr.get("frames_checked", 0) if ocr else 0,
                                 "ocr_expected": ocr.get("expected", 0) if ocr else 0,
                                 "ocr_error": ocr.get("error") if ocr else "OCR unavailable",
                                 "ocr_status": ocr.get("status") if ocr else "unavailable",
                                 "ocr_available": bool(ocr and ocr.get("available")),
                                 "ocr_frames_checked": ocr.get("frames_checked", 0) if ocr else 0,
                                 "ocr_expected": ocr.get("expected", 0) if ocr else 0,
                                 "ocr_error": ocr.get("error") if ocr else "OCR unavailable",
                            "taskId": tid,
                            "segment_id": seg.get("id"), "scene_id": seg.get("scene_id"),
                            "take_id": "%s-take-01" % seg.get("id"),
                            "video_handoff_fingerprint": seg.get("video_handoff_fingerprint"),
                             "review_status": "pending",
                            "actual_duration": (qc.get("media") or {}).get("actual_duration") if qc else None,
                            "media_qc": qc,
                            "media_qc_report": qc.get("report_path") if qc else None,
                            **_fallback_metadata(initial_model, seg_model,
                                                 "reference_real_person_privacy")}
            result["take_fingerprint"] = take_review.take_fingerprint(result)
            result["generation_dependency"] = (dependency or {}).get("payload")
            result["generation_dependency_fingerprint"] = (dependency or {}).get("fingerprint")
            results.append(result)
            prev_result = result

            # 抽本段尾帧，上传，供下一段串联
            if i < len(segments) - 1:
                src_for_tail = local or None
                if not src_for_tail:  # 没存本地则临时下载来抽帧
                    import tempfile
                    fd, src_for_tail = tempfile.mkstemp(suffix="_seg.mp4")
                    os.close(fd)
                    os.remove(src_for_tail)
                    _download_completed_video(api_key, tid, url, src_for_tail)
                tail = _extract_last_frame(src_for_tail, log)
                if not local and src_for_tail and os.path.isfile(src_for_tail):
                    os.remove(src_for_tail)
                prev_tail_path = tail
                prev_tail_url = (br_client.to_image_ref(tail, api_key=api_key, prefer_hosted=True)
                                 if tail else None)
        except (br_client.BRError, ValueError) as e:
            log("[chain %d] FAILED: %s" % (i + 1, e))
            results.append({"ok": False, "videoUrl": None, "localPath": None,
                            "absPath": None, "error": str(e),
                            "chain_aborted": True,
                            "chain_abort_reason": "predecessor_failed"})
            # A chained sequence is one semantic take. Never spend on later
            # independent segments after continuity has already failed.
            for remaining in segments[i + 1:]:
                results.append({"ok": False, "videoUrl": None, "localPath": None,
                                "absPath": None, "segment_id": remaining.get("id"),
                                "error": "CHAIN_ABORTED: predecessor segment failed",
                                "chain_aborted": True})
            break
            prev_tail_path = None
            prev_video_url = None
            prev_result = None
    for result in results:
        if result and result.get("ocr_warning") and not allow_ocr_warning:
            result["delivery_blocked"] = True
            result["needs_confirmation"] = True
    if results_out:
        _atomic_json_write(results_out, results)
    return results


def _apply_locked_refs(segments, locked_refs):
    """把一组共享锚定参考图强制注入每一段（放最前，去重），并把该段
    videoType 升到参考图锚定（有数字人→4，多图→5，单图→2），实现「固定素材、只变
    台词/剧本/运镜」的跨段一致性锁。返回新的 segments 列表（不改原对象）。

    locked_refs: 已确认的共享参考图路径/URL 列表（如人物板正脸+全身、产品 hero、场景图）。
    段自身的 urls 会被保留并接在共享锚之后（共享锚优先，权重更高）。

    每段额外打上 seg["_locked_urls"]=True 标记，区分"这段 urls 是 locked_refs 注入的"
    还是"用户自己在 segments.json 里显式写的 urls"。**这个标记是 --chain + --locked-refs
    组合时不互相打架的关键**：render_chained 的串联分支判断需要知道段的 urls 是不是
    locked_refs 注入的，才能正确地把尾帧图和锁定参考图合并，而不是让 locked_refs 的
    存在直接短路掉尾帧串联逻辑（旧 bug：urls 存在就完全跳过 tail-frame 分支，导致
    --chain 和 --locked-refs 一起用时尾帧串联被静默完全禁用）。
    """
    if not locked_refs:
        return segments
    out = []
    for seg in segments:
        seg = dict(seg)
        own = seg.get("urls") or []
        ordered = (list(own) + list(locked_refs) if seg.get("storyboard_ref")
                   else list(locked_refs) + list(own))
        merged = []
        for u in ordered:
            if u and u not in merged:
                merged.append(u)
        if len(merged) > 4:
            raise ValueError(
                "REFERENCE_COUNT_UNSUPPORTED: segment %s requires %d locked references; "
                "locked references may not be truncated." %
                (seg.get("id") or "unknown", len(merged)))
        seg["urls"] = merged
        existing_refs = {ref.get("url"): dict(ref) for ref in seg.get("references") or []
                         if isinstance(ref, dict) and ref.get("url")}
        seg["references"] = [existing_refs.get(url, {
            "id": "locked_ref_%02d" % (index + 1), "url": url,
            "type": "generic_visual", "scope": "scene", "label": "共享锁定参考图",
            "role": "固定人物/产品/场景", "intent": "只继承已确认主体身份和场景事实"
        }) for index, url in enumerate(merged)]
        seg["_locked_urls"] = True
        # Always recompute from the refs actually sent. Storyboard refs are kept
        # first so a shared lock can never silently evict the approved shot.
        seg["video_type"] = 4 if len(merged) == 1 else (5 if merged else 1)
        if seg.get("video_handoff_fingerprint"):
            seg["video_handoff_fingerprint"] = artifact_contract.build_video_handoff(seg)["fingerprint"]
        out.append(seg)
    return out


def _load_batch_segments(path, ratio_override=None):
    """Load either a raw segment list or the full script_splitter result.

    Keeping the splitter metadata in ``segments.json`` is useful for the later
    assemble/caption steps, so batch rendering should not require callers to
    manually extract ``result["segments"]`` first.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise ValueError("batch JSON 对象必须包含 segments 数组")
    elif isinstance(payload, list):
        segments = payload
    else:
        raise ValueError("batch JSON 必须是 segments 数组或包含 segments 的对象")
    if not all(isinstance(seg, dict) for seg in segments):
        raise ValueError("batch segments 必须是对象数组")
    if ratio_override:
        segments = [dict(seg, ratio=ratio_override) for seg in segments]
    return segments


def _load_manifest_handoff_segments(manifest):
    """Load exact formal segments from the immutable handoff file."""
    handoff = (manifest.get("handoffs") or {}).get("video") or {}
    record = handoff.get("file") or {}
    if not _rm.file_record_is_current(record):
        raise ValueError("VIDEO_HANDOFF_FILE_REQUIRED_OR_STALE")
    segments = _load_batch_segments(record["path"])
    _manifest_handoff_matches(manifest, segments)
    return segments


def _apply_continuity_plan(segments, plan):
    """Inject one reviewed continuity/re-anchor plan without mutating input."""
    target = plan.get("segment_id")
    output, matched = [], False
    for original in segments:
        segment = dict(original)
        if segment.get("id") == target:
            matched = True
            references = list(plan.get("references") or []) + list(segment.get("references") or [])
            deduped = []
            for ref in references:
                if ref.get("url") and ref.get("url") not in [item.get("url") for item in deduped]:
                    deduped.append(ref)
            segment["references"] = deduped[:4]
            segment["urls"] = [ref["url"] for ref in segment["references"]]
            segment["reference_roles"] = segment["references"]
            segment["continuity_in"] = json.dumps(plan.get("continuity_in") or {}, ensure_ascii=False)
            segment["video_type"] = 5 if len(segment["urls"]) > 1 else (2 if segment["urls"] else 1)
            segment["take_review_required"] = True
            segment["continuity_plan"] = plan
            segment["video_handoff_fingerprint"] = artifact_contract.build_video_handoff(segment)["fingerprint"]
        output.append(segment)
    if not matched:
        raise ValueError("CONTINUITY_PLAN_MISMATCH: 找不到 segment %s" % target)
    return output


def render_batch(segments, model=None, verbose=True, seed=None,
                 negative_prompt=DEFAULT_NEGATIVE, client=None, locked_refs=None,
                 allow_model_fallback=True, manifest=None, manifest_path=None,
                  ledger_path=None, results_out=None, allow_ocr_warning=False,
                   draft=False, max_wait=3600, prompt_review=None):
    """Render multiple segments as a PARALLEL async workflow.

    seed：整批共用同一 seed，透传给模型。⚠️ 实测 kling-v3-omni-video 不真正锁定
      画面（同 seed 两条 SSIM≈0.59，差异明显）——seed 对 kling 无一致性效果，
      仅对声明支持 seed 的模型（如部分 seedance/wan）可能生效。**跨段人物一致性
      不要依赖 seed**，应靠同一张 confirmed 参考图(type4)或首尾帧串联(type3)。
      参数保留（无害、不报错），每段可用 seg['seed'] 覆盖。
    negative_prompt：默认压制畸形/糊/多手指等 AI 通病，提升成片干净度。

    IMPORTANT — this is NOT 'video first, then dub'. kling-v3-omni-video is an
    audio+video (音画一体) model: each call takes the spoken script as `text` and
    returns a finished clip WITH voiceover and lip-sync in one shot. There is no
    separate TTS/dub step.

    Because createVideo is async (returns a taskId), all segments are SUBMITTED
    first (fired concurrently), then polled together — so a 3-segment interview
    renders in ~1 clip's wall-time, not 3x. Serial rendering is the wrong model.

    模型降级：走 _pick_video_model 自动兜底（seedance → kling → wan；type4/5 需参考图段自动回落 kling）。
    每段也可用 seg['model'] 单独指定。视频不做两遍清洗。

    segments: list of dicts, each:
      {text, video_type, urls, resolution, ratio, duration, out_path, model?}
    Returns list of {ok, videoUrl, localPath, error} aligned to input order.
    """
    key_setup.ensure_session_id()
    if prompt_review and not draft:
        _require_confirmed_prompt_review(prompt_review, "video", segments)
    if locked_refs:
        segments = _apply_locked_refs(segments, locked_refs)
    if not draft:
        if not client or manifest is None or not manifest_path or not results_out:
            raise ValueError("FORMAL_VIDEO_REQUIRES_CLIENT_MANIFEST: batch 正式入口需要 client、manifest、results_out")
        if ledger_path and os.path.isfile(ledger_path):
            _rm.reconcile_tasks_from_ledger(manifest, ledger_path)
            _rm.save_manifest(manifest, manifest_path)
        _rm.identity_gate(manifest, client=client)
        _rm.generation_gate(manifest, "video", client=client)
        _manifest_handoff_matches(manifest, segments)
        if not all(seg.get("out_path") for seg in segments):
            raise ValueError("OUT_PATH_REQUIRED: 正式 batch 每段必须提供 out_path")
        _validate_references(segments, client, manifest)
        _validate_reference_handoff(segments)
    api_key = key_setup.load_key()
    if not api_key:
        raise br_client.BRError("No API key. Run key onboarding first (paste your sk- key).")

    def log(m):
        if verbose:
            print(m, flush=True)

    # 默认模型：走降级兜底
    default_model = _pick_video_model(model or DEFAULT_MODEL)
    if model and default_model != model:
        log("[batch] 默认模型 %s 不可用，降级到 %s" % (model, default_model))

    # 共享锚定素材锁（跨段固定人物/产品/场景，只变台词/剧本/运镜）
    if locked_refs:
        log("[batch] 已锁定 %d 张共享锚定参考图注入每段（固定素材，只变台词/运镜）" % len(locked_refs))

    # A corrected storyboard is written to a new revision directory. Never let
    # an old segments file silently render against that previous storyboard.
    stale = []
    for seg in segments:
        handoff = artifact_contract.verify_video_handoff(seg)
        if handoff["strict"] and not handoff["ok"]:
            stale.append("%s (视频交接指纹已变化)" % seg.get("id", "?"))
        sb_path = seg.get("storyboard_path")
        if seg.get("storyboard_ref") and sb_path and not os.path.isfile(sb_path):
            stale.append("%s (%s)" % (seg.get("id", "?"), sb_path))
        sb_dir = seg.get("storyboard_dir")
        expected = seg.get("storyboard_plan_fingerprint")
        actual = None
        result_file = os.path.join(sb_dir, "storyboard_result.json") if sb_dir else None
        if result_file and os.path.isfile(result_file):
            try:
                with open(result_file, encoding="utf-8") as handle:
                    actual = json.load(handle).get("plan_fingerprint")
            except (OSError, ValueError, TypeError):
                actual = None
        recorded_result = seg.get("storyboard_result_fingerprint")
        if expected and recorded_result and expected != recorded_result:
            stale.append("%s (分段与故事板结果指纹不一致)" % seg.get("id", "?"))
        if expected and actual and expected != actual:
            stale.append("%s (故事板 revision 已不是分段编译时的版本)" % seg.get("id", "?"))
    if stale:
        raise ValueError(
            "STALE_STORYBOARD: 分段配置引用的故事板文件已不存在或已被修订：%s。"
            "请用最新 storyboard_plan.json 和最新 revision 目录重新运行 script_splitter split，"
            "不要继续使用旧的 segments JSON。" % "; ".join(stale)
        )

    # ---- phase 1: submit ALL tasks up front (parallel async) ----
    tasks = []
    for i, seg in enumerate(segments):
        # State is deliberately initialized per iteration: failed segment i
        # must never inherit audit fields from segment i-1.
        seg_model = initial_model = tid = ref_urls = None
        seg_vtype = seg.get("video_type", 1)
        submission_attempt = None
        try:
            handoff_fp = seg.get("video_handoff_fingerprint") or artifact_contract.build_video_handoff(seg)["fingerprint"]
            resumed = None
            completed = None
            intent = None
            if manifest is not None:
                resumed = _rm.find_resumable_task(manifest, "video", seg.get("id"), handoff_fp)
                intent = _rm.find_submission_intent(
                    manifest, "video", seg.get("id"), handoff_fp)
                completed = _completed_task(manifest, seg.get("id"), handoff_fp)
            # ── product_sku 自动展开：若 seg 含 product_sku，通过 product_library.resolve()
            # 把多方位图展开为 urls，实现与数字人对等的多图锚定（videoType=5）
            seg_urls = seg.get("urls") or []
            product_sku = seg.get("product_sku")
            if product_sku and not seg_urls:
                try:
                    import product_library as _pl
                    _pl_client = seg.get("client") or client or ""
                    if _pl_client:
                        _pr = _pl.resolve(_pl_client, product_sku)
                        expanded = []
                        if _pr.get("hero"):
                            expanded.append(_pr["hero"])
                        expanded.extend(_pr.get("refs") or [])
                        # A confirmed 3x3 product board is a second identity
                        # anchor: views control geometry, the board controls
                        # cross-angle material/details and usage context.
                        if _pr.get("product_board_confirmed") and _pr.get("product_board"):
                            expanded.append(_pr["product_board"])
                        if expanded:
                            seg_urls = expanded[:5]
                            # 如果没显式设 video_type，根据 refs 数量自动升 vtype
                            if product_sku:
                                # A product is an environment/multi-reference
                                # subject even when only hero.png exists. Keep
                                # the segment on type=5 instead of falling back
                                # to single-image type=2.
                                seg = dict(seg, video_type=5)
                            log("[product_sku %d] %s → %d 产品一致性参考图 (vtype=%s)"
                                % (i + 1, product_sku, len(seg_urls), seg.get("video_type", 1)))
                except Exception as _pe:
                    raise ValueError("PRODUCT_SKU_RESOLVE_FAILED: %s (%s)" % (product_sku, _pe))

            if product_sku and not seg_urls:
                raise ValueError("PRODUCT_SKU_NO_REFERENCES: %s" % product_sku)

            urls = seg_urls or None
            if not draft and not product_sku:
                bad = [ref for ref in (urls or [])
                       if not _reference_is_trusted(client, ref, manifest)]
                if bad:
                    raise ValueError("UNTRUSTED_VIDEO_REFERENCE: %s" % ", ".join(map(str, bad)))
            ref_urls = ([br_client.to_image_ref(u, api_key=api_key, prefer_hosted=True)
                         for u in urls] if urls else None)
            seg_vtype = seg.get("video_type", 1)
            pick_args = {"video_type": seg_vtype}
            if not draft:
                pick_args.update(dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                           or seg.get("dialogue")),
                                 formal=True)
            seg_model = _pick_video_model(seg.get("model") or default_model, **pick_args)
            seg_seed = seg.get("seed", seed)  # 段级 seed 覆盖批级；批级 seed 保证跨段一致
            storyboard_ref = bool(seg.get("storyboard_ref") or seg.get("storyboard_ref_mode") or seg.get("use_storyboard_reference"))
            seg_negative = seg.get("negative_prompt", negative_prompt)
            if storyboard_ref:
                seg_negative = _storyboard_negative(seg_negative)
            initial_model = seg_model
            fallback_reason = None
            if completed:
                tid = completed["task_id"]
                seg_model = completed.get("model") or seg_model
                log("[resume-download %d/%d] taskId=%s" % (i + 1, len(segments), tid))
            elif resumed:
                tid = resumed["task_id"]
                seg_model = resumed.get("model") or seg_model
                log("[resume %d/%d] taskId=%s" % (i + 1, len(segments), tid))
                _record_task_resume(
                    manifest, manifest_path, ledger_path,
                    {"segment": seg, "task_id": tid, "model": seg_model,
                     "handoff_fingerprint": handoff_fp})
            else:
                submission_attempt = _current_attempt(manifest, seg.get("id"))
                request_id = ((intent or {}).get("request_id") or
                              _persist_submission_intent(
                                  manifest, manifest_path, ledger_path, seg,
                                  seg_model, seg_vtype, handoff_fp,
                                  attempt=submission_attempt))
                try:
                    tid, _ = _submit_video(api_key, seg, seg_model, seg_vtype, ref_urls,
                                           seg_negative, seed=seg_seed,
                                           storyboard_ref=storyboard_ref,
                                           request_id=request_id)
                except br_client.BRVideoReferencePrivacyError:
                    if not _privacy_fallback_allowed(initial_model, seg_vtype, ref_urls, allow_model_fallback):
                        raise
                    seg_model = _pick_video_model(
                        "kling-v3-omni-video", video_type=seg_vtype,
                        dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                  or seg.get("dialogue")), formal=not draft,
                        allow_fallback=False, reference_count=len(ref_urls or []))
                    fallback_reason = "reference_real_person_privacy"
                    submission_attempt += 1
                    log("[submit %d privacy-fallback] Seedance 真人参考图被拒，改用 Kling 重试一次" % (i + 1))
                    request_id = _persist_submission_intent(
                        manifest, manifest_path, ledger_path, seg,
                        seg_model, seg_vtype, handoff_fp, attempt=submission_attempt)
                    tid, _ = _submit_video(api_key, seg, seg_model, seg_vtype, ref_urls,
                                            seg_negative, seed=seg_seed,
                                            storyboard_ref=storyboard_ref,
                                            request_id=request_id)
                except br_client.BRError as submit_error:
                    if (not allow_model_fallback or
                            not br_client.is_video_model_not_found(submit_error)):
                        raise
                    failed_model = seg_model
                    seg_model = _pick_video_model(
                        video_type=seg_vtype,
                        dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                  or seg.get("dialogue")),
                        formal=not draft,
                        allow_fallback=True,
                        reference_count=len(ref_urls or []),
                        exclude_models=[failed_model])
                    fallback_reason = "model_not_found"
                    submission_attempt += 1
                    log("[submit %d model-fallback] %s 不可用，改用 %s 重试一次" %
                        (i + 1, failed_model, seg_model))
                    request_id = _persist_submission_intent(
                        manifest, manifest_path, ledger_path, seg,
                        seg_model, seg_vtype, handoff_fp, attempt=submission_attempt)
                    tid, _ = _submit_video(api_key, seg, seg_model, seg_vtype, ref_urls,
                                            seg_negative, seed=seg_seed,
                                            storyboard_ref=storyboard_ref,
                                            request_id=request_id)
                if ledger_path:
                    generation_ledger.append_event(
                         ledger_path, "task_submitted", stage="video", unit_id=seg.get("id"),
                         task_id=tid, model=seg_model, handoff_fingerprint=handoff_fp,
                         request_id=request_id)
                if manifest is not None:
                    _rm.upsert_task(manifest, {"stage": "video", "unit_id": seg.get("id"),
                                                "handoff_fingerprint": handoff_fp,
                                                "attempt": submission_attempt,
                                                "task_id": tid, "model": seg_model,
                                                "request_id": request_id, "status": "submitted"})
                    if manifest_path:
                        _rm.save_manifest(manifest, manifest_path)
            log("[submit %d/%d] taskId=%s (model=%s)" % (i + 1, len(segments), tid, seg_model))
            tasks.append({"idx": i, "task_id": tid, "segment": seg, "error": None,
                          "model": seg_model, "initial_model": initial_model,
                          "video_type": seg_vtype, "ref_urls": ref_urls,
                          "negative_prompt": seg_negative, "seed": seg_seed,
                          "storyboard_ref": storyboard_ref,
                            "completed_url": None,
                           "fallback_reason": fallback_reason,
                           "attempt": (completed or resumed or {}).get(
                                "attempt", submission_attempt),
                           "handoff_fingerprint": handoff_fp})
        except (br_client.BRError, ValueError) as e:
            log("[submit %d/%d] FAILED: %s" % (i + 1, len(segments), e))
            tasks.append({"idx": i, "task_id": None, "segment": seg, "error": str(e),
                          "model": seg_model, "initial_model": initial_model,
                          "video_type": seg_vtype, "ref_urls": ref_urls,
                          "attempt": submission_attempt, "fallback_reason": None})

    # ---- phase 2: poll all live tasks together until each finishes ----
    results = [None] * len(segments)
    import time as _t
    pending = [t for t in tasks if t["task_id"]]
    # Poll transport failures do not mean the paid remote task failed. Keep the
    # same task pending until max_wait; only provider terminal status can fail it.
    poll_err_count = {}
    for t in tasks:
        if not t["task_id"]:
            results[t["idx"]] = {"ok": False, "videoUrl": None, "localPath": None,
                                  "error": t["error"],
                                  **_fallback_metadata(t["initial_model"], t["model"], t["fallback_reason"])}
    waited, interval = 0, 8
    while pending and waited <= max_wait:
        for t in list(pending):
            idx, tid, seg = t["idx"], t["task_id"], t["segment"]
            try:
                info = br_client.get_video(api_key, tid)
                poll_err_count[tid] = 0  # 查询成功，重置计数
            except br_client.BRVideoReferencePrivacyError as e:
                if (_privacy_fallback_allowed(t["model"], t["video_type"], t["ref_urls"],
                                              allow_model_fallback)
                        and not t.get("fallback_reason")):
                    t["model"] = _pick_video_model(
                        "kling-v3-omni-video", video_type=t["video_type"],
                        dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                  or seg.get("dialogue")), formal=not draft,
                        allow_fallback=False)
                    t["fallback_reason"] = "reference_real_person_privacy"
                    old_task_id = t["task_id"]
                    old_attempt = int(t.get("attempt", 1))
                    _persist_task(manifest, manifest_path, ledger_path, t, "superseded",
                                  attempt=old_attempt,
                                  reason="reference_real_person_privacy")
                    t["attempt"] = old_attempt + 1
                    request_id = _persist_submission_intent(
                        manifest, manifest_path, ledger_path, seg, t["model"],
                        t["video_type"], t.get("handoff_fingerprint"),
                        attempt=t["attempt"])
                    t["task_id"], _ = _submit_video(
                        api_key, seg, t["model"], t["video_type"], t["ref_urls"],
                        t["negative_prompt"], seed=t["seed"],
                        storyboard_ref=t["storyboard_ref"], request_id=request_id)
                    _persist_task(manifest, manifest_path, ledger_path, t, "submitted",
                                  attempt=t["attempt"], request_id=request_id,
                                  supersedes=old_task_id,
                                  fallback_reason="reference_real_person_privacy")
                    poll_err_count[t["task_id"]] = 0
                    log("[poll %d privacy-fallback] Seedance 真人参考图被拒，Kling 任务已重提" % (idx + 1))
                    continue
                results[idx] = {"ok": False, "videoUrl": None, "localPath": None,
                                "error": str(e),
                                 **_fallback_metadata(t["initial_model"], t["model"], t["fallback_reason"])}
                _persist_task(manifest, manifest_path, ledger_path, t, "failed",
                              error=str(e))
                pending.remove(t)
                continue
            except br_client.BRError as e:
                poll_err_count[tid] = poll_err_count.get(tid, 0) + 1
                t["last_poll_error"] = str(e)
                log("[poll %d] 查询异常(%d 次)，保持同一 taskId 继续等待。%s" %
                    (idx + 1, poll_err_count[tid], ux.friendly_error(e)))
                continue
            status = (info.get("status") or "").lower()
            if status in ("succeeded", "succeed", "success", "completed") and not info.get("videoUrl"):
                results[idx] = {"ok": False, "videoUrl": None, "localPath": None,
                                "error": "COMPLETED_WITHOUT_URL: 远端任务已完成但没有 videoUrl",
                                "taskId": tid, "segment_id": seg.get("id"),
                                "resume_available": False}
                if manifest is not None:
                    _persist_task(manifest, manifest_path, ledger_path, t, "failed",
                                  error=results[idx]["error"])
                pending.remove(t)
            elif status in ("succeeded", "succeed", "success", "completed"):
                url = info["videoUrl"]
                if manifest is not None:
                    _persist_task(manifest, manifest_path, ledger_path, t, "succeeded",
                                  video_url=url)
                local = None
                op = seg.get("out_path")
                if op:
                    os.makedirs(os.path.dirname(os.path.abspath(op)), exist_ok=True)
                    try:
                        _download_completed_video(api_key, tid, url, op)
                        local = op
                    except Exception as download_error:
                        results[idx] = {"ok": False, "videoUrl": url, "localPath": None,
                                        "absPath": None, "error": "DOWNLOAD_PENDING: %s" % download_error,
                                        "taskId": tid, "segment_id": seg.get("id"),
                                        "resume_available": True}
                        if manifest is not None:
                            _persist_task(manifest, manifest_path, ledger_path, t, "succeeded",
                                          video_url=url, download_error=str(download_error))
                        pending.remove(t)
                        continue
                qc_task = dict(t, video_url=url)
                qc = _media_qc_guard(local, seg, draft=draft, manifest=manifest,
                                     manifest_path=manifest_path, task=qc_task) if local else None
                if qc and not qc.get("passed"):
                    results[idx] = {"ok": False, "videoUrl": url, "localPath": local,
                                    "absPath": os.path.abspath(local),
                                    "error": "MEDIA_QC_FAILED: %s" % ", ".join(qc.get("errors") or []),
                                    "taskId": tid, "segment_id": seg.get("id"),
                                    "resume_available": True,
                                    "actual_duration": (qc.get("media") or {}).get("actual_duration"),
                                    "media_qc": qc, "media_qc_report": qc.get("report_path")}
                    pending.remove(t)
                    continue
                log("[done %d] %s" % (idx + 1, local or url))
                ocr = _ocr_guard(local, log) if local else None
                results[idx] = {"ok": True, "videoUrl": url, "localPath": local,
                                "absPath": os.path.abspath(local) if local else None,
                                "error": None,
                                 "ocr_warning": bool(ocr and ocr.get("subtitle_detected")),
                                 "ocr_texts": (ocr.get("texts") if ocr else []),
                                 "ocr_status": ocr.get("status") if ocr else "unavailable",
                                 "ocr_available": bool(ocr and ocr.get("available")),
                                 "ocr_frames_checked": ocr.get("frames_checked", 0) if ocr else 0,
                                 "ocr_expected": ocr.get("expected", 0) if ocr else 0,
                                 "ocr_error": ocr.get("error") if ocr else "OCR unavailable",
                                "taskId": t["task_id"],
                                "segment_id": seg.get("id"),
                                "scene_id": seg.get("scene_id"),
                                "take_id": "%s-take-01" % seg.get("id"),
                                "video_handoff_fingerprint": t.get("handoff_fingerprint"),
                                "review_status": "pending",
                                "actual_duration": (qc.get("media") or {}).get("actual_duration") if qc else None,
                                "media_qc": qc,
                                "media_qc_report": qc.get("report_path") if qc else None,
                                **_fallback_metadata(t["initial_model"], t["model"], t["fallback_reason"])}
                results[idx]["take_fingerprint"] = take_review.take_fingerprint(results[idx])
                if ledger_path:
                    generation_ledger.append_event(
                        ledger_path, "task_succeeded", stage="video", unit_id=seg.get("id"),
                        task_id=t["task_id"], handoff_fingerprint=t.get("handoff_fingerprint"),
                        video_url=url, local_path=local, take_fingerprint=results[idx]["take_fingerprint"])
                if manifest is not None:
                    _rm.upsert_task(manifest, {"stage": "video", "unit_id": seg.get("id"),
                                               "handoff_fingerprint": t.get("handoff_fingerprint"),
                                               "attempt": t.get("attempt", 1), "task_id": t["task_id"],
                                                "model": t["model"], "status": "succeeded",
                                               "take_id": results[idx]["take_id"],
                                               "take_fingerprint": results[idx]["take_fingerprint"]})
                    if manifest_path:
                        _rm.save_manifest(manifest, manifest_path)
                pending.remove(t)
            elif status in ("failed", "error"):
                error = br_client.video_task_error(info)
                if (isinstance(error, br_client.BRVideoReferencePrivacyError)
                        and _privacy_fallback_allowed(t["model"], t["video_type"], t["ref_urls"],
                                                      allow_model_fallback)
                        and not t.get("fallback_reason")):
                    old_task_id = t["task_id"]
                    t["model"] = _pick_video_model(
                        "kling-v3-omni-video", video_type=t["video_type"],
                        dialogue=((seg.get("audio_contract") or {}).get("dialogue")
                                  or seg.get("dialogue")), formal=not draft,
                        allow_fallback=False)
                    t["fallback_reason"] = "reference_real_person_privacy"
                    old_attempt = int(t.get("attempt", 1))
                    _persist_task(manifest, manifest_path, ledger_path, t, "superseded",
                                  attempt=old_attempt,
                                  reason="reference_real_person_privacy")
                    t["attempt"] = old_attempt + 1
                    request_id = _persist_submission_intent(
                        manifest, manifest_path, ledger_path, seg, t["model"],
                        t["video_type"], t.get("handoff_fingerprint"),
                        attempt=t["attempt"])
                    t["task_id"], _ = _submit_video(
                        api_key, seg, t["model"], t["video_type"], t["ref_urls"],
                        t["negative_prompt"], seed=t["seed"],
                        storyboard_ref=t["storyboard_ref"], request_id=request_id)
                    poll_err_count[t["task_id"]] = 0
                    if ledger_path:
                        generation_ledger.append_event(
                            ledger_path, "task_superseded", stage="video", unit_id=seg.get("id"),
                            task_id=old_task_id, replacement_task_id=t["task_id"],
                            handoff_fingerprint=t.get("handoff_fingerprint"),
                            reason="reference_real_person_privacy")
                        generation_ledger.append_event(
                            ledger_path, "task_submitted", stage="video", unit_id=seg.get("id"),
                             task_id=t["task_id"], attempt=t["attempt"], model=t["model"],
                             request_id=request_id,
                            handoff_fingerprint=t.get("handoff_fingerprint"))
                    if manifest is not None:
                        _rm.upsert_task(manifest, {"stage": "video", "unit_id": seg.get("id"),
                                                   "handoff_fingerprint": t.get("handoff_fingerprint"),
                                                    "attempt": t["attempt"], "task_id": t["task_id"],
                                                    "model": t["model"], "status": "submitted",
                                                    "request_id": request_id,
                                                   "supersedes": old_task_id})
                        if manifest_path:
                            _rm.save_manifest(manifest, manifest_path)
                    log("[poll %d privacy-fallback] Seedance 真人参考图被拒，Kling 任务已重提" % (idx + 1))
                else:
                    results[idx] = {"ok": False, "videoUrl": None, "localPath": None,
                                    "error": str(error),
                                     **_fallback_metadata(t["initial_model"], t["model"], t["fallback_reason"])}
                    _persist_task(manifest, manifest_path, ledger_path, t, "failed",
                                  error=str(error))
                    pending.remove(t)
        if pending:
            _t.sleep(interval)
            waited += interval
            completed = len(segments) - len(pending)
            log("[poll] %ss, %d/%d 仍在生成… %s" %
                (waited, len(pending), len(segments),
                 ux.progress_hint("poll", current=min(completed + 1, len(segments)), total=len(segments))))
    for t in pending:  # timed out
        results[t["idx"]] = {"ok": False, "videoUrl": None, "localPath": None,
                              "error": "timeout after %ss" % max_wait,
                              "taskId": t["task_id"], "resume_available": True,
                              "last_poll_error": t.get("last_poll_error"),
                              "segment_id": t["segment"].get("id"),
                              "video_handoff_fingerprint": t.get("handoff_fingerprint"),
                              **_fallback_metadata(t["initial_model"], t["model"], t["fallback_reason"])}
        if ledger_path:
            generation_ledger.append_event(
                ledger_path, "task_timed_out", stage="video", unit_id=t["segment"].get("id"),
                task_id=t["task_id"], handoff_fingerprint=t.get("handoff_fingerprint"))

    for result in results:
        if result and result.get("ocr_warning") and not allow_ocr_warning:
            result["delivery_blocked"] = True
            result["needs_confirmation"] = True
    if results_out:
        _atomic_json_write(results_out, results)
    return results


def _score_candidate(local_path, api_key, criteria, verbose=True):
    """Best-of-N 评分（可选 vision）。返回 0-100 分或 None（无法自动评分）。

    口型/发音无法从静帧判断，vision 只能评「画面质量/构图/人物清晰度/无畸形」，
    发音择优仍需人工听。若未配 vision 或缺帧，返回 None 交由用户选。
    """
    # 抽一帧
    frame = None
    try:
        import shutil
        import subprocess
        import tempfile
        ff = shutil.which("ffmpeg")
        if not ff:
            try:
                from static_ffmpeg import run
                ff, _ = run.get_or_fetch_platform_executables_else_raise()
            except Exception:
                return None
        fd, frame = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        os.remove(frame)
        try:
            subprocess.run([ff, "-hide_banner", "-y", "-i", local_path,
                            "-vf", "select=eq(n\\,10)", "-vframes", "1", frame],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not os.path.exists(frame):
                return None
        except Exception:
            return None
    except Exception:
        return None
    # 调 BasicRouter 多模态 chat 打分（vision 能力，未必所有模型支持）
    # 硬编码 qwen3.7-max 万一下线会静默返回 None（有 except 兜底），不影响出片。
    # 但优先从可用 text 模型里挑一个视觉能力强的，降低"模型下线=评分永远不可用"风险。
    _SCORE_MODEL_CANDIDATES = ["qwen3.7-max", "qwen-vl-max", "qwen3.6-plus", "gpt-4o-mini"]
    score_model = _SCORE_MODEL_CANDIDATES[0]
    try:
        avail = _available_models_set("text")
        if avail:
            for c in _SCORE_MODEL_CANDIDATES:
                if c in avail:
                    score_model = c
                    break
    except Exception:
        pass
    try:
        import base64
        with open(frame, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        prompt = ("你是视频质检。只看这一帧，从画面清晰度、构图、人物是否自然无畸形"
                  "打分。criteria=%s。只回一个 0-100 整数，不要解释。" % (criteria or "通用质量"))
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]
        resp = br_client.chat(api_key, [{"role": "user", "content": content}],
                              model=score_model)
        import re
        m = re.search(r"\d{1,3}", resp or "")
        return int(m.group()) if m else None
    except Exception as e:
        if verbose:
            print("  [score] 自动评分不可用（vision 未配/模型不支持）: %s" % e, flush=True)
        return None
    finally:
        try:
            if frame and os.path.exists(frame):
                os.remove(frame)
        except OSError:
            pass


def render_candidates(text, n=3, video_type=1, urls=None, model=DEFAULT_MODEL,
                      resolution="1080p", ratio="9:16", duration=5, out_prefix=None,
                      criteria=None, api_key=None, verbose=True,
                      negative_prompt=DEFAULT_NEGATIVE, storyboard_ref=False,
                      draft=False):
    """Best-of-N：并行生成 N 个同段候选，全部下载，尽量自动评分择优。

    符合「LLM 是工厂、多生成择优」的定位；async 并行，墙钟≈单段。
    返回 {candidates:[{idx,ok,localPath,videoUrl,score}], best_idx, auto_scored}。
    - 关键段/开场才建议开 N，控制成本。
    - 口型/发音无法从静帧判断——自动分只评画面；发音择优请人工听候选。
    """
    api_key = api_key or key_setup.load_key()
    if not api_key:
        raise br_client.BRError("No API key. Run key onboarding first (paste your sk- key).")
    out_prefix = out_prefix or "output/candidate"
    segments = []
    for i in range(n):
        segments.append({"text": text, "video_type": video_type, "urls": urls,
                         "resolution": resolution, "ratio": ratio, "duration": duration,
                         "out_path": "%s_%d.mp4" % (out_prefix, i + 1),
                         "storyboard_ref": storyboard_ref})
    # best-of-N 不锁 seed：要的是多样性择优，锁 seed 会让 N 版趋同。只带负向约束。
    results = render_batch(segments, model=model, verbose=verbose,
                           negative_prompt=negative_prompt, draft=draft)

    cands, best_idx, best_score, auto = [], None, -1, False
    for i, r in enumerate(results):
        score = None
        if r and r.get("ok") and r.get("localPath"):
            score = _score_candidate(r["localPath"], api_key, criteria, verbose=verbose)
            if score is not None:
                auto = True
                if score > best_score:
                    best_score, best_idx = score, i
        cands.append({"idx": i + 1, "ok": bool(r and r.get("ok")),
                      "localPath": r.get("localPath") if r else None,
                      "videoUrl": r.get("videoUrl") if r else None, "score": score})
    # 无自动分：最优留空，交用户选（仍返回所有候选路径）
    if best_idx is None:
        ok_ones = [c for c in cands if c["ok"]]
        best_idx = (ok_ones[0]["idx"] - 1) if ok_ones else None
    return {"candidates": cands, "best_idx": best_idx,
            "suggested_idx": best_idx, "accepted_idx": None,
            "needs_review": True, "auto_scored": auto}


def main(argv):
    p = argparse.ArgumentParser(description="BasicRouter video engine")
    p.add_argument("--batch", default=None,
                   help="JSON file: segments 数组，或 script_splitter.split() 的完整输出")
    p.add_argument("--text", help="video prompt / script (single segment)")
    p.add_argument("--type", type=int, default=1, dest="video_type",
                   help="1=t2v 2=i2v(first) 3=first+last 4=ref-image 5=multi-ref")
    p.add_argument("--urls", nargs="*", default=None, help="reference image URLs")
    p.add_argument("--storyboard-ref", action="store_true",
                   help="把 --urls/段 urls 当作最终16:9 4x3 12格故事板参考，按1→12格顺序生成连续视频")
    p.add_argument("--seedance-native", action="store_true",
                   help="Seedance 使用时间节点+参考素材角色的原生提示词格式")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--resolution", default="1080p",
                   help="默认 1080p（实测真出 1920x1080，画质翻倍）；省额度可传 720p")
    p.add_argument("--ratio", default=None,
                   help="单段或 batch 覆盖比例；batch 不传时保留每段 ratio，单段默认 9:16")
    p.add_argument("--batch-ratio", default=None,
                   help="batch 模式覆盖所有段落比例（例如 9:16）；默认保留每段 ratio")
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--seed", type=int, default=None,
                   help="固定随机种子；多段/多候选用同一 seed 提升人物/风格一致性")
    p.add_argument("--negative", default=DEFAULT_NEGATIVE,
                   help="负向约束（默认压制畸形/糊/多手指等 AI 通病）；传空串关闭")
    p.add_argument("--out", dest="out_path", default=None, help="local mp4 path")
    p.add_argument("--candidates", type=int, default=1,
                   help="best-of-N：并行生成 N 个同段候选并择优（关键段/开场用；async 墙钟≈单段）")
    p.add_argument("--criteria", default=None, help="best-of-N 自动评分侧重（如 '人物清晰无畸形/构图专业'）")
    p.add_argument("--no-fallback", action="store_true",
                   help="禁用模型自动降级（调试用，不推荐生产）")
    p.add_argument("--max-wait", type=int, default=3600,
                   help="同一视频 task 最长轮询秒数；超时保留 taskId 供恢复，不切换模型或重提")
    p.add_argument("--allow-ocr-warning", action="store_true",
                   help="明确接受 OCR 检出的画面文字；默认阻止单段成片静默交付")
    p.add_argument("--draft", action="store_true",
                   help="显式草稿模式：绕过正式 manifest/client/素材确认闸门")
    p.add_argument("--chain", action="store_true",
                   help="尾帧串联(跨段一致性正解,实测SSIM≈0.96)：后续段用上段尾帧作首帧,"
                        "同人物/场景连贯不跳脸。串行(墙钟≈N×单段)。仅需连贯性时用；"
                        "镜头独立时用默认并行 batch。配合 --batch。")
    p.add_argument("--json", action="store_true", help="print result as JSON")
    p.add_argument("--client", default=None,
                   help="客户标识（用于 product_sku 展开多方位图，与 product_library --client 一致）")
    p.add_argument("--results-out", default=None,
                   help="batch 模式：把每段结果写成 JSON 文件（喂给 script_splitter assemble --results，"
                        "打通「出片→合成」交接，避免工作流断开）")
    p.add_argument("--locked-refs", nargs="*", default=None,
                   help="共享锚定参考图（路径/URL）：强制注入每段最前，固定人物/产品/场景，"
                        "只变台词/剧本/运镜。跨段一致性锁，配合 --batch/--chain。")
    p.add_argument("--manifest", default=None,
                   help="run_manifest.json：提交后立即持久化 taskId，并支持中断恢复")
    p.add_argument("--ledger", default=None,
                   help="generation_runs.jsonl：append-only 生成事件流水账")
    p.add_argument("--continuity-plan", default=None,
                   help="continuity_state plan 输出：只使用已验收父 take 或已批准场景锚点")
    p.add_argument("--prompt-review", default=None,
                   help="已确认的中文视频提示词审核文件；正式生成必须提供")
    args = p.parse_args(argv)

    # ---- batch mode: 默认并行；--chain 走尾帧串联(串行、跨段一致) ----
    if args.batch:
        try:
            segments = _load_batch_segments(
                args.batch, ratio_override=args.batch_ratio or args.ratio)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(json.dumps({"ok": False, "error": "批量段落文件格式错误：%s" % e},
                             ensure_ascii=False) if args.json else "ERROR: 批量段落文件格式错误：%s" % e)
            return 2
        if args.locked_refs:
            segments = _apply_locked_refs(segments, args.locked_refs)
        if args.continuity_plan:
            with open(args.continuity_plan, encoding="utf-8") as handle:
                segments = _apply_continuity_plan(segments, json.load(handle))
        try:
            _validate_reference_handoff(segments)
        except ValueError as error:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
                  if args.json else "ERROR: %s" % error)
            return 2
        manifest = None
        if args.manifest:
            with open(args.manifest, encoding="utf-8") as handle:
                manifest = json.load(handle)
        if not args.draft:
            if not args.prompt_review:
                print("ERROR: 正式生视频前必须提供已确认的中文提示词审核文件 --prompt-review")
                return 2
            if not args.client or manifest is None or not args.manifest or not args.results_out:
                print("ERROR: 正式 batch 必须同时提供 --client、--manifest、--results-out；旧方式请显式使用 --draft")
                return 2
            if args.locked_refs or args.continuity_plan:
                print("ERROR: 正式 batch 禁止在已登记 handoff 后临时修改参考图。请先把 locked refs/continuity plan 写入 segments，重新计算并登记 video handoff，再出片。")
                return 2
        try:
            # Extension segments depend on the previous returned video URL and
            # therefore cannot use the parallel batch path. Automatically use
            # the chained renderer for plans produced by script_splitter.
            use_chain = args.chain or any(seg.get("extend_video") or seg.get("extend_from_previous")
                                          for seg in segments[1:])
            if use_chain:
                results = render_chained(segments, model=args.model, verbose=not args.json,
                                          negative_prompt=args.negative,
                                          allow_model_fallback=not args.no_fallback,
                                          client=args.client, manifest=manifest,
                                          manifest_path=args.manifest, ledger_path=args.ledger,
                                          results_out=args.results_out,
                                          allow_ocr_warning=args.allow_ocr_warning,
                                          draft=args.draft, max_wait=args.max_wait,
                                          prompt_review=args.prompt_review)
            else:
                results = render_batch(segments, model=args.model, verbose=not args.json,
                                        seed=args.seed, negative_prompt=args.negative,
                                        client=args.client,
                                         allow_model_fallback=not args.no_fallback,
                                         manifest=manifest, manifest_path=args.manifest,
                                         ledger_path=args.ledger, results_out=args.results_out,
                                         allow_ocr_warning=args.allow_ocr_warning,
                                           draft=args.draft, max_wait=args.max_wait,
                                           prompt_review=args.prompt_review)
        except br_client.BRError as e:
            message = ux.friendly_error(e)
            print(json.dumps({"ok": False, "error": str(e), "user_message": message},
                             ensure_ascii=False) if args.json else "ERROR: %s\n下一步：%s" % (e, message))
            return 1
        ok_n = sum(1 for r in results if r and r.get("ok"))
        # 把结果落盘，打通「出片 → script_splitter assemble 合成」交接（防工作流断开）
        if args.results_out:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(args.results_out)) or ".", exist_ok=True)
                _atomic_json_write(args.results_out, results)
                if not args.json:
                    print("[batch] 结果已写入 %s（可直接喂给 script_splitter assemble --results）" % args.results_out)
            except (OSError, TypeError, ValueError) as e:
                print("ERROR: 结果落盘失败 %s：%s" % (args.results_out, e))
                return 1
        if args.json:
            print(json.dumps({"ok": ok_n == len(results), "count": len(results),
                              "succeeded": ok_n, "results": results,
                              "results_out": os.path.abspath(args.results_out) if args.results_out else None},
                             ensure_ascii=False))
        else:
            print("[batch] %d/%d 段成片完成" % (ok_n, len(results)))
            for i, r in enumerate(results):
                print("  seg%d: %s" % (i + 1, r.get("localPath") or r.get("videoUrl") or ("FAILED: " + str(r.get("error")))))
        blocked = any(r and r.get("ocr_warning") for r in results)
        if blocked and not args.allow_ocr_warning:
            for r in results:
                if r and r.get("ocr_warning"):
                    r["delivery_blocked"] = True
                    r["needs_confirmation"] = True
            try:
                _atomic_json_write(args.results_out, results)
            except (OSError, TypeError, ValueError):
                return 1
        return 0 if ok_n == len(results) and (not blocked or args.allow_ocr_warning) else 1

    if not args.text:
        print("ERROR: 需要 --text（单段）或 --batch（多段并行）")
        return 2

    single_manifest = None
    single_segment = None
    if not args.draft:
        if not args.client or not args.manifest or not args.results_out:
            print("ERROR: 单段正式入口必须提供 --client、--manifest、--results-out；旧方式请显式使用 --draft")
            return 2
        try:
            with open(args.manifest, encoding="utf-8") as handle:
                single_manifest = json.load(handle)
            formal_segments = _load_manifest_handoff_segments(single_manifest)
            if len(formal_segments) != 1:
                raise ValueError("单段入口要求 manifest 恰好记录一个 video handoff")
            single_segment = formal_segments[0]
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print("ERROR: manifest 无法用于单段正式生成：%s" % error)
            return 2

    # ---- best-of-N candidates mode ----
    if args.candidates and args.candidates > 1:
        if not args.draft:
            print("ERROR: 正式 best-of-N 必须使用带多个 recorded handoff 的 batch；旧候选入口请显式使用 --draft")
            return 2
        prefix = None
        if args.out_path:
            base = args.out_path[:-4] if args.out_path.endswith(".mp4") else args.out_path
            prefix = base
        try:
            res = render_candidates(
                args.text, n=args.candidates, video_type=args.video_type, urls=args.urls,
                model=args.model, resolution=args.resolution, ratio=args.ratio or "9:16",
                duration=args.duration, out_prefix=prefix, criteria=args.criteria,
                verbose=not args.json, storyboard_ref=args.storyboard_ref,
                draft=True)
        except (br_client.BRError, ValueError) as e:
            message = ux.friendly_error(e)
            print(json.dumps({"ok": False, "error": str(e), "user_message": message},
                             ensure_ascii=False) if args.json else "ERROR: %s\n下一步：%s" % (e, message))
            return 1
        if args.json:
            print(json.dumps({"ok": True, **res}, ensure_ascii=False))
        else:
            print("[best-of-%d] 候选：" % args.candidates)
            for c in res["candidates"]:
                tag = "★推荐" if (res["best_idx"] is not None and c["idx"] == res["best_idx"] + 1) else "     "
                sc = ("  评分%s" % c["score"]) if c["score"] is not None else ""
                print("  %s 候选%d: %s%s" % (tag, c["idx"], c.get("localPath") or ("FAILED: " + str(c.get("videoUrl"))), sc))
            if not res["auto_scored"]:
                print("  （未配 vision 自动评分：请人工目检画面 + 听发音/口型后从上面候选中挑选）")
        return 0

    try:
        # 单段也走降级兜底（除非 --no-fallback）；按 videoType 能力选（type4→kling）
        picked = _pick_video_model(
            args.model, video_type=args.video_type,
            dialogue=args.text if not args.draft else None, formal=not args.draft,
            allow_fallback=(not args.no_fallback) if args.draft else False)
        if picked != args.model and not args.json:
            print("[model] %s 不可用，降级到 %s" % (args.model, picked))
        ocr_result = {}
        if args.draft:
            url, local = render(
                args.text, video_type=args.video_type, urls=args.urls,
                model=picked, resolution=args.resolution, ratio=args.ratio or "9:16",
                duration=args.duration, out_path=args.out_path,
                negative_prompt=args.negative, seed=args.seed,
                verbose=not args.json, storyboard_ref=args.storyboard_ref,
                seedance_native=args.seedance_native, ocr_result=ocr_result,
                allow_model_fallback=not args.no_fallback, draft=True,
                max_wait=args.max_wait)
        else:
            segment = dict(single_segment)
            if args.out_path and os.path.abspath(args.out_path) != os.path.abspath(segment.get("out_path") or ""):
                raise ValueError("FORMAL_SINGLE_OUT_PATH_MISMATCH")
            formal = render_batch(
                [segment], model=picked, verbose=not args.json, seed=args.seed,
                negative_prompt=args.negative, client=args.client,
                allow_model_fallback=not args.no_fallback, manifest=single_manifest,
                manifest_path=args.manifest, ledger_path=args.ledger,
                results_out=args.results_out,
                allow_ocr_warning=args.allow_ocr_warning, draft=False,
                max_wait=args.max_wait)[0]
            if not formal.get("ok"):
                raise ValueError(formal.get("error") or "单段生成失败")
            url, local = formal.get("videoUrl"), formal.get("localPath")
            ocr_result.update({"subtitle_detected": formal.get("ocr_warning", False),
                               "texts": formal.get("ocr_texts", [])})
    except (br_client.BRError, ValueError) as e:
        message = ux.friendly_error(e)
        if args.json:
            print(json.dumps({"ok": False, "error": str(e), "user_message": message},
                             ensure_ascii=False))
        else:
            print("ERROR: %s\n下一步：%s" % (e, message))
        return 1
    if args.json:
        ocr_warning = bool(ocr_result.get("subtitle_detected"))
        out = {"ok": not ocr_warning or args.allow_ocr_warning,
               "videoUrl": url, "localPath": local,
               "ocr_warning": ocr_warning,
                "ocr_texts": ocr_result.get("texts", [])}
        if ocr_result.get("media_qc"):
            out["media_qc"] = ocr_result["media_qc"]
            out["media_qc_report"] = ocr_result["media_qc"].get("report_path")
            out["actual_duration"] = (ocr_result["media_qc"].get("media") or {}).get("actual_duration")
        if local:
            out["absPath"] = os.path.abspath(local)  # 供 agent 用绝对路径发给客户
        if ocr_warning and not args.allow_ocr_warning:
            out["needs_confirmation"] = True
            out["error"] = ("OCR 检出画面文字，已阻止静默交付。请重新生成，或明确使用 "
                            "--allow-ocr-warning 接受该结果。")
        print(json.dumps(out))
        return 0 if out["ok"] else 1
    if ocr_result.get("subtitle_detected") and not args.allow_ocr_warning:
        print("[OCR_WARNING] 已阻止交付：请重新生成，或使用 --allow-ocr-warning 明确接受。")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        code = str(exc).split(":", 1)[0] or "VIDEO_WORKFLOW_BLOCKED"
        print("ERROR:%s\n下一步：请查看 run 状态、完成缺失审批或补齐素材后重试。\n详情：%s" %
              (code, exc), file=sys.stderr)
        raise SystemExit(2)
