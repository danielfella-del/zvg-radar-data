import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("maintenance", HERE / "run_history_cleanup.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
OLD, NEW, OTHER = "1" * 40, "2" * 40, "3" * 40


class FakeAPI:
    def __init__(self, active=True):
        self.states = {10: "active" if active else "disabled_manually", 20: "disabled_manually"}
        self.calls = []
        self.runs = []

    def request(self, path, method="GET"):
        self.calls.append((method, path))
        if path == "/actions/runs/99":
            return {"workflow_id": 30}
        wid = int(path.split("/")[3])
        if path.endswith("/disable"):
            self.states[wid] = "disabled_manually"
        elif path.endswith("/enable"):
            self.states[wid] = "active"
        else:
            return {"state": self.states[wid]}

    def list_items(self, path, key):
        if key == "workflows":
            return [{"id": k, "path": f".github/workflows/{k}.yml", "state": s} for k, s in self.states.items()] + [{"id": 30}]
        return self.runs


class RunAPI(FakeAPI):
    def __init__(self, normal="stuck", force="complete"):
        super().__init__()
        self.normal, self.force = normal, force
        self.status = "queued"
        self.list_hides_pending = False

    def request(self, path, method="GET"):
        if path.startswith("/actions/runs/101"):
            self.calls.append((method, path))
            if path.endswith("/force-cancel"):
                if self.force == "conflict":
                    raise m.Halt("github_api_http_409")
                if self.force == "forbidden":
                    raise m.Halt("github_api_http_403")
                if self.force == "complete":
                    self.status = "completed"
            elif path.endswith("/cancel"):
                if self.normal == "conflict":
                    raise m.Halt("github_api_http_409")
                if self.normal == "completed_conflict":
                    self.status = "completed"
                    raise m.Halt("github_api_http_409")
                if self.normal == "complete":
                    self.status = "completed"
            else:
                return {"id": 101, "status": self.status}
            return None
        return super().request(path, method)

    def list_items(self, path, key):
        if key == "workflow_runs":
            if self.status == "completed" or self.list_hides_pending:
                return [{"id": 99, "status": "in_progress"}]
            return [{"id": 99, "status": "in_progress"}, {"id": 101, "status": self.status}]
        return super().list_items(path, key)


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.code = self.root / "code"
        self.code.mkdir()
        (self.code / "workflow-baseline.data").write_text(json.dumps({"version": 1, "workflows": {
            "10": {"path": ".github/workflows/10.yml", "state": "active"},
            "20": {"path": ".github/workflows/20.yml", "state": "disabled_manually"},
        }}))
        self.api = FakeAPI()
        self.job = m.Maintenance(self.code, self.root / "work", "synthetic-token", 99, OLD, "apply", "a" * 64, "b" * 64, self.api)
        self.before = {"refs/heads/main": OLD, "refs/tags/v1": OTHER, "refs/pull/1/head": OLD}
        self.after = {"refs/heads/main": NEW, "refs/tags/v1": OTHER, "refs/pull/1/head": NEW}
        self.job.measure_push_pack = lambda **kwargs: {
            "bytes": 0, "complete": True,
            "target_oids": sorted(set(m.owned_refs(self.job.after).values())),
            "expected_remote_refs_sha256": m.refs_digest(self.job.expected_staged_snapshot())}
        self.job.manifest = {"blob_ids": [OTHER], "json_blobs": {OLD: {"new_sha": NEW, "mode": "fast", "changed": True}}}

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_is_order_independent_and_exact(self):
        self.assertEqual(m.refs_digest(self.before), m.refs_digest(dict(reversed(list(self.before.items())))))
        self.assertNotEqual(m.refs_digest(self.before), m.refs_digest(self.after))
        self.assertEqual(m.parse_refs(m.canonical_refs(self.before)), self.before)

    def test_unknown_ref_namespace_rejected(self):
        with self.assertRaisesRegex(m.Halt, "unexpected_ref_namespace"):
            m.parse_refs(f"{OLD}\trefs/replace/{NEW}\n".encode())

    def test_atomic_push_has_explicit_lease_for_every_owned_ref(self):
        args = m.push_arguments(self.before, self.after)
        self.assertIn("--atomic", args)
        self.assertIn(f"--force-with-lease=refs/heads/main:{OLD}", args)
        self.assertIn(f"{NEW}:refs/heads/main", args)
        self.assertFalse(any("refs/pull/" in x for x in args))
        self.assertNotIn("--mirror", args)
        self.assertNotIn("--force", args)

    def test_missing_or_added_branch_rejected(self):
        for result in ({"refs/heads/main": NEW}, dict(self.after, **{"refs/heads/new": NEW})):
            with self.assertRaisesRegex(m.Halt, "owned_ref_set_changed"):
                m.push_arguments(self.before, result)

    def test_credentials_are_environment_only_and_trace_disabled(self):
        with patch.dict(os.environ, {"GIT_TRACE": "1", "GIT_CURL_VERBOSE": "1"}):
            env = m.auth_environment("private-test-token")
        self.assertNotIn("GIT_TRACE", env)
        self.assertNotIn("GIT_CURL_VERBOSE", env)
        self.assertNotIn("private-test-token", " ".join(m.push_arguments(self.before, self.after)))

    def test_only_formerly_active_workflows_resume(self):
        self.job.pause_workflows()
        self.assertEqual(self.api.states, {10: "disabled_manually", 20: "disabled_manually"})
        self.job.state["push_verified"] = True
        self.job.restore_workflows()
        self.assertEqual(self.api.states, {10: "active", 20: "disabled_manually"})

    def test_prepaused_active_baseline_survives_separate_preflight_run(self):
        self.api.states[10] = "disabled_manually"
        self.job.pause_workflows()
        self.assertEqual(self.job.state["resume_after_success"], [10])

    def drain_with_clock(self, api):
        self.job.api = api
        clock = Clock()
        with patch.object(m.time, "monotonic", clock.monotonic), patch.object(m.time, "sleep", clock.sleep):
            self.job.drain_runs()
        return clock

    def test_ordinary_cancel_completes_without_force_or_self_cancel(self):
        api = RunAPI(normal="complete")
        clock = self.drain_with_clock(api)
        self.assertLess(clock.now, 60)
        self.assertEqual(self.job.state["cancel_requested_run_ids"], [101])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [101])
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [])
        self.assertFalse(any(method == "POST" and "/99/" in path for method, path in api.calls))

    def test_stuck_run_force_cancelled_only_after_grace_and_verified(self):
        api = RunAPI()
        clock = self.drain_with_clock(api)
        self.assertGreaterEqual(clock.now, 60)
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [101])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [101])
        writes = [path for method, path in api.calls if method == "POST"]
        self.assertEqual(writes, ["/actions/runs/101/cancel", "/actions/runs/101/force-cancel"])

    def test_ordinary_409_uses_force_after_authoritative_nonterminal_check(self):
        api = RunAPI(normal="conflict")
        clock = self.drain_with_clock(api)
        self.assertLess(clock.now, 60)
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [101])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [101])

    def test_completion_race_on_409_does_not_force_cancel(self):
        api = RunAPI(normal="completed_conflict")
        self.drain_with_clock(api)
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [101])

    def test_force_409_is_bounded_and_does_not_ignore_stuck_run(self):
        api = RunAPI(normal="conflict", force="conflict")
        with self.assertRaisesRegex(m.Halt, "other_runs_did_not_stop"):
            self.drain_with_clock(api)
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [101])
        self.assertEqual(self.job.state["force_cancel_conflict_run_ids"], [101])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [])
        self.assertEqual(sum(path.endswith("/force-cancel") for _, path in api.calls), 1)
        self.assertFalse(any(method == "DELETE" for method, _ in api.calls))

    def test_force_permission_failure_halts_without_resume(self):
        api = RunAPI(normal="conflict", force="forbidden")
        with self.assertRaisesRegex(m.Halt, "github_api_http_403"):
            self.drain_with_clock(api)
        self.assertFalse(any(path.endswith("/enable") for _, path in api.calls))

    def test_filtered_list_cannot_hide_still_running_cancelled_job(self):
        api = RunAPI(normal="stuck")
        original = api.request
        def request(path, method="GET"):
            result = original(path, method)
            if path.endswith("/cancel"):
                api.list_hides_pending = True
            return result
        api.request = request
        self.drain_with_clock(api)
        self.assertEqual(self.job.state["force_cancel_requested_run_ids"], [101])
        self.assertEqual(self.job.state["confirmed_stopped_run_ids"], [101])

    def test_unverified_push_cannot_resume(self):
        with self.assertRaisesRegex(m.Halt, "restore_without_verified_push"):
            self.job.restore_workflows()
        self.assertFalse(any(x[1].endswith("/enable") for x in self.api.calls))

    def test_unknown_new_workflow_aborts_before_any_disable(self):
        self.api.states[40] = "active"
        with self.assertRaisesRegex(m.Halt, "workflow_set_changed"):
            self.job.pause_workflows()
        self.assertFalse(any(x[1].endswith("/disable") for x in self.api.calls))

    def test_json_changes_require_exact_hash_and_mode_preservation(self):
        self.job.verify_tree_change(f":100644 100644 {OLD} {NEW} M\0data.json\0".encode())
        for newmode, path, newsha in (("100755", "data.json", NEW), ("100644", "code.py", NEW), ("100644", "data.json", OTHER)):
            with self.assertRaises(m.Halt):
                self.job.verify_tree_change(f":100644 {newmode} {OLD} {newsha} M\0{path}\0".encode())

    def test_deletion_scope_is_only_approved_blob_ids(self):
        self.job.verify_tree_change(f":100644 000000 {OTHER} {m.ZERO} D\0private.pdf\0".encode())
        with self.assertRaisesRegex(m.Halt, "unapproved_file_deletion"):
            self.job.verify_tree_change(f":100644 000000 {OLD} {m.ZERO} D\0other.pdf\0".encode())

    def test_audit_coverage_and_hash_mismatch_rejected(self):
        audit = self.job.work / "audit.jsonl"
        audit.write_text("")
        with self.assertRaisesRegex(m.Halt, "audit_coverage_mismatch"):
            self.job.verify_audit()
        audit.write_text(json.dumps({"old_sha": OLD, "new_sha": OTHER, "mode": "fast", "changed": True}) + "\n")
        with self.assertRaisesRegex(m.Halt, "audit_hash_mismatch"):
            self.job.verify_audit()
        audit.write_text(json.dumps({"old_sha": OLD, "new_sha": NEW, "mode": "fast", "changed": True}) + "\n")
        self.job.verify_audit()

    def test_ref_race_blocks_push_and_restore(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.after
        self.job.git = lambda *a, **k: self.fail("push should not be reached")
        with self.assertRaisesRegex(m.Halt, "remote_refs_changed_before_push"):
            self.job.publish()
        self.assertFalse(self.job.state["push_verified"])

    def test_failure_after_pause_stays_paused_and_report_contains_no_token(self):
        self.job.load_manifest = lambda: None
        self.job.precheck_approved_refs = lambda: None
        self.job.drain_runs = lambda: None
        self.job.clone = lambda: (_ for _ in ()).throw(m.Halt("synthetic_failure"))
        self.assertEqual(self.job.execute(), 1)
        self.assertEqual(self.api.states[10], "disabled_manually")
        report = (self.job.evidence / "state.json").read_text()
        self.assertNotIn("synthetic-token", report)
        self.assertIn("synthetic_failure", report)

    def test_stale_approval_fails_before_any_actions_mutation(self):
        self.job.load_manifest = lambda: None
        self.job.refs_hash = m.refs_digest(self.before)
        self.job.remote_refs = lambda: self.after
        self.assertEqual(self.job.execute(), 1)
        self.assertEqual(self.job.state["error_code"], "approved_refs_mismatch")
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.api.states[10], "active")
        self.assertEqual(self.job.state["paused_workflow_ids"], [])

    def test_stale_dispatch_sha_fails_before_any_actions_mutation(self):
        self.job.load_manifest = lambda: None
        self.job.refs_hash = m.refs_digest(self.after)
        self.job.remote_refs = lambda: self.after
        self.assertEqual(self.job.execute(), 1)
        self.assertEqual(self.job.state["error_code"], "workflow_sha_is_not_approved_main")
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.api.states[10], "active")

    def test_preflight_never_pushes_or_resumes(self):
        self.job.mode = "preflight"
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.before
        self.job.git = lambda *a, **k: self.fail("preflight cannot push")
        self.job.publish()
        self.assertFalse(self.job.state["push_verified"])

    def test_manifest_rejects_extra_private_fields(self):
        manifest = {"version": 1, "id_hashes": ["a" * 64], "blob_ids": [OTHER],
                    "json_blobs": self.job.manifest["json_blobs"]}
        for extra in (True, False):
            data = dict(manifest)
            if extra:
                data["private_markers"] = ["synthetic sensitive value"]
            raw = json.dumps(data).encode()
            self.job.manifest_hash = hashlib.sha256(raw).hexdigest()
            (self.code / "approved-manifest.b64").write_bytes(base64.b64encode(gzip.compress(raw)))
            if extra:
                with self.assertRaisesRegex(m.Halt, "unexpected_manifest_fields"):
                    self.job.load_manifest()
            else:
                self.job.load_manifest()


class PushDiagnosticTests(unittest.TestCase):
    setUp = GuardTests.setUp
    tearDown = GuardTests.tearDown

    def test_error_signals_do_not_copy_sensitive_text(self):
        samples = {
            "workflow_permission_denied": b"remote: refusing to allow a GitHub App to create or update workflow `.github/workflows/private-name.yml` without `workflows` permission",
            "pack_size_limit": b"remote: fatal: pack exceeds maximum allowed size",
            "file_size_limit": b"remote: GH001: Large files detected.",
            "repository_rule_rejected": b"remote: error: GH013: Repository rule violations found for refs/heads/private-branch.",
            "lease_rejected": b"!\trefs/heads/private:refs/heads/private\t[rejected] (stale info)",
            "atomic_push_unsupported": b"fatal: the receiving end does not support --atomic push",
            "atomic_push_rejected": b"[remote rejected] (atomic push failure)",
            "authentication_failed": b"fatal: Authentication failed for 'https://private-token@github.com/private/repo'",
            "repository_write_denied": b"remote: Write access to repository not granted.",
            "transport_failed": b"fatal: the remote end hung up unexpectedly",
            "local_disk_full": b"fatal: cannot write private-file: No space left on device",
            "mirror_refspec_conflict": b"fatal: --mirror can't be combined with refspecs",
        }
        for expected, message in samples.items():
            with self.subTest(signal=expected):
                report = m.push_diagnostics(128, b"private-stdout", message + b" secret-token private-person")
                self.assertIn(expected, report["observed_signals"])
                encoded = json.dumps(report)
                for secret in ("private", "secret-token", "github.com", "refusing", "refs/heads"):
                    self.assertNotIn(secret, encoded)
                self.assertEqual(report["returncode"], 128)

    def test_unknown_error_is_not_returned_as_text(self):
        report = m.push_diagnostics(1, b"unknown private stdout", b"unknown private stderr")
        self.assertEqual(report["observed_signals"], ["unclassified"])
        self.assertEqual(report["stdout_bytes"], 22)
        self.assertEqual(report["stderr_bytes"], 22)
        self.assertNotIn("private", json.dumps(report))

    def test_bounded_scan_finds_server_error_at_tail(self):
        message = b"x" * (m.DIAGNOSTIC_SCAN_LIMIT * 3) + b"remote: fatal: pack exceeds maximum allowed size"
        report = m.push_diagnostics(1, stderr=message)
        self.assertEqual(report["observed_signals"], ["pack_size_limit"])
        self.assertEqual(report["stderr_bytes"], len(message))
        middle = b"x" * m.DIAGNOSTIC_SCAN_LIMIT + b"pack exceeds maximum allowed size" + b"x" * m.DIAGNOSTIC_SCAN_LIMIT
        self.assertEqual(m.push_diagnostics(1, stderr=middle)["observed_signals"], ["unclassified"])

    def test_subprocess_push_failure_saves_only_safe_diagnostics(self):
        result = subprocess.CompletedProcess(["git"], 128, b"private-stdout", b"remote: fatal: pack exceeds maximum allowed size secret-token")
        with patch.object(m.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(m.Halt, "git_operation_failed_128"):
                self.job.git(m.push_arguments(self.before, self.after))
        saved = (self.job.evidence / "state.json").read_text()
        self.assertIn("pack_size_limit", saved)
        self.assertNotIn("secret-token", saved)
        self.assertNotIn("private-stdout", saved)
        self.assertFalse(self.job.state["push_verified"])
        self.assertEqual(list(self.job.evidence.iterdir()), [self.job.evidence / "state.json"])

    def test_timeout_records_only_counts_and_fixed_signal(self):
        error = subprocess.TimeoutExpired(["git", "push"], 10, output=b"private-token", stderr=b"private-file")
        with patch.object(m.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(m.Halt, "git_operation_timeout"):
                self.job.git(m.push_arguments(self.before, self.after))
        report = self.job.state["push_diagnostics"]
        self.assertIsNone(report["returncode"])
        self.assertEqual(report["observed_signals"], ["process_timeout"])
        self.assertTrue(report["timed_out"])
        self.assertNotIn("private", (self.job.evidence / "state.json").read_text())

    def test_nonpush_failure_does_not_capture_other_command_output(self):
        result = subprocess.CompletedProcess(["git"], 1, b"private data", b"pack exceeds maximum allowed size")
        with patch.object(m.subprocess, "run", return_value=result):
            with self.assertRaises(m.Halt):
                self.job.git(["show", "HEAD:private.json"])
        self.assertNotIn("push_diagnostics", self.job.state)

    def test_diagnosed_push_failure_keeps_workflows_paused(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.before
        result = subprocess.CompletedProcess(["git"], 1, b"", b"remote: fatal: pack exceeds maximum allowed size")
        with patch.object(m.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(m.Halt, "push_failed_or_remote_outcome_unverified"):
                self.job.publish()
        self.assertFalse(self.job.state["push_verified"])
        self.assertFalse(self.job.state["restore_complete"])
        self.assertFalse(any(path.endswith("/enable") for _, path in self.api.calls))

    def test_diagnostics_never_replace_postpush_remote_verification(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        answers = iter([self.before, self.before, self.after, self.after])
        self.job.remote_refs = lambda: next(answers)
        result = subprocess.CompletedProcess(["git"], 1, b"", b"fatal: the remote end hung up unexpectedly")
        with patch.object(m.subprocess, "run", return_value=result):
            self.job.publish()
        self.assertTrue(self.job.state["push_verified"])
        self.assertTrue(self.job.state["restore_complete"])
        self.assertTrue(self.job.state["push_transport_error_but_remote_verified"])
        self.assertEqual(self.job.state["push_diagnostics"]["observed_signals"], ["transport_failed"])


class TransportMeasurementTests(unittest.TestCase):
    setUp = GuardTests.setUp
    tearDown = GuardTests.tearDown

    def test_transport_codes_are_bounded_numbers_not_error_text(self):
        report = m.push_diagnostics(1, b"Done\n", b"error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\nsend-pack: unexpected disconnect while reading sideband packet\nfatal: the remote end hung up unexpectedly\nprivate-name private-token")
        self.assertEqual(report["http_statuses"], [500])
        self.assertEqual(report["curl_codes"], [22])
        self.assertEqual(report["observed_signals"], ["sideband_disconnected", "transport_failed"])
        self.assertNotIn("private", json.dumps(report))
        report = m.push_diagnostics(1, stderr=b"error: RPC failed; curl 92 HTTP/2 stream 0 was not closed cleanly: INTERNAL_ERROR")
        self.assertEqual(report["curl_codes"], [92])
        self.assertIn("http2_transport_failed", report["observed_signals"])
        self.assertEqual(report["http_statuses"], [])
        report = m.push_diagnostics(1, stderr=b"some arbitrary private HTTP 500 curl 22 text\nerror: RPC failed; HTTP 999 curl 999")
        self.assertEqual(report["http_statuses"], [])
        self.assertEqual(report["curl_codes"], [])

    def test_pack_revision_input_has_only_owned_tips_and_owned_exclusions(self):
        before = dict(self.before, **{"refs/pull/2/head": "4" * 40})
        after = dict(self.after, **{"refs/pull/2/head": "5" * 40})
        lines = m.pack_revisions(before, after).decode().splitlines()
        self.assertEqual(set(lines), {NEW, OTHER, "^" + OLD, "^" + OTHER})
        self.assertNotIn("^" + "4" * 40, lines)
        self.assertNotIn("5" * 40, lines)
        with self.assertRaisesRegex(m.Halt, "invalid_pack_revision"):
            m.pack_revisions(before, dict(after, **{"refs/heads/main": "private-invalid"}))

    def real_repository(self):
        env = os.environ.copy()
        env.update({"GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "test@example.invalid"})
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.job.repo, env=env, stderr=subprocess.DEVNULL)
        self.job.repo.mkdir()
        git("init", "-b", "main")
        (self.job.repo / "unchanged.txt").write_text("synthetic old data")
        git("add", ".")
        git("commit", "-qm", "Synthetic initial")
        old = git("rev-parse", "HEAD").decode().strip()
        (self.job.repo / "new.txt").write_text("synthetic sanitized new data")
        git("add", ".")
        git("commit", "-qm", "Synthetic new")
        new = git("rev-parse", "HEAD").decode().strip()
        self.job.before = {"refs/heads/main": old, "refs/pull/1/head": old}
        self.job.after = {"refs/heads/main": new, "refs/pull/1/head": new}
        return git

    def test_full_traversal_is_identical_for_measurement_and_push(self):
        env = m.auth_environment("synthetic-token")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "6")
        self.assertEqual(env["GIT_CONFIG_KEY_5"], "pack.useSparse")
        self.assertEqual(env["GIT_CONFIG_VALUE_5"], "false")
        result = subprocess.CompletedProcess(["git"], 0, b"", b"")
        with patch.object(m.subprocess, "run", return_value=result) as run:
            self.job.git(m.push_arguments(self.before, self.after))
        self.assertEqual(run.call_args.kwargs["env"]["GIT_CONFIG_VALUE_5"], "false")
        self.real_repository()
        original = m.subprocess.Popen
        observed = []
        def launch(*args, **kwargs):
            observed.append(kwargs["env"])
            return original(*args, **kwargs)
        with patch.object(m.subprocess, "Popen", side_effect=launch):
            m.Maintenance.measure_push_pack(self.job)
        self.assertTrue(observed)
        self.assertTrue(all(env["GIT_CONFIG_VALUE_5"] == "false" for env in observed))
        record = self.job.state["push_pack_measurement"]
        self.assertTrue(record["full_object_traversal"])
        self.assertEqual(record["exclusion_basis"], "reviewed_old_owned_and_verified_current_stage_refs")

    def test_full_traversal_remains_conservative_for_historical_binary(self):
        # Recreate a history rewrite with a large unchanged blob present only in
        # historical trees. Full traversal can still resend it despite old history
        # already being reachable remotely. All data here is synthetic.
        self.job.repo.mkdir()
        env = dict(self.job.env, GIT_AUTHOR_NAME="Synthetic", GIT_AUTHOR_EMAIL="test@example.invalid",
                   GIT_COMMITTER_NAME="Synthetic", GIT_COMMITTER_EMAIL="test@example.invalid")
        def git(*args, input=None, override=None):
            return subprocess.check_output(["git", *args], cwd=self.job.repo, env=override or env,
                                           input=input, stderr=subprocess.DEVNULL)
        git("init", "-b", "main")
        (self.job.repo / "base.txt").write_text("shared base")
        git("add", ".")
        git("commit", "-qm", "Synthetic common root")
        common = git("rev-parse", "HEAD").decode().strip()
        shared = os.urandom(1024 * 1024)
        for label in ("original", "rewritten"):
            if label == "rewritten":
                git("checkout", "-qb", "rewritten", common)
            (self.job.repo / "historic.bin").write_bytes(shared)
            (self.job.repo / "data.json").write_text('{"synthetic":"' + label + ' one"}')
            git("add", ".")
            git("commit", "-qm", "Synthetic historical object " + label)
            (self.job.repo / "historic.bin").unlink()
            (self.job.repo / "data.json").write_text('{"synthetic":"' + label + ' two"}')
            git("add", "-A")
            git("commit", "-qm", "Synthetic current " + label)
            if label == "original":
                old = git("rev-parse", "HEAD").decode().strip()
            else:
                new = git("rev-parse", "HEAD").decode().strip()
        self.job.before, self.job.after = {"refs/heads/main": old}, {"refs/heads/main": new}
        revisions = m.pack_revisions(self.job.before, self.job.after)
        args = ("pack-objects", "--revs", "--stdout", "--thin", "--delta-base-offset", "--quiet")
        full = git(*args, input=revisions)
        sparse = git(*args, input=revisions, override=dict(env, GIT_CONFIG_VALUE_5="true"))
        self.assertGreater(len(full), 1024 * 1024)
        self.assertGreaterEqual(len(sparse), len(full))
        self.assertLessEqual(int.from_bytes(full[8:12], "big"), int.from_bytes(sparse[8:12], "big"))
        m.Maintenance.measure_push_pack(self.job)
        self.assertEqual(self.job.state["push_pack_measurement"]["bytes"], len(full))
        self.assertEqual(list((self.job.repo / ".git/objects/pack").iterdir()), [])

    def test_real_pack_stream_count_matches_reference_without_saved_pack(self):
        git = self.real_repository()
        m.Maintenance.measure_push_pack(self.job)
        observed = self.job.state["push_pack_measurement"]
        reference = subprocess.check_output(["git", "pack-objects", "--revs", "--stdout", "--thin", "--delta-base-offset", "--quiet"],
            cwd=self.job.repo, env=self.job.env, input=m.pack_revisions(self.job.before, self.job.after))
        self.assertEqual(observed["bytes"], len(reference))
        self.assertEqual(observed["object_count"], int.from_bytes(reference[8:12], "big"))
        self.assertTrue(observed["complete"])
        self.assertTrue(observed["is_offline_estimate"])
        self.assertTrue(observed["receive_pack_advertisement_unverified"])
        self.assertEqual(list(self.job.evidence.iterdir()), [self.job.evidence / "state.json"])
        self.assertEqual(list((self.job.repo / ".git/objects/pack").iterdir()), [])
        self.assertNotIn("sanitized new data", (self.job.evidence / "state.json").read_text())

    def test_oversize_measurement_limit_halts_without_push(self):
        self.real_repository()
        self.job.measure_push_pack = lambda **kwargs: m.Maintenance.measure_push_pack(self.job, **kwargs)
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.job.before
        self.job.git = lambda *args, **kwargs: self.fail("oversized pack must not push")
        with patch.object(m, "PUSH_PACK_LIMIT", 1):
            with self.assertRaisesRegex(m.Halt, "estimated_push_pack_exceeds_limit"):
                self.job.measure_push_pack()
        self.assertTrue(self.job.state["push_pack_measurement"]["complete"])
        self.assertFalse(self.job.state["push_verified"])
        self.assertFalse(self.job.state["restore_complete"])

    def test_pack_subprocess_failure_reports_no_raw_error(self):
        self.job.repo.mkdir()
        self.job.before, self.job.after = self.before, self.after
        with self.assertRaisesRegex(m.Halt, "push_pack_measurement_failed"):
            m.Maintenance.measure_push_pack(self.job)
        report = self.job.state["push_pack_measurement"]
        self.assertFalse(report["complete"])
        self.assertNotEqual(report["returncode"], 0)
        self.assertNotIn("not a git repository", (self.job.evidence / "state.json").read_text())

    def test_pack_timeout_reaps_subprocess_and_does_not_push(self):
        self.real_repository()
        processes = []
        original = m.subprocess.Popen
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(m.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(m.Halt, "push_pack_measurement_timeout"):
                m.Maintenance.measure_push_pack(self.job, timeout=0)
        self.assertTrue(processes)
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0].stdout.closed)
        self.assertFalse(self.job.state["push_pack_measurement"]["complete"])

    def test_preflight_measures_but_never_pushes(self):
        self.real_repository()
        self.job.mode = "preflight"
        self.job.measure_push_pack = lambda **kwargs: m.Maintenance.measure_push_pack(self.job, **kwargs)
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.job.before
        self.job.git = lambda *args, **kwargs: self.fail("preflight must not push")
        self.job.publish()
        self.assertTrue(self.job.state["push_pack_measurement"]["complete"])
        self.assertFalse(self.job.state["push_verified"])

    def test_new_writer_after_measurement_blocks_push(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        answers = iter([{}, {101: {"status": "in_progress"}}])
        self.job.pending_runs = lambda: next(answers)
        self.job.remote_refs = lambda: self.before
        self.job.git = lambda *args, **kwargs: self.fail("new writer must block push")
        with self.assertRaisesRegex(m.Halt, "new_run_appeared"):
            self.job.publish()

    def test_invalid_initial_refs_block_even_measurement(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.remote_refs = lambda: self.after
        self.job.measure_push_pack = lambda: self.fail("initial guards must precede sizing")
        with self.assertRaisesRegex(m.Halt, "remote_refs_changed_before_push"):
            self.job.publish()

    def test_pack_spawn_error_preserves_fixed_failure_and_no_private_path(self):
        self.job.before, self.job.after = self.before, self.after
        with patch.object(m.subprocess, "Popen", side_effect=OSError("private path secret token")):
            with self.assertRaisesRegex(m.Halt, "push_pack_measurement_io_failure"):
                m.Maintenance.measure_push_pack(self.job)
        report = (self.job.evidence / "state.json").read_text()
        self.assertNotIn("private path", report)
        self.assertNotIn("secret token", report)
        self.assertFalse(self.job.state["push_pack_measurement"]["complete"])

    def test_ref_change_during_measurement_blocks_push(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        answers = iter([self.before, self.after])
        self.job.remote_refs = lambda: next(answers)
        self.job.git = lambda *args, **kwargs: self.fail("changed refs must block push")
        with self.assertRaisesRegex(m.Halt, "remote_refs_changed_before_push"):
            self.job.publish()


class StagingGuardTests(unittest.TestCase):
    setUp = GuardTests.setUp
    tearDown = GuardTests.tearDown

    def ready(self):
        self.job.before, self.job.after = self.before, self.after
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        self.job.verified_commit_ids = {NEW, OTHER}
        return m.STAGE_REF_PREFIX + "99-0"

    def measurement(self, *, target=NEW, size=10):
        return {"complete": True, "bytes": size, "target_oids": [target],
                "expected_remote_refs_sha256": m.refs_digest(self.job.expected_staged_snapshot())}

    def test_prior_stage_ref_blocks_before_pause_even_with_matching_approval(self):
        observed = dict(self.before, **{m.STAGE_REF_PREFIX + "98-0": OTHER})
        self.job.load_manifest = lambda: None
        self.job.remote_refs = lambda: observed
        self.job.refs_hash = m.refs_digest(observed)
        self.assertEqual(self.job.execute(), 1)
        self.assertEqual(self.job.state["error_code"], "staging_recovery_required")
        self.assertEqual(self.api.calls, [])

    def test_preflight_oversize_cannot_stage_or_restore(self):
        self.ready()
        self.job.mode = "preflight"
        self.job.remote_refs = lambda: self.before
        self.job.measure_push_pack = lambda **kw: {"complete": True, "bytes": m.PUSH_PACK_LIMIT * 4}
        self.job.git = lambda *a, **kw: self.fail("preflight may not write")
        self.job.publish()
        self.assertFalse(self.job.stages)
        self.assertFalse(self.job.state["publication_journal"])
        self.assertFalse(self.job.state["restore_complete"])

    def test_measured_revision_excludes_only_current_stages_and_owned_baseline(self):
        stages = {m.STAGE_REF_PREFIX + "99-0": "4" * 40,
                  m.STAGE_REF_PREFIX + "99-1": "5" * 40}
        before = dict(self.before, **{"refs/pull/2/head": "6" * 40})
        after = dict(self.after, **{"refs/pull/2/head": "7" * 40})
        lines = set(m.pack_revisions(before, after, stages, [NEW]).decode().splitlines())
        self.assertEqual(lines, {NEW, "^" + OLD, "^" + OTHER, "^" + "4" * 40, "^" + "5" * 40})

    def test_final_arguments_keep_original_leases_and_delete_every_stage(self):
        stages = {m.STAGE_REF_PREFIX + "99-0": "4" * 40, m.STAGE_REF_PREFIX + "99-1": "5" * 40}
        args = m.final_push_arguments(self.before, self.after, stages)
        self.assertIn("--atomic", args)
        for ref, oid in m.owned_refs(self.before).items():
            self.assertIn(f"--force-with-lease={ref}:{oid}", args)
            self.assertIn(f"{self.after[ref]}:{ref}", args)
        for ref, oid in stages.items():
            self.assertIn(f"--force-with-lease={ref}:{oid}", args)
            self.assertIn(":" + ref, args)
        self.assertFalse(any("refs/pull/" in value for value in args))

    def test_unverified_stage_commit_or_oversize_measurement_cannot_write(self):
        ref = self.ready()
        self.job.git = lambda *a, **kw: self.fail("invalid stage may not write")
        with self.assertRaisesRegex(m.Halt, "unverified_staging_commit"):
            self.job.stage_checkpoint(ref, OLD, self.measurement())
        with self.assertRaisesRegex(m.Halt, "estimated_stage_pack_exceeds_limit"):
            self.job.stage_checkpoint(ref, NEW, self.measurement(size=m.STAGE_PACK_LIMIT))
        self.assertFalse(self.job.state["publication_journal"])

    def test_ancestor_failure_blocks_stage_before_write(self):
        ref = self.ready()
        self.job.stages[ref] = OTHER
        def git(args, **kw):
            self.assertEqual(args, ["merge-base", "--is-ancestor", OTHER, NEW])
            raise m.Halt("git_operation_failed_1")
        self.job.git = git
        with self.assertRaisesRegex(m.Halt, "staging_checkpoint_not_descendant"):
            self.job.stage_checkpoint(ref, NEW, self.measurement())
        self.assertEqual(self.job.stages, {ref: OTHER})

    def test_wrong_target_or_stale_snapshot_measurement_blocks_stage(self):
        ref = self.ready()
        self.job.git = lambda *a, **kw: self.fail("unbound measurement may not write")
        with self.assertRaisesRegex(m.Halt, "stage_measurement_target_or_snapshot_mismatch"):
            self.job.stage_checkpoint(ref, NEW, self.measurement(target=OTHER))
        measurement = self.measurement()
        self.job.stages[m.STAGE_REF_PREFIX + "99-1"] = OTHER
        with self.assertRaisesRegex(m.Halt, "stage_measurement_target_or_snapshot_mismatch"):
            self.job.stage_checkpoint(ref, NEW, measurement)
        self.assertFalse(self.job.state["publication_journal"])

    def test_final_measurement_must_cover_all_owned_targets_and_current_stages(self):
        self.ready()
        self.job.remote_refs = lambda: self.job.expected_staged_snapshot()
        self.job.git = lambda *a, **kw: self.fail("unbound final measurement may not write")
        for change in ({"target_oids": [NEW]}, {"expected_remote_refs_sha256": "0" * 64}):
            measurement = {"bytes": 10, "complete": True,
                "target_oids": sorted(set(m.owned_refs(self.after).values())),
                "expected_remote_refs_sha256": m.refs_digest(self.before)}
            measurement.update(change)
            self.job.measure_push_pack = lambda **kw: measurement
            with self.assertRaisesRegex(m.Halt, "final_measurement_target_or_snapshot_mismatch"):
                self.job.publish()
        self.assertFalse(self.job.state["publication_journal"])

    def test_pr_ref_race_after_measurement_blocks_stage(self):
        ref = self.ready()
        self.job.remote_refs = lambda: dict(self.before, **{"refs/pull/1/head": OTHER})
        self.job.git = lambda *a, **kw: self.fail("PR race must block stage")
        with self.assertRaisesRegex(m.Halt, "remote_refs_changed_before_push"):
            self.job.stage_checkpoint(ref, NEW, self.measurement())
        self.assertFalse(self.job.state["publication_journal"])

    def test_pending_writer_or_unpaused_workflow_blocks_stage(self):
        ref = self.ready()
        self.job.git = lambda *a, **kw: self.fail("writer must block stage")
        self.job.pending_runs = lambda: {101: {"status": "queued"}}
        with self.assertRaisesRegex(m.Halt, "new_run_appeared"):
            self.job.stage_checkpoint(ref, NEW, self.measurement())
        self.job.pending_runs = lambda: {}
        self.job.verify_paused = lambda: (_ for _ in ()).throw(m.Halt("workflow_not_paused"))
        with self.assertRaisesRegex(m.Halt, "workflow_not_paused"):
            self.job.stage_checkpoint(ref, NEW, self.measurement())

    def test_stage_failure_outcomes_are_proven_and_journalled_before_write(self):
        for outcome in ("accepted", "unchanged", "divergent", "unreadable"):
            with self.subTest(outcome=outcome):
                self.job.stages = {}
                self.job.state["publication_journal"] = []
                ref = self.ready()
                count = 0
                def remote():
                    nonlocal count
                    count += 1
                    if count == 1 or outcome == "unchanged":
                        return self.before
                    if outcome == "unreadable":
                        raise m.Halt("git_operation_failed_1")
                    if outcome == "accepted":
                        return dict(self.before, **{ref: NEW})
                    return dict(self.before, **{ref: OTHER})
                self.job.remote_refs = remote
                def git(args, **kw):
                    saved = json.loads((self.job.evidence / "state.json").read_text())
                    entry = saved["publication_journal"][-1]
                    self.assertEqual(entry["outcome"], "planned")
                    self.assertEqual(entry["expected_old"], "")
                    self.assertIn(f"--force-with-lease={ref}:", args)
                    raise m.Halt("git_operation_failed_1")
                self.job.git = git
                if outcome == "accepted":
                    self.job.stage_checkpoint(ref, NEW, self.measurement())
                    self.assertEqual(self.job.stages, {ref: NEW})
                    self.assertEqual(self.job.state["publication_journal"][-1]["outcome"], "accepted_after_transport_error")
                else:
                    code = "stage_push_not_accepted" if outcome == "unchanged" else "stage_remote_outcome_unknown"
                    with self.assertRaisesRegex(m.Halt, code):
                        self.job.stage_checkpoint(ref, NEW, self.measurement())
                    self.assertEqual(self.job.state["publication_journal"][-1]["outcome"], outcome)
                    self.assertFalse(self.job.stages)
                self.assertFalse(self.job.state["push_verified"])
                self.assertFalse(self.job.state["restore_complete"])


class StagedPushIntegration(unittest.TestCase):
    setUp = GuardTests.setUp
    tearDown = GuardTests.tearDown

    def create_histories(self, *, merge_side=False):
        self.job.repo.mkdir()
        self.job.env.update({"GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                             "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "test@example.invalid"})
        def git(*args, cwd=None, input=None):
            return subprocess.check_output(["git", *args], cwd=cwd or self.job.repo,
                env=self.job.env, input=input, stderr=subprocess.DEVNULL).decode().strip()
        git("init", "-b", "main")
        (self.job.repo / "old.txt").write_text("synthetic-private-original")
        git("add", ".")
        git("commit", "-qm", "Synthetic original")
        old = git("rev-parse", "HEAD")
        forbidden = git("rev-parse", "HEAD:old.txt")
        git("branch", "branch")
        remote = self.root / "remote.git"
        git("clone", "--bare", str(self.job.repo), str(remote))
        git("update-ref", "refs/pull/1/head", old, cwd=remote)
        self.job.before = {"refs/heads/main": old, "refs/heads/branch": old, "refs/pull/1/head": old}
        git("checkout", "--orphan", "clean")
        git("rm", "-rf", ".")
        (self.job.repo / "base.txt").write_text("synthetic sanitized base")
        git("add", ".")
        git("commit", "-qm", "Synthetic sanitized root")
        base = git("rev-parse", "HEAD")
        roots = {}
        for branch in ("main", "branch"):
            git("checkout", "-B", "clean-" + branch, base)
            for index in range(6):
                (self.job.repo / (branch + str(index) + ".bin")).write_bytes(os.urandom(8192))
                git("add", ".")
                git("commit", "-qm", "Synthetic sanitized increment")
            roots[branch] = git("rev-parse", "HEAD")
        if merge_side:
            git("checkout", "clean-main")
            git("merge", "--no-ff", "-qm", "Synthetic side merge", "clean-branch")
            roots["main"] = git("rev-parse", "HEAD")
        self.job.after = {"refs/heads/" + branch: oid for branch, oid in roots.items()}
        self.job.after["refs/pull/1/head"] = roots["main"]
        self.job.verified_commit_ids = set(git("rev-list", *roots.values()).splitlines())
        self.job.measure_push_pack = lambda **kw: m.Maintenance.measure_push_pack(self.job, **kw)
        self.job.verify_paused = lambda: None
        self.job.pending_runs = lambda: {}
        return git, remote, forbidden

    def test_divergent_histories_keep_stages_until_atomic_final_publish(self):
        git, remote, forbidden = self.create_histories()
        push = self.job.git
        snapshots, args_seen, selected_measures = [], [], []
        def checked(args, **kw):
            if args[0] == "push":
                before = self.job.remote_refs()
                self.assertEqual({ref: before[ref] for ref in self.job.before}, self.job.before)
                entry = self.job.state["publication_journal"][-1]
                self.assertEqual(entry["outcome"], "planned")
                self.assertLess(entry["measured_pack_bytes"], 22000)
                selected_measures.append(entry["measured_pack_bytes"])
                args_seen.append(args)
                if entry["kind"] == "stage":
                    reachable = git("rev-list", "--objects", "--no-object-names", entry["target"]).splitlines()
                    self.assertNotIn(forbidden, reachable)
                    if entry["expected_old"]:
                        git("merge-base", "--is-ancestor", entry["expected_old"], entry["target"])
            result = push(args, **kw)
            if args[0] == "push":
                snapshots.append(self.job.remote_refs())
            return result
        self.job.git = checked
        with patch.object(m, "REMOTE", remote.as_uri()), patch.object(m, "STAGE_PACK_LIMIT", 22000):
            self.job.publish()
        journal = self.job.state["publication_journal"]
        stage_names = {entry["ref"] for entry in journal if entry["kind"] == "stage"}
        self.assertEqual(len(stage_names), 2)
        self.assertGreater(len(journal), 4)
        self.assertEqual(journal[-1]["deleted_stage_ref_count"], 2)
        for snapshot in snapshots[:-1]:
            self.assertEqual({ref: snapshot[ref] for ref in self.job.before}, self.job.before)
        self.assertTrue(stage_names <= set(snapshots[-2]))
        final = snapshots[-1]
        self.assertEqual(m.owned_refs(final), m.owned_refs(self.job.after))
        self.assertEqual(final["refs/pull/1/head"], self.job.before["refs/pull/1/head"])
        for ref in stage_names:
            self.assertIn(":" + ref, args_seen[-1])
            self.assertIn(f"--force-with-lease={ref}:{snapshots[-2][ref]}", args_seen[-1])
        for ref, oid in m.owned_refs(self.job.before).items():
            self.assertIn(f"--force-with-lease={ref}:{oid}", args_seen[-1])
        self.assertEqual(self.job.state["push_pack_measurement"]["stage_ref_count"], 2)
        self.assertTrue(self.job.state["push_verified"])
        self.assertTrue(self.job.state["restore_complete"])
        self.assertFalse(self.job.stages)
        self.assertNotIn("synthetic-private-original", (self.job.evidence / "state.json").read_text())
        self.assertFalse(any(path.suffix == ".pack" for path in self.job.evidence.iterdir()))

    def test_large_merge_side_graph_halts_safely_with_originals_unchanged(self):
        git, remote, forbidden = self.create_histories(merge_side=True)
        with patch.object(m, "REMOTE", remote.as_uri()), patch.object(m, "STAGE_PACK_LIMIT", 22000):
            with self.assertRaisesRegex(m.Halt, "stage_minimum_pack_exceeds_limit"):
                self.job.publish()
            observed = self.job.remote_refs()
        self.assertEqual({ref: observed[ref] for ref in self.job.before}, self.job.before)
        self.assertTrue(self.job.stages)
        self.assertFalse(self.job.state["push_verified"])
        self.assertFalse(self.job.state["restore_complete"])

    def test_real_create_collision_keeps_existing_stage(self):
        git, remote, forbidden = self.create_histories()
        ref = m.STAGE_REF_PREFIX + "99-0"
        target = self.job.after["refs/heads/main"]
        competing = self.job.after["refs/heads/branch"]
        git("push", remote.as_uri(), f"{competing}:{ref}")
        args = ["push", "--atomic", "--porcelain", f"--force-with-lease={ref}:",
                remote.as_uri(), f"{target}:{ref}"]
        result = subprocess.run(["git", *args], cwd=self.job.repo, env=self.job.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git("rev-parse", ref, cwd=remote), competing)
        for original, oid in self.job.before.items():
            self.assertEqual(git("rev-parse", original, cwd=remote), oid)

    def test_real_stale_stage_delete_lease_blocks_entire_final_atomic_push(self):
        git, remote, forbidden = self.create_histories()
        ref = m.STAGE_REF_PREFIX + "99-0"
        expected = self.job.after["refs/heads/main"]
        competing = self.job.after["refs/heads/branch"]
        git("push", remote.as_uri(), f"{competing}:{ref}")
        with patch.object(m, "REMOTE", remote.as_uri()):
            args = m.final_push_arguments(self.job.before, self.job.after, {ref: expected})
        result = subprocess.run(["git", *args], cwd=self.job.repo, env=self.job.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git("rev-parse", ref, cwd=remote), competing)
        for original, oid in self.job.before.items():
            self.assertEqual(git("rev-parse", original, cwd=remote), oid)


class PartialMirrorIntegration(unittest.TestCase):
    def test_real_callback_manifest_and_all_commit_validation(self):
        tool = Path(os.environ.get("FILTER_REPO_BIN", "/nonexistent"))
        project = HERE.parent.parent
        helper_path = project / "privacy23-review/history_rewrite_helpers.py"
        policy = project / "zvg-radar-data/collector/publication_policy.py"
        if not tool.exists() or not helper_path.exists() or not policy.exists():
            self.skipTest("integration helper inputs unavailable")
        helper_spec = importlib.util.spec_from_file_location("real_helper", helper_path)
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.update({"GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                        "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"})
            def git(*args, cwd=None):
                return subprocess.check_output(["git", *args], cwd=cwd or root, env=env, stderr=subprocess.DEVNULL).decode().strip()
            source = root / "author"
            git("init", "-b", "main", str(source))
            private_id = "by-900000001"
            private_pdf = b"synthetic discarded file"
            private_blob = helper.blob_sha(private_pdf)
            private_scope = {"object_id": private_id, "blob_ids": [private_blob], "private_markers": {"text": ["Synthetic Private Road"]}}
            cleaner = helper.HistoryCleaner(private_scope, policy)
            manifest = {"version": 1, "id_hashes": sorted(cleaner.hashes), "blob_ids": [private_blob], "json_blobs": {}}
            media = source / "media" / private_id
            media.mkdir(parents=True)
            (media / "document.pdf").write_bytes(private_pdf)
            (source / "keep.py").write_text("print('unchanged')\n")
            for number in (1, 2):
                raw = json.dumps({"meta": {"count": 2}, "results": [{"id": private_id, "address": "Synthetic Private Road"}, {"id": "he-900000002", "number": number}]}).encode()
                oldsha = helper.blob_sha(raw)
                _, entry = cleaner.transform_json(oldsha, raw)
                manifest["json_blobs"][oldsha] = entry
                (source / "data.json").write_bytes(raw)
                git("add", ".", cwd=source)
                git("commit", "-qm", f"Synthetic version {number}", cwd=source)
                if number == 1:
                    git("tag", "-a", "v1", "-m", "Synthetic annotated tag", cwd=source)
            before_main = git("rev-parse", "HEAD", cwd=source)
            remote = root / "remote.git"
            git("clone", "--bare", str(source), str(remote))
            git("update-ref", "refs/pull/1/head", before_main, cwd=remote)
            code = root / "checkout/.github/privacy-maintenance"
            code.mkdir(parents=True)
            (code.parent.parent / "collector").mkdir()
            shutil.copy2(policy, code.parent.parent / "collector/publication_policy.py")
            shutil.copy2(helper_path, code / helper_path.name)
            shutil.copy2(helper_path.parent / "history-file-callback.py", code / "history-file-callback.py")
            work = root / "work"
            job = m.Maintenance(code, work, "test-token", 99, before_main, "apply", "a" * 64, "b" * 64, FakeAPI())
            job.env["PATH"] = str(tool.parent) + os.pathsep + env["PATH"]
            git("clone", "--mirror", remote.as_uri(), str(job.repo))
            job.before = job.local_refs()
            job.manifest = manifest
            (work / "manifest.json").write_text(json.dumps(manifest))
            job.rewrite()
            job.verify_rewrite()
            self.assertEqual(job.state["verified_json_blob_count"], 2)
            self.assertEqual(job.state["verified_commit_count"], 2)
            self.assertEqual(job.state["affected_pr_count"], 1)
            with patch.object(m, "REMOTE", remote.as_uri()):
                job.publish()
            self.assertTrue(job.state["push_verified"])
            self.assertEqual(git("rev-parse", "refs/pull/1/head", cwd=remote), before_main)
            self.assertEqual(git("show", "refs/heads/main:keep.py", cwd=remote), "print('unchanged')")
            self.assertFalse(any(private_id in p.read_text() for p in job.evidence.iterdir()))

    def test_no_gc_filtered_rewrite_and_atomic_push_preserve_binary(self):
        tool = os.environ.get("FILTER_REPO_BIN") or shutil.which("git-filter-repo")
        if not tool:
            self.skipTest("set FILTER_REPO_BIN for integration")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.update({"GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                        "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"})
            def git(*args, cwd=None, input=None):
                return subprocess.run(["git", *args], cwd=cwd or root, env=env, input=input,
                                      check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            source = root / "author"
            git("init", "-b", "main", str(source))
            (source / "data.json").write_text('[{"keep":true},{"remove":true}]\n')
            (source / "keep.bin").write_bytes(os.urandom(1024 * 1024))
            (source / "removed.pdf").write_bytes(b"synthetic disposable document")
            (source / ".github/workflows").mkdir(parents=True)
            (source / ".github/workflows/existing.yml").write_text("name: unchanged\non: workflow_dispatch\n")
            git("add", ".", cwd=source)
            git("commit", "-qm", "Synthetic initial", cwd=source)
            old = git("rev-parse", "HEAD", cwd=source).decode().strip()
            binary = git("rev-parse", "HEAD:keep.bin", cwd=source).decode().strip()
            remote = root / "remote.git"
            git("clone", "--bare", str(source), str(remote))
            git("config", "uploadpack.allowFilter", "true", cwd=remote)
            git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=remote)
            git("update-ref", "refs/pull/1/head", old, cwd=remote)
            mirror = root / "mirror.git"
            url = remote.as_uri()
            git("clone", "--mirror", "--filter=blob:none", url, str(mirror))
            missing_initial = git("rev-list", "--objects", "--all", "--missing=print", cwd=mirror).decode()
            self.assertIn("?" + binary, missing_initial)
            before = m.parse_refs(git("ls-remote", "--refs", url))
            callback = root / "callback.py"
            callback.write_text('if filename == b"removed.pdf":\n    return (None, mode, blob_id)\nif filename == b"data.json":\n    blob_id = value.insert_file_with_contents(b\'[ {"keep":true} ]\\n\')\nreturn (filename, mode, blob_id)\n')
            subprocess.run([tool, "--force", "--sensitive-data-removal", "--no-fetch", "--no-gc",
                            "--prune-empty", "never", "--prune-degenerate", "never", "--preserve-commit-hashes",
                            "--replace-refs", "delete-no-add", "--file-info-callback", str(callback)],
                           cwd=mirror, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            after = m.parse_refs(git("for-each-ref", "--format=%(objectname)%09%(refname)", cwd=mirror))
            missing_before = git("rev-list", "--objects", "--all", "--missing=print", cwd=mirror).decode()
            print("partial-clone-rewrite-fetched-unrelated-binary:", "?" + binary not in missing_before)
            with patch.object(m, "REMOTE", url):
                git(*m.push_arguments(before, after), cwd=mirror)
            self.assertEqual(git("rev-parse", "refs/heads/main:keep.bin", cwd=remote).decode().strip(), binary)
            self.assertEqual(git("rev-parse", "refs/pull/1/head", cwd=remote).decode().strip(), old)
            self.assertEqual(git("rev-parse", "refs/heads/main", cwd=remote).decode().strip(), after["refs/heads/main"])
            missing_after = git("rev-list", "--objects", "--all", "--missing=print", cwd=mirror).decode()
            print("partial-clone-push-fetched-unrelated-binary:", "?" + binary not in missing_after)
            self.assertEqual(git("show", "refs/heads/main:.github/workflows/existing.yml", cwd=remote), b"name: unchanged\non: workflow_dispatch\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
