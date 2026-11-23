#!/usr/bin/env python3
"""One-repository, hash-gated maintenance. Never log source content or credentials."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPOSITORY = "danielfella-del/zvg-radar-data"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ZERO = "0" * 40
DIAGNOSTIC_SCAN_LIMIT = 65536


def push_diagnostics(returncode, stdout=b"", stderr=b"", *, timed_out=False):
    """Return fixed signal names and numbers only, never captured Git text.

    Signals describe observed error messages, not permission to bypass a guard.
    Scan bounded head/tail windows because server errors often follow progress.
    """
    def window(raw):
        half = DIAGNOSTIC_SCAN_LIMIT // 2
        return raw if len(raw) <= DIAGNOSTIC_SCAN_LIMIT else raw[:half] + b"\n" + raw[-half:]

    sample = (window(stdout) + b"\n" + window(stderr)).lower()
    patterns = {
        "workflow_permission_denied": (
            b"without `workflows` permission", b"without `workflow` scope",
            b"without workflows permission", b"without workflow scope"),
        "pack_size_limit": (b"pack exceeds maximum allowed size",),
        "file_size_limit": (b"gh001: large files detected",),
        "repository_rule_rejected": (b"gh006: protected branch update failed", b"gh013: repository rule violations found"),
        "lease_rejected": (b"(stale info)", b"(fetch first)"),
        "atomic_push_unsupported": (b"the receiving end does not support --atomic push",),
        "atomic_push_rejected": (b"atomic push failed", b"atomic push failure"),
        "authentication_failed": (b"fatal: authentication failed", b"invalid username or token"),
        "repository_write_denied": (b"write access to repository not granted",),
        "transport_failed": (b"rpc failed;", b"the remote end hung up unexpectedly", b"fatal: unable to access"),
        "local_disk_full": (b"no space left on device",),
        "mirror_refspec_conflict": (b"--mirror can't be combined with refspecs",),
    }
    signals = sorted(code for code, needles in patterns.items() if any(n in sample for n in needles))
    if timed_out:
        signals.append("process_timeout")
    return {"returncode": returncode, "timed_out": bool(timed_out),
            "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
            "scan_limit_per_stream": DIAGNOSTIC_SCAN_LIMIT,
            "observed_signals": signals or ["unclassified"]}


class Halt(RuntimeError):
    """Messages are fixed technical codes safe to include in public evidence."""


def require(condition, code):
    if not condition:
        raise Halt(code)


def canonical_refs(refs):
    return "".join(f"{oid}\t{ref}\n" for ref, oid in sorted(refs.items())).encode()


def refs_digest(refs):
    return hashlib.sha256(canonical_refs(refs)).hexdigest()


def parse_refs(raw):
    refs = {}
    for line in raw.decode("ascii").splitlines():
        oid, ref = line.split("\t")
        require(SHA1.fullmatch(oid) is not None, "invalid_ref_object_id")
        require(ref.startswith(("refs/heads/", "refs/tags/", "refs/pull/")), "unexpected_ref_namespace")
        require(ref not in refs, "duplicate_ref")
        require(not any(c.isspace() for c in ref), "invalid_ref_name")
        refs[ref] = oid
    require("refs/heads/main" in refs, "main_ref_absent")
    return refs


def owned_refs(refs):
    return {r: s for r, s in refs.items() if r.startswith(("refs/heads/", "refs/tags/"))}


def push_arguments(before, after):
    old, new = owned_refs(before), owned_refs(after)
    require(old.keys() == new.keys(), "owned_ref_set_changed")
    require(all(SHA1.fullmatch(s) and s != ZERO for s in new.values()), "invalid_push_target")
    # No '+' refspecs or --force/--mirror: each overwrite has its exact reviewed lease.
    return ["push", "--atomic", "--porcelain"] + [
        f"--force-with-lease={r}:{old[r]}" for r in sorted(old)
    ] + [REMOTE] + [f"{new[r]}:{r}" for r in sorted(new)]


def auth_environment(token):
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
        "GIT_CONFIG_COUNT": "5",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode(),
        "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0",
        "GIT_CONFIG_KEY_2": "maintenance.auto", "GIT_CONFIG_VALUE_2": "false",
        "GIT_CONFIG_KEY_3": "pack.windowMemory", "GIT_CONFIG_VALUE_3": "256m",
        "GIT_CONFIG_KEY_4": "pack.threads", "GIT_CONFIG_VALUE_4": "2",
    })
    for key in list(env):
        if key.startswith("GIT_TRACE") or key in {"GIT_CURL_VERBOSE", "GIT_CONFIG_PARAMETERS"}:
            del env[key]
    return env


class API:
    def __init__(self, token):
        self.token = token

    def request(self, path, method="GET"):
        require(path.startswith("/actions/"), "unexpected_api_path")
        req = urllib.request.Request(
            "https://api.github.com/repos/" + REPOSITORY + path,
            method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Responses can include filenames and private content; never emit their bodies.
            raise Halt("github_api_http_" + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise Halt("github_api_transport_failure") from None

    def list_items(self, path, key):
        output = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 101):
            values = self.request(f"{path}{sep}per_page=100&page={page}")[key]
            output.extend(values)
            if len(values) < 100:
                return output
        raise Halt("github_api_pagination_limit")


class Maintenance:
    def __init__(self, code, work, token, run_id, sha, mode, refs_hash, manifest_hash, api=None):
        self.code, self.work = Path(code), Path(work)
        self.repo = self.work / "source.git"
        self.evidence = self.work / "evidence"
        self.env = auth_environment(token)
        self.api = api or API(token)
        self.run_id, self.sha, self.mode = int(run_id), sha, mode
        self.refs_hash, self.manifest_hash = refs_hash, manifest_hash
        self.state = {"version": 1, "repository": REPOSITORY, "run_id": self.run_id,
                      "operation": mode, "phase": "starting", "push_verified": False,
                      "restore_complete": False, "workflow_states_before": {},
                      "resume_after_success": [], "paused_workflow_ids": [],
                      "resumed_workflow_ids": [], "cancel_requested_run_ids": [],
                      "force_cancel_requested_run_ids": [], "force_cancel_conflict_run_ids": [],
                      "confirmed_stopped_run_ids": []}
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.save()

    def save(self):
        target = self.evidence / "state.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, sort_keys=True, indent=2) + "\n")
        temporary.replace(target)

    def phase(self, name):
        self.state["phase"] = name
        self.save()
        print("Maintenance phase:", name, flush=True)

    def git(self, args, *, cwd=None, input=None, timeout=3600):
        try:
            result = subprocess.run(["git", *args], cwd=cwd or self.repo,
                                    env=self.env, input=input, capture_output=True,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            if args and args[0] == "push":
                self.state["push_diagnostics"] = push_diagnostics(
                    None, exc.stdout or b"", exc.stderr or b"", timed_out=True)
                self.save()
            raise Halt("git_operation_timeout") from None
        if args and args[0] == "push" and result.returncode != 0:
            self.state["push_diagnostics"] = push_diagnostics(
                result.returncode, result.stdout, result.stderr)
            self.save()
        require(result.returncode == 0, "git_operation_failed_" + str(result.returncode))
        return result.stdout

    def remote_refs(self):
        return parse_refs(self.git(["ls-remote", "--refs", REMOTE], cwd=self.work))

    def local_refs(self):
        return parse_refs(self.git(["for-each-ref", "--format=%(objectname)%09%(refname)"]))

    def load_manifest(self):
        require(self.mode in {"preflight", "apply"}, "invalid_operation")
        require(SHA256.fullmatch(self.refs_hash) and SHA256.fullmatch(self.manifest_hash), "invalid_approval_digest")
        try:
            raw = gzip.decompress(base64.b64decode((self.code / "approved-manifest.b64").read_bytes(), validate=False))
            require(hashlib.sha256(raw).hexdigest() == self.manifest_hash, "manifest_digest_mismatch")
            data = json.loads(raw)
        except (ValueError, OSError, EOFError):
            raise Halt("invalid_manifest_encoding") from None
        require(set(data) == {"version", "id_hashes", "blob_ids", "json_blobs"}, "unexpected_manifest_fields")
        require(data["version"] == 1, "unsupported_manifest_version")
        require(isinstance(data["json_blobs"], dict) and data["json_blobs"], "empty_manifest")
        for key, regex in [("id_hashes", SHA256), ("blob_ids", SHA1)]:
            require(isinstance(data[key], list) and data[key], "empty_scope")
            require(all(isinstance(x, str) and regex.fullmatch(x) for x in data[key]), "non_hash_scope")
        for old, change in data["json_blobs"].items():
            require(SHA1.fullmatch(old) is not None, "invalid_old_blob_hash")
            require(set(change) == {"new_sha", "mode", "changed"}, "unexpected_blob_manifest_fields")
            require(SHA1.fullmatch(change["new_sha"]) is not None, "invalid_new_blob_hash")
            require(change["mode"] in {"fast", "full"} and type(change["changed"]) is bool, "invalid_blob_manifest_mode")
            require(change["changed"] == (old != change["new_sha"]), "inconsistent_blob_manifest")
        self.manifest = data
        (self.work / "manifest.json").write_bytes(raw)
        self.state["approved_manifest_sha256"] = self.manifest_hash
        self.state["approved_refs_sha256"] = self.refs_hash
        self.state["approved_json_blob_count"] = len(data["json_blobs"])
        self.save()

    def pause_workflows(self):
        self.phase("pause_workflows")
        baseline = json.loads((self.code / "workflow-baseline.data").read_text())
        require(baseline["version"] == 1, "invalid_workflow_baseline")
        own_id = self.api.request(f"/actions/runs/{self.run_id}")["workflow_id"]
        current = {str(w["id"]): w for w in self.api.list_items("/actions/workflows", "workflows") if w["id"] != own_id}
        require(current.keys() == baseline["workflows"].keys(), "workflow_set_changed")
        for wid, prior in baseline["workflows"].items():
            require(current[wid]["path"] == prior["path"], "workflow_path_changed")
            require(prior["state"] in {"active", "disabled_manually", "disabled_inactivity"}, "unsupported_prior_workflow_state")
            require(current[wid]["state"] in {prior["state"], "disabled_manually"}, "unexpected_current_workflow_state")
        self.state["workflow_states_before"] = {k: v["state"] for k, v in current.items()}
        self.state["resume_after_success"] = sorted(int(k) for k, v in baseline["workflows"].items() if v["state"] == "active")
        self.save()
        for wid, workflow in current.items():
            if workflow["state"] == "active":
                self.api.request(f"/actions/workflows/{wid}/disable", "PUT")
            self.state["paused_workflow_ids"].append(int(wid))
            self.save()
        self.verify_paused()

    def precheck_approved_refs(self):
        # An obsolete rerun must fail before it can pause healthy production writers.
        # The same checks remain mandatory after pause/drain and again before push.
        self.phase("check_approval_before_pause")
        observed = self.remote_refs()
        require(refs_digest(observed) == self.refs_hash, "approved_refs_mismatch")
        require(observed["refs/heads/main"] == self.sha, "workflow_sha_is_not_approved_main")

    def verify_paused(self):
        for wid in self.state["paused_workflow_ids"]:
            state = self.api.request(f"/actions/workflows/{wid}")["state"]
            require(state != "active", "workflow_not_paused")

    def pending_runs(self):
        runs = {}
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            for run in self.api.list_items("/actions/runs?status=" + status, "workflow_runs"):
                if int(run["id"]) != self.run_id:
                    runs[int(run["id"])] = run
        return runs

    def drain_runs(self):
        self.phase("drain_other_runs")
        deadline = time.monotonic() + 600
        attempted_at = {}
        ordinary_conflicts = set()
        while True:
            pending = self.pending_runs()
            # Verify attempted cancellations directly as well as through the filtered list.
            # A temporarily incomplete list must not conceal a still-running writer.
            for run_id in attempted_at:
                observed = self.api.request(f"/actions/runs/{run_id}")
                if observed["status"] != "completed":
                    pending[run_id] = observed
                elif run_id not in self.state["confirmed_stopped_run_ids"]:
                    self.state["confirmed_stopped_run_ids"].append(run_id)
                    self.save()
            if not pending:
                return
            require(time.monotonic() < deadline, "other_runs_did_not_stop")
            for run_id in pending:
                if run_id not in attempted_at:
                    try:
                        self.api.request(f"/actions/runs/{run_id}/cancel", "POST")
                    except Halt as exc:
                        if str(exc) != "github_api_http_409":
                            raise
                        ordinary_conflicts.add(run_id)
                    attempted_at[run_id] = time.monotonic()
                    self.state["cancel_requested_run_ids"].append(run_id)
                    self.save()
                force_due = run_id in ordinary_conflicts or time.monotonic() - attempted_at[run_id] >= 60
                if force_due and run_id not in self.state["force_cancel_requested_run_ids"]:
                    # GitHub documents force-cancel for runs unresponsive to ordinary cancel.
                    # Recheck individually before escalation to handle a completion race.
                    observed = self.api.request(f"/actions/runs/{run_id}")
                    if observed["status"] == "completed":
                        continue
                    self.state["force_cancel_requested_run_ids"].append(run_id)
                    self.save()
                    try:
                        self.api.request(f"/actions/runs/{run_id}/force-cancel", "POST")
                    except Halt as exc:
                        if str(exc) != "github_api_http_409":
                            raise
                        self.state["force_cancel_conflict_run_ids"].append(run_id)
                        self.save()
                    # A 202/409 does not prove termination: poll to observed completion.
            time.sleep(10)

    def clone(self):
        self.phase("snapshot_and_clone")
        self.before = self.remote_refs()
        require(refs_digest(self.before) == self.refs_hash, "approved_refs_mismatch")
        require(self.before["refs/heads/main"] == self.sha, "workflow_sha_is_not_approved_main")
        (self.evidence / "refs-before.tsv").write_bytes(canonical_refs(self.before))
        require(not self.repo.exists(), "temporary_repo_already_exists")
        # fast-import can demand-fetch every retained blob even with --no-data/--no-gc.
        # A full mirror batches this transfer instead of risking thousands of lazy fetches.
        require(shutil.disk_usage(self.work).free > 14 * 1024 ** 3, "insufficient_free_disk")
        self.git(["clone", "--mirror", REMOTE, str(self.repo)], cwd=self.work)
        require(self.local_refs() == self.before, "clone_refs_changed")
        require(shutil.disk_usage(self.work).free > 4 * 1024 ** 3, "insufficient_postclone_disk")

    def rewrite(self):
        self.phase("rewrite_local_history")
        self.env.update({
            "ZVG_REWRITE_HELPER": str(self.code / "history_rewrite_helpers.py"),
            "ZVG_POLICY_MODULE": str(self.code.parent.parent / "collector/publication_policy.py"),
            "ZVG_WITHDRAWAL_CONFIG": json.dumps({k: self.manifest[k] for k in ("id_hashes", "blob_ids")}),
            "ZVG_REWRITE_MANIFEST": str(self.work / "manifest.json"),
            "ZVG_REWRITE_AUDIT_PATH": str(self.work / "audit.jsonl"),
        })
        self.git(["filter-repo", "--sensitive-data-removal", "--no-fetch", "--no-gc",
                  "--prune-empty", "never", "--prune-degenerate", "never",
                  "--preserve-commit-hashes", "--replace-refs", "delete-no-add",
                  "--file-info-callback", str(self.code / "history-file-callback.py")], timeout=7200)
        self.after = self.local_refs()
        require(self.before.keys() == self.after.keys(), "rewrite_ref_set_changed")

    def verify_audit(self):
        seen = {}
        for line in (self.work / "audit.jsonl").read_text().splitlines():
            entry = json.loads(line)
            require(set(entry) == {"old_sha", "new_sha", "mode", "changed"}, "unexpected_audit_fields")
            old = entry["old_sha"]
            require(old in self.manifest["json_blobs"], "unapproved_audit_blob")
            expected = self.manifest["json_blobs"][old]
            require(entry["new_sha"] == expected["new_sha"] and entry["mode"] == expected["mode"] and entry["changed"] == expected["changed"], "audit_hash_mismatch")
            require(old not in seen or seen[old] == entry, "conflicting_audit_entries")
            seen[old] = entry
        require(seen.keys() == self.manifest["json_blobs"].keys(), "audit_coverage_mismatch")
        self.state["verified_json_blob_count"] = len(seen)
        self.state["changed_json_blob_count"] = sum(v["changed"] for v in self.manifest["json_blobs"].values())

    def verify_tree_change(self, raw):
        # Raw -z diff exposes only modes, hashes and paths; paths never enter logs/artifacts.
        fields = raw.split(b"\0")
        for index in range(0, len(fields) - 1, 2):
            header, path = fields[index], fields[index + 1]
            oldmode, newmode, old, new, status = header.decode("ascii").split()
            oldmode = oldmode.lstrip(":")
            if status == "D":
                require(old in self.manifest["blob_ids"], "unapproved_file_deletion")
            else:
                require(status == "M" and oldmode == newmode, "unapproved_file_operation")
                require(path.lower().endswith(b".json"), "non_json_content_changed")
                require(old in self.manifest["json_blobs"] and self.manifest["json_blobs"][old]["new_sha"] == new, "unapproved_json_change")

    def verify_rewrite(self):
        self.phase("verify_every_rewritten_commit")
        self.verify_audit()
        mapfile = self.repo / "filter-repo/commit-map"
        lines = mapfile.read_text().splitlines()
        require(lines and lines[0].split() == ["old", "new"], "invalid_commit_map")
        mapping = {}
        for line in lines[1:]:
            old, new = line.split()
            require(SHA1.fullmatch(old) and SHA1.fullmatch(new) and new != ZERO, "commit_removed_or_invalid")
            mapping[old] = new
            if old != new:
                raw = self.git(["diff-tree", "--raw", "-z", "--no-renames", "--no-abbrev", "--no-commit-id", "-r", old, new])
                self.verify_tree_change(raw)
        # Include PR refs in local reachability checks; these are never pushed.
        reachable = {x.lstrip("?") for x in self.git(["rev-list", "--objects", "--all", "--missing=print", "--no-object-names"]).decode().splitlines()}
        require(not (set(self.manifest["blob_ids"]) & reachable), "withdrawn_blob_still_reachable")
        forbidden_json = {k for k, v in self.manifest["json_blobs"].items() if v["changed"]}
        require(not (forbidden_json & reachable), "unfiltered_json_still_reachable")
        self.state["verified_commit_count"] = len(mapping)
        self.state["changed_commit_count"] = sum(a != b for a, b in mapping.items())
        (self.evidence / "commit-map.tsv").write_text("old\tnew\n" + "".join(f"{a}\t{b}\n" for a, b in sorted(mapping.items())))
        first = [line.split() for line in (self.repo / "filter-repo/first-changed-commits").read_text().splitlines()]
        require(first and all(len(row) == 2 and all(SHA1.fullmatch(x) for x in row) for row in first), "invalid_first_changed_commits")
        (self.evidence / "first-changed-commits.txt").write_text("".join(" ".join(row) + "\n" for row in first))
        self.state["first_changed_commit_count"] = len(first)
        (self.evidence / "refs-after-local.tsv").write_bytes(canonical_refs(self.after))
        affected = {r: {"old": self.before[r], "rewritten": self.after[r]} for r in self.before if r.startswith("refs/pull/") and self.before[r] != self.after[r]}
        (self.evidence / "support-pr-refs.json").write_text(json.dumps(affected, sort_keys=True, indent=2) + "\n")
        self.state["affected_pr_ref_count"] = len(affected)
        self.state["affected_pr_count"] = len({r.split("/")[2] for r in affected})
        lfs = self.repo / "filter-repo/orphaned_lfs_objects"
        lfs_ids = lfs.read_text().splitlines() if lfs.exists() else []
        require(all(re.fullmatch(r"sha256:[0-9a-f]{64}", oid) for oid in lfs_ids), "unexpected_lfs_report_content")
        self.state["orphaned_lfs_object_count"] = len(lfs_ids)
        (self.evidence / "orphaned-lfs-objects.txt").write_text("".join(oid + "\n" for oid in lfs_ids))
        self.save()

    def publish(self):
        self.phase("prepush_checks")
        self.verify_paused()
        require(not self.pending_runs(), "new_run_appeared")
        require(self.remote_refs() == self.before, "remote_refs_changed_before_push")
        if self.mode == "preflight":
            self.phase("preflight_verified_writers_remain_paused")
            return
        self.phase("atomic_leased_push")
        try:
            self.git(push_arguments(self.before, self.after), timeout=3600)
        except Halt:
            # A transport error after server acceptance is possible. Never guess its outcome.
            observed = self.remote_refs()
            require(owned_refs(observed) == owned_refs(self.after), "push_failed_or_remote_outcome_unverified")
            self.state["push_transport_error_but_remote_verified"] = True
        observed = self.remote_refs()
        require(owned_refs(observed) == owned_refs(self.after), "remote_refs_postpush_mismatch")
        self.state["push_verified"] = True
        (self.evidence / "refs-remote-verified.tsv").write_bytes(canonical_refs(observed))
        self.save()
        self.restore_workflows()
        self.phase("owned_refs_verified_support_cleanup_outstanding")

    def restore_workflows(self):
        require(self.state["push_verified"], "restore_without_verified_push")
        self.phase("restore_previously_active_workflows")
        for wid in self.state["resume_after_success"]:
            self.api.request(f"/actions/workflows/{wid}/enable", "PUT")
            require(self.api.request(f"/actions/workflows/{wid}")["state"] == "active", "workflow_restore_not_verified")
            self.state["resumed_workflow_ids"].append(wid)
            self.save()
        self.state["restore_complete"] = True
        self.save()

    def execute(self):
        try:
            self.load_manifest()
            self.precheck_approved_refs()
            self.pause_workflows()
            self.drain_runs()
            self.clone()
            self.rewrite()
            self.verify_rewrite()
            self.publish()
        except BaseException as exc:
            self.state["failure_phase"] = self.state["phase"]
            self.state["phase"] = "halted"
            self.state["error_code"] = str(exc) if isinstance(exc, Halt) else "unexpected_" + type(exc).__name__
            self.save()
            print("Maintenance halted:", self.state["error_code"], flush=True)
            return 1
        return 0


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "wrong_repository")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "manual_dispatch_required")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "main_dispatch_required")
    token = os.environ["GITHUB_TOKEN"]
    require(bool(token), "token_required")
    code = Path(__file__).resolve().parent
    # Add the installed tool's bin directory, without credential-bearing command arguments.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    job = Maintenance(code, Path(os.environ["RUNNER_TEMP"]) / "privacy-maintenance", token,
                      os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_SHA"],
                      os.environ["MAINTENANCE_OPERATION"], os.environ["APPROVED_REFS_SHA256"],
                      os.environ["APPROVED_MANIFEST_SHA256"])
    return job.execute()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Halt as exc:
        print("Maintenance configuration rejected:", str(exc))
        sys.exit(1)
