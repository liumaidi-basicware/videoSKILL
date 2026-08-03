import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import generation_ledger as ledger  # noqa: E402
import project_utils  # noqa: E402
import run_manifest  # noqa: E402


class FileLockTests(unittest.TestCase):
    def test_lock_times_out_and_owner_release_does_not_delete_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "resource.lock")
            owner = project_utils.FileLock(path, timeout=0.1).acquire()
            with self.assertRaises(project_utils.LockTimeoutError):
                project_utils.FileLock(
                    path, timeout=0.03, stale_after=None, poll_interval=0.005).acquire()
            with open(path, "w", encoding="ascii") as handle:
                json.dump({"token": "replacement"}, handle)
            owner.release()
            self.assertTrue(os.path.exists(path))

    def test_stale_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "resource.lock")
            with open(path, "w", encoding="ascii") as handle:
                handle.write("stale")
            old = time.time() - 60
            os.utime(path, (old, old))
            with project_utils.FileLock(
                    path, timeout=0.1, stale_after=1, poll_interval=0.005):
                self.assertTrue(os.path.isfile(path))
            self.assertFalse(os.path.exists(path))


class ManifestCASTests(unittest.TestCase):
    def test_revision_increments_and_stale_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            manifest = run_manifest.create_manifest("acme", "run1")
            run_manifest.save_manifest(manifest, path)
            self.assertEqual(manifest["revision"], 1)
            first = run_manifest.load_manifest(path)
            stale = run_manifest.load_manifest(path)
            first["status"] = "first"
            run_manifest.save_manifest(first, path)
            self.assertEqual(first["revision"], 2)
            stale["status"] = "stale"
            with self.assertRaises(run_manifest.ManifestConflictError):
                run_manifest.save_manifest(stale, path)
            self.assertEqual(run_manifest.load_manifest(path)["status"], "first")

    def test_legacy_manifest_without_revision_uses_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            manifest = run_manifest.create_manifest("acme", "run1")
            manifest.pop("revision")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            loaded = run_manifest.load_manifest(path)
            self.assertEqual(loaded["revision"], 0)
            run_manifest.save_manifest(loaded, path)
            self.assertEqual(loaded["revision"], 1)


class LedgerTests(unittest.TestCase):
    def test_submission_intent_reconciles_without_task_id(self):
        manifest = run_manifest.create_manifest("acme", "run1")
        events = [{"schema_version": 1, "event_id": "intent-1",
                   "timestamp": "2026-01-01T00:00:00", "event": "task_submitting",
                   "stage": "video", "unit_id": "s1", "handoff_fingerprint": "h1",
                   "attempt": 1, "model": "seedance-2.0", "request_id": "video-r1"}]
        run_manifest.reconcile_tasks_from_ledger(manifest, events)
        intent = run_manifest.find_submission_intent(manifest, "video", "s1", "h1")
        self.assertEqual(intent["request_id"], "video-r1")
        self.assertEqual(intent["status"], "submitting")
    def test_concurrent_appends_are_all_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            threads = [threading.Thread(
                target=ledger.append_event,
                args=(path, "task_submitted"),
                kwargs={"unit_id": "s%s" % index, "task_id": "t%s" % index})
                for index in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            events = ledger.read_events(path)
            self.assertEqual(len(events), 20)
            self.assertEqual(len({event["event_id"] for event in events}), 20)

    def test_middle_corruption_raises_but_truncated_tail_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            with open(path, "wb") as handle:
                handle.write(b'{"event":"task_submitted","task_id":"one"}\n')
                handle.write(b'{broken}\n')
                handle.write(b'{"event":"task_succeeded","task_id":"one"}\n')
            with self.assertRaisesRegex(ledger.LedgerCorruptionError, ":2"):
                ledger.read_events(path)
            with open(path, "wb") as handle:
                handle.write(b'{"event":"task_submitted","task_id":"one"}\n')
                handle.write(b'{"event":"task_suc')
            self.assertEqual(len(ledger.read_events(path)), 1)

    def test_append_repairs_truncated_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            with open(path, "wb") as handle:
                handle.write(b'{"event":"task_submitted","task_id":"one"}\n{"broken"')
            ledger.append_event(path, "task_succeeded", task_id="one")
            self.assertEqual([event["event"] for event in ledger.read_events(path)],
                             ["task_submitted", "task_succeeded"])

    def test_reconcile_replays_latest_task_state_and_preserves_fields(self):
        manifest = run_manifest.create_manifest("acme", "run1")
        run_manifest.upsert_task(manifest, {
            "stage": "video", "unit_id": "s1", "handoff_fingerprint": "h1",
            "task_id": "t1", "status": "submitted", "local_note": "keep"})
        events = [
            {"event": "task_resumed", "event_id": "e1", "timestamp": "one",
             "stage": "video", "unit_id": "s1", "task_id": "t1",
             "handoff_fingerprint": "h1"},
            {"event": "task_succeeded", "event_id": "e2", "timestamp": "two",
             "stage": "video", "unit_id": "s1", "task_id": "t1",
             "handoff_fingerprint": "h1", "video_url": "https://example/video"},
        ]
        changed = run_manifest.reconcile_tasks_from_ledger(manifest, events)
        self.assertEqual(len(changed), 2)
        self.assertEqual(len(manifest["tasks"]), 1)
        task = manifest["tasks"][0]
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["local_note"], "keep")
        self.assertEqual(task["ledger_event_id"], "e2")


if __name__ == "__main__":
    unittest.main()
