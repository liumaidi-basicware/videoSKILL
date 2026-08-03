#!/usr/bin/env python3
"""Persistent run manifest for approvals, inputs, and generated outputs."""
import hashlib
import json
import os
import argparse
import tempfile
import copy
from datetime import datetime
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_contract import verify_video_handoff  # noqa: E402
import take_review as _take_review  # noqa: E402
import take_review  # noqa: E402
from project_utils import (FileLock, validate_client, validate_run_id,
                           require_contained_path)
from agent_runtime import detect_agent_runtime


STAGES = (
    "brief", "script", "product_board", "cast_board", "product_usage", "storyboard", "render_plan",
    "video", "captions", "final", "derive",
)


class ManifestConflictError(ValueError):
    """The manifest changed on disk after the caller loaded its snapshot."""

# Generation dependencies are deliberately stricter than the approval list. A
# stage may only start after its inputs have been approved and recorded. The
# product board is conditional because pure talking-head projects do not need it.
BASE_DEPENDENCIES = {
    "brief": (),
    "script": ("brief",),
    "cast_board": ("script",),
    "product_board": ("brief",),
    "product_usage": ("script", "product_board", "cast_board"),
    "storyboard": ("script", "cast_board"),
    "render_plan": ("storyboard",),
    "video": ("storyboard", "render_plan"),
    "captions": ("video",),
    "final": ("captions",),
    "derive": ("final",),
}


def _downstream_stages(stage, manifest=None):
    """Return graph descendants whose inputs include ``stage``."""
    result = []
    pending = [stage]
    while pending:
        current = pending.pop(0)
        for candidate in BASE_DEPENDENCIES:
            dependencies = (generation_dependencies(manifest, candidate)
                            if manifest is not None else BASE_DEPENDENCIES[candidate])
            if current in dependencies and candidate not in result:
                result.append(candidate)
                pending.append(candidate)
    return result


def _digest(value):
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _session_digest(session_id=None):
    value = session_id if session_id is not None else os.environ.get(
        "BASICROUTER_SESSION_ID", "")
    value = value.strip()
    return _digest(value) if value else None


def _plan_requires_product_board(plan_path):
    """Infer whether the approved plan contains product references."""
    if not plan_path or not os.path.isfile(plan_path):
        return False
    try:
        with open(plan_path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False
    refs = plan.get("asset_refs") or {}
    if refs.get("product_images") or refs.get("product_sku") or plan.get("product_type"):
        return True
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        shot_refs = shot.get("asset_refs") or {}
        if (shot_refs.get("product_images") or shot_refs.get("product_sku") or
                shot.get("product_sku") or shot.get("product_refs") or
                shot.get("product_type") or shot.get("product")):
            return True
    return False


def _plan_requires_product_usage(plan_path):
    """Infer whether a digital human and product must share a confirmed use anchor."""
    if not plan_path or not os.path.isfile(plan_path):
        return False
    try:
        with open(plan_path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False
    refs = plan.get("asset_refs") or {}
    has_human = bool(plan.get("characters") or refs.get("digital_human_portraits"))
    return has_human and _plan_requires_product_board(plan_path)


def create_manifest(client, run_id, *, script_path=None, plan_path=None):
    """Create a resumable manifest without overwriting an existing run."""
    validate_client(client)
    validate_run_id(run_id)
    now = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "manifest_version": 4,
        "revision": 0,
        "run_id": run_id,
        "client": client,
        "created_at": now,
        "updated_at": now,
        "status": "created",
        "script": _file_record(script_path),
        "storyboard_plan": _file_record(plan_path),
        "approvals": {stage: False for stage in STAGES},
        "assets": [],
        "outputs": [],
        "tasks": [],
        "handoffs": {},
        "accepted_takes": {},
        "ocr_checks": {},
        "ocr_waivers": {},
        "requires_product_board": _plan_requires_product_board(plan_path),
        "requires_product_usage": _plan_requires_product_usage(plan_path),
        "identity": {
            "client": client,
            "asset_dir": os.path.abspath(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "assets", client)),
            "session_id_sha256": _session_digest(),
            "agent_runtime": detect_agent_runtime(),
        },
    }
    return manifest


def _file_record(path):
    if not path:
        return None
    record = {"path": os.path.abspath(path), "exists": os.path.isfile(path)}
    if record["exists"]:
        with open(path, "rb") as handle:
            record["sha256"] = _digest(handle.read())
    return record


def file_record(path):
    """Public content identity for a file-backed handoff artifact."""
    return _file_record(path)


def file_record_is_current(record):
    """Return whether a persisted file identity still matches disk bytes."""
    current = _refresh_file_record(record)
    return bool(current and current.get("exists") and current == record)


def _refresh_file_record(record):
    """Re-read one persisted file record instead of trusting cached metadata."""
    if not record or not record.get("path"):
        return None
    return _file_record(record["path"])


def _stage_artifacts(manifest, stage, *, refresh=False):
    """Return the real artifacts whose bytes are approved for ``stage``."""
    generation = manifest.get("generation", {}).get(stage) or {}
    records = generation.get("artifacts")
    if records is None:
        records = [_file_record(path) for path in generation.get("outputs", [])]
    records = [record for record in (records or []) if record]
    if refresh:
        records = [_refresh_file_record(record) for record in records]
        records = [record for record in records if record]
    return records


def _approval_input_hash(manifest, stage, *, refresh=False):
    """Hash stage outputs plus the exact upstream approvals they were built on."""
    records = _stage_artifacts(manifest, stage, refresh=refresh)
    dependencies = {
        dependency: manifest.get("approval_hashes", {}).get(dependency)
        for dependency in generation_dependencies(manifest, stage)
    }
    return _digest(json.dumps(
        {"artifacts": records, "dependencies": dependencies},
        sort_keys=True, ensure_ascii=False))


def approval_is_current(manifest, stage):
    """Check approval against bytes currently on disk, not cached manifest data."""
    if not manifest.get("approvals", {}).get(stage):
        return False
    artifacts = _stage_artifacts(manifest, stage, refresh=True)
    if not artifacts or any(not item.get("exists") for item in artifacts):
        return False
    if any(not approval_is_current(manifest, dependency)
           for dependency in generation_dependencies(manifest, stage)):
        return False
    return manifest.get("approval_hashes", {}).get(stage) == _approval_input_hash(
        manifest, stage, refresh=True)


def identity_gate(manifest, *, client=None, asset_dir=None):
    """Reusable run/client identity gate for orchestrators and generation code.

    Agent runtime metadata is deliberately not gated: a run may be resumed by
    Kilo, Codex, Hermes, or another compatible host.
    """
    manifest_client = manifest.get("client")
    validate_client(manifest_client)
    validate_run_id(manifest.get("run_id"))
    identity = manifest.get("identity") or {}
    if not manifest_client or identity.get("client") != manifest_client:
        raise ValueError("RUN_IDENTITY_MISMATCH: manifest client 与 identity 不一致")
    if client is not None and client != manifest_client:
        raise ValueError("RUN_IDENTITY_MISMATCH: 当前 client 与 run 不一致")
    expected_assets = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", manifest_client))
    recorded_assets = identity.get("asset_dir")
    if recorded_assets and os.path.abspath(recorded_assets) != expected_assets:
        raise ValueError("RUN_IDENTITY_MISMATCH: manifest asset_dir 与 client 不一致")
    if asset_dir is not None and os.path.abspath(asset_dir) != expected_assets:
        raise ValueError("RUN_IDENTITY_MISMATCH: 当前 asset_dir 与 run 不一致")
    return True


def run_output_root(manifest):
    identity_gate(manifest)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "output", manifest["client"], manifest["run_id"])


def require_run_output(manifest, path, *, label="output", must_exist=False):
    return require_contained_path(
        run_output_root(manifest), path, label=label, must_exist=must_exist)


def generation_dependencies(manifest, stage):
    """Return the approved stages required before starting ``stage``."""
    if stage not in BASE_DEPENDENCIES:
        raise ValueError("未知生成阶段: %s" % stage)
    dependencies = list(BASE_DEPENDENCIES[stage])
    plan_record = manifest.get("storyboard_plan") or {}
    plan_path = plan_record.get("path") if isinstance(plan_record, dict) else None
    requires_board = (_plan_requires_product_board(plan_path) if plan_path and os.path.isfile(plan_path)
                      else manifest.get("requires_product_board"))
    requires_usage = (_plan_requires_product_usage(plan_path) if plan_path and os.path.isfile(plan_path)
                      else manifest.get("requires_product_usage"))
    if stage in ("storyboard", "video") and requires_board:
        dependencies.append("product_board")
    if stage in ("storyboard", "video") and requires_usage:
        dependencies.append("product_usage")
    return tuple(dependencies)


def record_generation_requirements(manifest):
    """Persist plan-derived optional-stage requirements explicitly."""
    plan_record = manifest.get("storyboard_plan") or {}
    plan_path = plan_record.get("path") if isinstance(plan_record, dict) else None
    if plan_path and os.path.isfile(plan_path):
        manifest["requires_product_board"] = bool(_plan_requires_product_board(plan_path))
        manifest["requires_product_usage"] = bool(_plan_requires_product_usage(plan_path))
    return manifest


def generation_gate(manifest, stage, *, client=None, asset_dir=None):
    """Validate a generation transition without mutating the manifest.

    This is the single gate for UI/orchestrator code. It prevents a batch job
    from submitting dependent image tasks before the previous asset was
    approved, while still allowing projects that do not require a product board.
    """
    identity_gate(manifest, client=client, asset_dir=asset_dir)
    missing = [dependency for dependency in generation_dependencies(manifest, stage)
                if not approval_is_current(manifest, dependency)]
    if missing:
        raise ValueError(
            "GENERATION_BLOCKED: %s 依赖已确认阶段: %s" %
            (stage, ", ".join(missing))
        )
    return True


def mark_generation_started(manifest, stage):
    """Record an in-flight stage only after its dependency gate passes."""
    generation_gate(manifest, stage)
    manifest.setdefault("approvals", {})[stage] = False
    manifest.setdefault("approval_hashes", {}).pop(stage, None)
    for downstream in _downstream_stages(stage, manifest):
        manifest["approvals"][downstream] = False
        manifest["approval_hashes"].pop(downstream, None)
    manifest.setdefault("generation", {})[stage] = {
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest["status"] = "generating_%s" % stage
    return manifest


def mark_generation_finished(manifest, stage, outputs=()):
    """Record outputs and leave the stage awaiting explicit customer approval."""
    if stage == "video":
        raise ValueError("VIDEO_STAGE_SPECIALIZED_FINISH_REQUIRED")
    if stage not in BASE_DEPENDENCIES:
        raise ValueError("未知生成阶段: %s" % stage)
    outputs = list(outputs)
    output_records = [_file_record(path) for path in outputs]
    missing = [os.path.abspath(path) for path, record in zip(outputs, output_records)
               if not record or not record.get("exists")]
    if missing:
        raise ValueError("GENERATION_OUTPUT_MISSING: %s" % ", ".join(missing))
    if not output_records:
        raise ValueError("GENERATION_OUTPUT_MISSING: %s 阶段没有可审批产物" % stage)
    generation = manifest.setdefault("generation", {})
    generation[stage] = {
        "status": "pending_approval",
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "outputs": [os.path.abspath(path) for path in outputs],
        "artifacts": output_records,
    }
    for path in outputs:
        add_output(manifest, path, kind=stage)
    manifest["status"] = "needs_%s_approval" % stage
    return manifest


def mark_video_generation_finished(manifest, outputs=()):
    """Internal specialized finish used only after the video closure is built."""
    stage = "video"
    outputs = list(outputs)
    records = [_file_record(path) for path in outputs]
    if not records or any(not record or not record.get("exists") for record in records):
        raise ValueError("GENERATION_OUTPUT_MISSING: video")
    manifest.setdefault("generation", {})[stage] = {
        "status": "pending_approval",
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "outputs": [os.path.abspath(path) for path in outputs],
        "artifacts": records,
    }
    for path in outputs:
        add_output(manifest, path, kind=stage)
    manifest["status"] = "needs_video_approval"
    return manifest


def bootstrap_pending_approval(manifest, stage, outputs):
    """Register pre-existing brief/script files as generated approval inputs.

    This is intended for files authored before a run manifest was created. It
    deliberately has the same existence checks as a normal generation finish.
    """
    if stage not in ("brief", "script"):
        raise ValueError("APPROVAL_BOOTSTRAP_BLOCKED: 仅 brief/script 可登记既有产物")
    return mark_generation_finished(manifest, stage, outputs)


def load_manifest(path):
    """Load a manifest snapshot, treating pre-revision files as revision zero."""
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest.setdefault("revision", 0)
    return manifest


def save_manifest(manifest, path, *, expected_revision=None, lock_timeout=10.0,
                  stale_after=300.0, create_only=False):
    """Atomically save with revision CAS so stale snapshots cannot overwrite."""
    path = os.path.abspath(path)
    expected = int(manifest.get("revision", 0) if expected_revision is None
                   else expected_revision)
    candidate = copy.deepcopy(manifest)
    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")
    identity = candidate.setdefault("identity", {})
    identity_gate(candidate)
    expected_assets = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", candidate["client"]))
    if identity.get("asset_dir") and os.path.abspath(identity["asset_dir"]) != expected_assets:
        raise ValueError("RUN_IDENTITY_MISMATCH: manifest asset_dir 与 client 不一致")
    identity["asset_dir"] = expected_assets
    current_session = _session_digest()
    history = list(identity.get("session_id_sha256_history", []))
    for digest in (identity.get("session_id_sha256"), current_session):
        if digest and digest not in history:
            history.append(digest)
    identity["session_id_sha256_history"] = history
    current_runtime = detect_agent_runtime()
    runtime_history = list(identity.get("agent_runtime_history", []))
    for runtime in (identity.get("agent_runtime"), current_runtime):
        if runtime and runtime not in runtime_history:
            runtime_history.append(runtime)
    identity["agent_runtime"] = current_runtime
    identity["agent_runtime_history"] = runtime_history
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with FileLock(path + ".lock", timeout=lock_timeout, stale_after=stale_after):
        if create_only and os.path.exists(path):
            raise ManifestConflictError("RUN_EXISTS: manifest 已存在")
        disk_revision = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                current = json.load(handle)
            disk_revision = int(current.get("revision", 0))
        if ((disk_revision is None and expected != 0) or
                (disk_revision is not None and disk_revision != expected)):
            raise ManifestConflictError(
                "MANIFEST_REVISION_CONFLICT: expected=%s actual=%s" %
                (expected, disk_revision))
        candidate["revision"] = expected + 1
        fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(candidate, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            try:
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    manifest.clear()
    manifest.update(candidate)
    return path


def approve(manifest, stage, approved=True, strict=False):
    if stage not in STAGES:
        raise ValueError("未知审批阶段: %s" % stage)
    if approved and stage == "video":
        validate_video_closure(manifest)
    if approved and strict:
        missing = [dependency for dependency in generation_dependencies(manifest, stage)
                   if not approval_is_current(manifest, dependency)]
        if missing:
            raise ValueError("审批顺序错误：请先完成 %s" % ", ".join(missing))
        generation = manifest.get("generation", {}).get(stage) or {}
        if generation.get("status") != "pending_approval":
            raise ValueError("APPROVAL_BLOCKED: %s 尚未处于 pending_approval" % stage)
        artifacts = _stage_artifacts(manifest, stage, refresh=True)
        if not artifacts or any(not item.get("exists") for item in artifacts):
            raise ValueError("APPROVAL_BLOCKED: %s 的待审批产物不存在" % stage)
        # Capture refreshed records so the approval and subsequent checks use
        # exactly the same bytes. Any later disk edit invalidates this hash.
        generation["artifacts"] = artifacts
        manifest.setdefault("approval_hashes", {})[stage] = _approval_input_hash(manifest, stage)
    if approved:
        for downstream in _downstream_stages(stage):
            manifest.setdefault("approvals", {})[downstream] = False
            manifest.setdefault("approval_hashes", {}).pop(downstream, None)
    manifest.setdefault("approvals", {})[stage] = bool(approved)
    if not approved:
        manifest.setdefault("approval_hashes", {}).pop(stage, None)
    elif strict:
        manifest.setdefault("generation", {}).setdefault(stage, {})["status"] = "approved"
        manifest["generation"][stage]["approved_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["status"] = stage if approved else "needs_%s_approval" % stage
    return manifest


def add_output(manifest, path, kind="file"):
    item = _file_record(path) or {"path": os.path.abspath(path), "exists": False}
    item["kind"] = kind
    manifest.setdefault("outputs", []).append(item)
    return manifest


def task_key(stage, unit_id, handoff_fingerprint, attempt=1):
    return "%s:%s:%s:%s" % (stage, unit_id, handoff_fingerprint, attempt)


def upsert_task(manifest, task):
    task = dict(task)
    key = task.get("task_key") or task_key(
        task.get("stage", "video"), task.get("unit_id"),
        task.get("handoff_fingerprint"), task.get("attempt", 1))
    task["task_key"] = key
    task["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tasks = manifest.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("task_key") == key:
            tasks[index] = dict(existing, **task)
            return tasks[index]
    tasks.append(task)
    return task


def find_resumable_task(manifest, stage, unit_id, handoff_fingerprint):
    attempt = current_video_attempt(manifest, unit_id) if stage == "video" else None
    candidates = [task for task in manifest.get("tasks", [])
                  if task.get("stage") == stage and task.get("unit_id") == unit_id
                  and task.get("handoff_fingerprint") == handoff_fingerprint
                   and task.get("status") in ("submitted", "running", "timed_out")
                   and (attempt is None or int(task.get("attempt", 1)) == attempt)
                  and task.get("task_id")]
    return candidates[-1] if candidates else None


def find_submission_intent(manifest, stage, unit_id, handoff_fingerprint):
    attempt = current_video_attempt(manifest, unit_id) if stage == "video" else None
    candidates = [task for task in manifest.get("tasks", [])
                  if task.get("stage") == stage and task.get("unit_id") == unit_id
                  and task.get("handoff_fingerprint") == handoff_fingerprint
                   and task.get("status") == "submitting"
                   and (attempt is None or int(task.get("attempt", 1)) == attempt)
                  and task.get("request_id")]
    return candidates[-1] if candidates else None


def current_video_attempt(manifest, segment_id):
    authorization = (manifest.get("video_attempts") or {}).get(str(segment_id)) or {}
    return int(authorization.get("attempt") or 1)


def request_video_attempt(manifest, segment_id, *, actor, reason, review=None):
    """Authorize a new paid attempt after an accepted or formally rejected take."""
    segment_id = str(segment_id)
    if not actor or not reason:
        raise ValueError("VIDEO_ATTEMPT_ACTOR_REASON_REQUIRED")
    accepted = (manifest.get("accepted_takes") or {}).get(segment_id)
    rejected = (review or {}).get("decision", {}).get("verdict") == "rejected"
    expected = (((manifest.get("handoffs") or {}).get("video") or {})
                .get("segments") or {}).get(segment_id)
    if not expected:
        raise ValueError("VIDEO_HANDOFF_REQUIRED")
    if not accepted and not rejected:
        raise ValueError("VIDEO_ATTEMPT_REQUIRES_ACCEPTED_OR_REJECTED_TAKE")
    if review:
        artifact = review.get("artifact") or {}
        if (str(review.get("segment_id")) != segment_id or
                artifact.get("video_handoff_fingerprint") != expected):
            raise ValueError("VIDEO_ATTEMPT_REVIEW_IDENTITY_MISMATCH")
    previous = max([int(task.get("attempt", 1)) for task in manifest.get("tasks", [])
                    if task.get("stage") == "video" and str(task.get("unit_id")) == segment_id]
                   + [current_video_attempt(manifest, segment_id)])
    item = {"attempt": previous + 1, "actor": actor, "reason": reason,
            "previous_take_fingerprint": (accepted or {}).get("take_fingerprint") or
            ((review or {}).get("artifact") or {}).get("take_fingerprint"),
            "authorized_at": datetime.now().isoformat(timespec="seconds")}
    manifest.setdefault("video_attempts", {})[segment_id] = item
    manifest.setdefault("accepted_takes", {}).pop(segment_id, None)
    manifest.setdefault("ocr_checks", {}).pop(segment_id, None)
    manifest.setdefault("ocr_waivers", {}).pop(segment_id, None)
    return item


def validate_video_closure(manifest):
    """Fail closed unless every formal video artifact and take review is current."""
    handoff = (manifest.get("handoffs") or {}).get("video") or {}
    artifact = manifest.get("video_artifact") or {}
    for name in ("segments", "results", "basecut", "reviews"):
        if not file_record_is_current(artifact.get(name)):
            raise ValueError("VIDEO_APPROVAL_ARTIFACT_REQUIRED_OR_STALE: %s" % name)
    if artifact.get("handoff_sha256") != handoff.get("sha256"):
        raise ValueError("VIDEO_APPROVAL_HANDOFF_MISMATCH")
    with open(artifact["segments"]["path"], encoding="utf-8") as handle:
        segments = json.load(handle).get("segments") or []
    with open(artifact["results"]["path"], encoding="utf-8") as handle:
        raw = json.load(handle)
        results = raw.get("results") if isinstance(raw, dict) else raw
    with open(artifact["reviews"]["path"], encoding="utf-8") as handle:
        reviews = json.load(handle)
    ids = {str(segment.get("id")) for segment in segments}
    if not ids or ids != set((handoff.get("segments") or {}).keys()) \
            or ids != set((manifest.get("accepted_takes") or {}).keys()) \
            or ids != set(map(str, (reviews or {}).keys())):
        raise ValueError("VIDEO_APPROVAL_CLOSURE_INCOMPLETE")
    by_id = {str(result.get("segment_id")): result for result in results or []}
    for segment in segments:
        sid = str(segment.get("id"))
        result, review = by_id.get(sid) or {}, reviews.get(sid) or {}
        accepted = manifest["accepted_takes"][sid]
        audio = segment.get("audio_contract") or {}
        authored_audio = [key for key in ("voice", "language", "bgm", "sfx")
                          if audio.get(key) not in (None, "", False)]
        if audio.get("lip_sync"):
            authored_audio.append("lip_sync")
        audio_review = ((review.get("quality") or {}).get("audio_contract") or {})
        if any(audio_review.get(key) is not True for key in authored_audio):
            raise ValueError("VIDEO_AUDIO_CONTRACT_REVIEW_REQUIRED: %s (%s)" %
                             (sid, ",".join(authored_audio)))
        if (not result.get("ok") or not (result.get("media_qc") or {}).get("passed") or
                result.get("take_fingerprint") != accepted.get("take_fingerprint") or
                not take_review.is_accepted(review, accepted.get("take_fingerprint")) or
                not ocr_take_is_clear_or_waived(manifest, sid, accepted.get("take_fingerprint"))):
            raise ValueError("VIDEO_APPROVAL_TAKE_CLOSURE_FAILED: %s" % sid)
    return True


def reconcile_tasks_from_ledger(manifest, ledger, **kwargs):
    """Restore manifest task state from durable ledger events."""
    import generation_ledger
    return generation_ledger.reconcile_manifest_tasks(manifest, ledger, **kwargs)


def record_video_handoff(manifest, segments_spec, path=None):
    segments = segments_spec.get("segments") if isinstance(segments_spec, dict) else segments_spec
    if not isinstance(segments_spec, dict):
        raise ValueError("VIDEO_HANDOFF_SPEC_REQUIRED")
    approval = segments_spec.get("storyboard_approval") or {}
    if approval.get("status") != "confirmed":
        raise ValueError("STORYBOARD_APPROVAL_REQUIRED")
    if (segments_spec.get("client") != manifest.get("client") or
            str(segments_spec.get("run_id")) != str(manifest.get("run_id"))):
        raise ValueError("VIDEO_HANDOFF_RUN_IDENTITY_MISMATCH")
    if (approval.get("client") != manifest.get("client") or
            str(approval.get("run_id")) != str(manifest.get("run_id"))):
        raise ValueError("STORYBOARD_APPROVAL_IDENTITY_MISMATCH")
    if segments_spec.get("missing_images") or segments_spec.get("needs_image") or not segments:
        raise ValueError("VIDEO_HANDOFF_INCOMPLETE")
    for segment in segments:
        if (segment.get("client") != manifest.get("client") or
                str(segment.get("run_id")) != str(manifest.get("run_id")) or
                segment.get("storyboard_approval") != approval):
            raise ValueError("VIDEO_HANDOFF_SEGMENT_IDENTITY_MISMATCH")
        if not verify_video_handoff(segment).get("ok"):
            raise ValueError("STALE_VIDEO_HANDOFF: %s" % segment.get("id"))
        validate_run_id(str(segment.get("id")))
        if segment.get("out_path"):
            require_run_output(manifest, segment["out_path"], label="segment_output")
    payload = {segment.get("id"): segment.get("video_handoff_fingerprint")
               for segment in segments or []}
    record = _file_record(path) if path else None
    if path and (not record or not record.get("exists")):
        raise ValueError("VIDEO_HANDOFF_FILE_MISSING: %s" % os.path.abspath(path))
    manifest.setdefault("handoffs", {})["video"] = {
        "path": os.path.abspath(path) if path else None,
        "file": record,
        "sha256": _digest(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        "segments": payload, "recorded_at": datetime.now().isoformat(timespec="seconds")}
    return manifest


def accept_take(manifest, segment_id, review, review_path=None):
    """Persist one content-bound accepted take; recommendations are not approvals."""
    if review.get("segment_id") != segment_id:
        raise ValueError("TAKE_SEGMENT_MISMATCH")
    artifact = review.get("artifact") or {}
    take_fingerprint = artifact.get("take_fingerprint")
    if not take_fingerprint:
        raise ValueError("TAKE_FINGERPRINT_REQUIRED")
    expected_handoff = ((manifest.get("handoffs") or {}).get("video") or {}).get("segments", {}).get(segment_id)
    if not expected_handoff or artifact.get("video_handoff_fingerprint") != expected_handoff:
        raise ValueError("VIDEO_HANDOFF_MISMATCH")
    if (not _take_review.artifact_is_current(review)
            or not _take_review.artifact_fingerprint_is_current(review)):
        raise ValueError("STALE_TAKE_ARTIFACT")
    if (review.get("decision") or {}).get("acceptance_mode") != "formal":
        raise ValueError("FORMAL_TAKE_REVIEW_REQUIRED")
    if not _take_review.is_accepted(review):
        raise ValueError("TAKE_REVIEW_REQUIRED: take 尚未通过验片")
    if not ocr_take_is_clear_or_waived(manifest, segment_id, take_fingerprint):
        raise ValueError("OCR_NOT_CLEAR_OR_WAIVED")
    item = {"segment_id": segment_id, "take_id": review.get("take_id"),
            "review_id": review.get("review_id"),
            "take_fingerprint": review.get("artifact", {}).get("take_fingerprint"),
            "video_handoff_fingerprint": review.get("artifact", {}).get("video_handoff_fingerprint"),
            "accepted_at": datetime.now().isoformat(timespec="seconds")}
    if review_path:
        item["review"] = _file_record(review_path)
        if not item["review"] or not item["review"].get("exists"):
            raise ValueError("TAKE_REVIEW_FILE_MISSING: %s" % os.path.abspath(review_path))
    manifest.setdefault("accepted_takes", {})[segment_id] = item
    return item


def record_ocr_result(manifest, segment_id, take_fingerprint, status, ocr_texts=(),
                      *, available=None, frames_checked=0, expected=0, error=None,
                      source="automated", reviewer=None, reason=None):
    """Persist OCR evidence for one exact accepted/generated take."""
    if not segment_id or not take_fingerprint:
        raise ValueError("OCR_IDENTITY_REQUIRED: segment_id/take_fingerprint 缺失")
    legacy_bool = isinstance(status, bool)
    if legacy_bool:
        status = "detected" if status else "unavailable"
    if status not in ("clear", "detected", "unavailable", "error"):
        raise ValueError("OCR_STATUS_INVALID")
    texts = [str(text) for text in (ocr_texts or [])]
    if status == "detected" and not texts:
        raise ValueError("OCR_DETECTED_TEXT_REQUIRED")
    if available is None:
        available = status in ("clear", "detected")
    if status in ("clear", "detected") and not available:
        raise ValueError("OCR_AVAILABILITY_CONFLICT")
    if status == "clear" and source == "automated" \
            and (int(expected) <= 0 or int(frames_checked) != int(expected)):
        raise ValueError("OCR_COVERAGE_INCOMPLETE")
    item = {
        "segment_id": str(segment_id), "take_fingerprint": str(take_fingerprint),
        "status": status, "available": bool(available),
        "frames_checked": int(frames_checked), "expected": int(expected),
        "error": error, "ocr_warning": status == "detected", "ocr_texts": texts,
        "legacy_boolean_input": legacy_bool,
        "source": source, "reviewer": reviewer, "reason": reason,
        "ocr_texts_sha256": _digest(json.dumps(texts, ensure_ascii=False, sort_keys=True)),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest.setdefault("ocr_checks", {})[str(segment_id)] = item
    return item


def record_manual_ocr_review(manifest, segment_id, take_fingerprint, status, *,
                              reviewer, reason, ocr_texts=(), frame_sha256s=()):
    """Persist attributed human OCR evidence bound to one immutable take."""
    import ocr_check
    record = ocr_check.manual_review(
        take_fingerprint, reviewer, reason, status, ocr_texts, frame_sha256s)
    item = record_ocr_result(
        manifest, segment_id, take_fingerprint, record["status"], record["texts"],
        available=True, frames_checked=record["frames_checked"],
        expected=record["expected"], source="manual",
        reviewer=record["reviewer"], reason=record["reason"])
    item["frame_sha256s"] = record["frame_sha256s"]
    item["first_frame_sha256"] = record["first_frame_sha256"]
    item["last_frame_sha256"] = record["last_frame_sha256"]
    return item


def grant_ocr_waiver(manifest, segment_id, take_fingerprint, ocr_texts, *, actor, reason):
    """Waive OCR only for the exact segment/take/text evidence currently recorded."""
    check = (manifest.get("ocr_checks") or {}).get(str(segment_id)) or {}
    texts = [str(text) for text in (ocr_texts or [])]
    if check.get("status") != "detected":
        raise ValueError("OCR_WAIVER_BLOCKED: 当前 take 没有 OCR warning")
    if check.get("take_fingerprint") != take_fingerprint:
        raise ValueError("OCR_WAIVER_TAKE_MISMATCH")
    if check.get("ocr_texts") != texts:
        raise ValueError("OCR_WAIVER_TEXT_MISMATCH")
    if not actor or not reason:
        raise ValueError("OCR_WAIVER_REASON_REQUIRED")
    waiver = {
        "segment_id": str(segment_id), "take_fingerprint": str(take_fingerprint),
        "ocr_texts": texts, "ocr_texts_sha256": check["ocr_texts_sha256"],
        "actor": actor, "reason": reason,
        "granted_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest.setdefault("ocr_waivers", {})[str(segment_id)] = waiver
    return waiver


def ocr_take_is_clear_or_waived(manifest, segment_id, take_fingerprint):
    """Validate clean OCR or an exact, non-transferable waiver for one take."""
    check = (manifest.get("ocr_checks") or {}).get(str(segment_id)) or {}
    if check.get("take_fingerprint") != take_fingerprint:
        return False
    automated_clear = (check.get("source") == "automated"
                       and check.get("frames_checked") == check.get("expected")
                       and check.get("expected", 0) > 0)
    manual_clear = (check.get("source") == "manual"
                    and bool(check.get("reviewer")) and bool(check.get("reason"))
                    and check.get("frames_checked") == check.get("expected")
                    and check.get("expected", 0) >= 12
                    and len(set(check.get("frame_sha256s") or [])) == check.get("expected"))
    if check.get("status") == "clear" and check.get("available") \
            and (automated_clear or manual_clear):
        return True
    if check.get("status") != "detected":
        return False
    waiver = (manifest.get("ocr_waivers") or {}).get(str(segment_id)) or {}
    return bool(
        waiver.get("take_fingerprint") == take_fingerprint
        and waiver.get("ocr_texts_sha256") == check.get("ocr_texts_sha256")
        and waiver.get("ocr_texts") == check.get("ocr_texts")
    )


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Create/update a resumable project run manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--client", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--script")
    create.add_argument("--plan")
    create.add_argument("--out", required=True)
    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--manifest", required=True)
    approve_cmd.add_argument("--stage", choices=STAGES, required=True)
    approve_cmd.add_argument("--reject", action="store_true")
    output_cmd = sub.add_parser("add-output")
    output_cmd.add_argument("--manifest", required=True)
    output_cmd.add_argument("--path", required=True)
    output_cmd.add_argument("--kind", default="file")
    gate_cmd = sub.add_parser("gate", help="检查某素材阶段是否允许开始生成")
    gate_cmd.add_argument("--manifest", required=True)
    gate_cmd.add_argument("--stage", choices=STAGES, required=True)
    start_cmd = sub.add_parser("start-stage", help="通过依赖门禁并登记阶段开始")
    start_cmd.add_argument("--manifest", required=True)
    start_cmd.add_argument("--stage", choices=STAGES, required=True)
    finish_cmd = sub.add_parser("finish-stage", help="登记阶段产物并等待客户确认")
    finish_cmd.add_argument("--manifest", required=True)
    finish_cmd.add_argument("--stage", choices=STAGES, required=True)
    finish_cmd.add_argument("--path", action="append", required=True)
    handoff_cmd = sub.add_parser("record-video-handoff", help="原子登记 segments 出片交接")
    handoff_cmd.add_argument("--manifest", required=True)
    handoff_cmd.add_argument("--segments", required=True)
    take_cmd = sub.add_parser("accept-take", help="登记已通过验片的精确 take")
    take_cmd.add_argument("--manifest", required=True)
    take_cmd.add_argument("--segment-id", required=True)
    take_cmd.add_argument("--review", required=True)
    attempt_cmd = sub.add_parser("new-video-attempt", help="验收/拒绝 take 后受控授权新 attempt")
    attempt_cmd.add_argument("--manifest", required=True)
    attempt_cmd.add_argument("--segment-id", required=True)
    attempt_cmd.add_argument("--actor", required=True)
    attempt_cmd.add_argument("--reason", required=True)
    attempt_cmd.add_argument("--review", help="正式 rejected review JSON；accepted take 可省略")
    status_cmd = sub.add_parser("status", help="输出 manifest 阶段状态")
    status_cmd.add_argument("--manifest", required=True)
    next_cmd = sub.add_parser("next", help="输出下一待处理阶段")
    next_cmd.add_argument("--manifest", required=True)
    bootstrap_cmd = sub.add_parser(
        "bootstrap-approval",
        help="把 manifest 创建前已有的 brief/script 登记为 pending_approval")
    bootstrap_cmd.add_argument("--manifest", required=True)
    bootstrap_cmd.add_argument("--stage", choices=("brief", "script"), required=True)
    bootstrap_cmd.add_argument("--path", action="append", required=True)
    ocr_cmd = sub.add_parser("record-ocr", help="登记一个精确 take 的 OCR 结果")
    ocr_cmd.add_argument("--manifest", required=True)
    ocr_cmd.add_argument("--segment-id", required=True)
    ocr_cmd.add_argument("--take-fingerprint", required=True)
    ocr_cmd.add_argument("--status", choices=("clear", "detected", "unavailable", "error"), required=True)
    ocr_cmd.add_argument("--available", action=argparse.BooleanOptionalAction, default=None)
    ocr_cmd.add_argument("--frames-checked", type=int, default=0)
    ocr_cmd.add_argument("--expected", type=int, default=0)
    ocr_cmd.add_argument("--error")
    ocr_cmd.add_argument("--text", action="append", default=[])
    waiver_cmd = sub.add_parser("waive-ocr", help="仅豁免指定 segment/take/OCR texts")
    waiver_cmd.add_argument("--manifest", required=True)
    waiver_cmd.add_argument("--segment-id", required=True)
    waiver_cmd.add_argument("--take-fingerprint", required=True)
    waiver_cmd.add_argument("--text", action="append", default=[])
    waiver_cmd.add_argument("--actor", required=True)
    waiver_cmd.add_argument("--reason", required=True)
    manual_ocr_cmd = sub.add_parser(
        "manual-ocr-review", help="登记绑定精确 take 的人工 OCR 复核")
    manual_ocr_cmd.add_argument("--manifest", required=True)
    manual_ocr_cmd.add_argument("--segment-id", required=True)
    manual_ocr_cmd.add_argument("--take-fingerprint", required=True)
    manual_ocr_cmd.add_argument("--status", choices=("clear", "detected"), required=True)
    manual_ocr_cmd.add_argument("--text", action="append", default=[])
    manual_ocr_cmd.add_argument("--frame-sha256", action="append", default=[])
    manual_ocr_cmd.add_argument("--reviewer", required=True)
    manual_ocr_cmd.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        if os.path.exists(args.out):
            raise SystemExit(
                "RUN_EXISTS: 这个项目记录已经存在。为避免覆盖历史确认和成片，"
                "请继续使用原记录恢复，或更换 --run-id/--out 创建新版本。"
            )
        manifest = create_manifest(args.client, args.run_id, script_path=args.script, plan_path=args.plan)
        save_manifest(manifest, args.out, create_only=True)
    else:
        manifest = load_manifest(args.manifest)
        if args.command == "approve":
            approve(manifest, args.stage, not args.reject, strict=True)
        elif args.command == "add-output":
            add_output(manifest, args.path, args.kind)
        elif args.command == "bootstrap-approval":
            bootstrap_pending_approval(manifest, args.stage, args.path)
        elif args.command == "start-stage":
            mark_generation_started(manifest, args.stage)
        elif args.command == "finish-stage":
            mark_generation_finished(manifest, args.stage, args.path)
        elif args.command == "record-video-handoff":
            with open(args.segments, encoding="utf-8") as handle:
                segments_spec = json.load(handle)
            if (segments_spec.get("client") != manifest.get("client") or
                    str(segments_spec.get("run_id")) != str(manifest.get("run_id"))):
                raise ValueError("VIDEO_HANDOFF_RUN_IDENTITY_MISMATCH")
            record_video_handoff(manifest, segments_spec, args.segments)
        elif args.command == "accept-take":
            with open(args.review, encoding="utf-8") as handle:
                review = json.load(handle)
            if str(review.get("segment_id")) != str(args.segment_id):
                raise ValueError("TAKE_REVIEW_SEGMENT_MISMATCH")
            expected = (((manifest.get("handoffs") or {}).get("video") or {})
                        .get("segments") or {}).get(args.segment_id)
            if (not expected or review.get("artifact", {}).get(
                    "video_handoff_fingerprint") != expected):
                raise ValueError("TAKE_REVIEW_HANDOFF_MISMATCH")
            accept_take(manifest, args.segment_id, review, args.review)
        elif args.command == "new-video-attempt":
            review = None
            if args.review:
                with open(args.review, encoding="utf-8") as handle:
                    review = json.load(handle)
            request_video_attempt(manifest, args.segment_id, actor=args.actor,
                                  reason=args.reason, review=review)
        elif args.command == "record-ocr":
            record_ocr_result(manifest, args.segment_id, args.take_fingerprint,
                              args.status, args.text, available=args.available,
                              frames_checked=args.frames_checked, expected=args.expected,
                              error=args.error)
        elif args.command == "waive-ocr":
            grant_ocr_waiver(manifest, args.segment_id, args.take_fingerprint,
                             args.text, actor=args.actor, reason=args.reason)
        elif args.command == "manual-ocr-review":
            record_manual_ocr_review(
                manifest, args.segment_id, args.take_fingerprint, args.status,
                reviewer=args.reviewer, reason=args.reason, ocr_texts=args.text,
                frame_sha256s=args.frame_sha256)
        elif args.command == "gate":
            generation_gate(manifest, args.stage)
            print(json.dumps({"ok": True, "stage": args.stage,
                              "dependencies": generation_dependencies(manifest, args.stage)},
                             ensure_ascii=False, indent=2))
            return 0
        elif args.command in ("status", "next"):
            stages = [{"stage": stage,
                       "approved": approval_is_current(manifest, stage),
                       "status": (manifest.get("generation", {}).get(stage) or {}).get("status")}
                      for stage in STAGES]
            pending = next((item for item in stages if not item["approved"]), None)
            output = pending if args.command == "next" else {
                "run_id": manifest.get("run_id"), "client": manifest.get("client"),
                "status": manifest.get("status"), "stages": stages,
                "next": pending,
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        save_manifest(manifest, args.manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    try:
        return _main(argv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "error_type": type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
