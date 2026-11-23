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
import selectors
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
PUSH_PACK_LIMIT = 2 * 1024 ** 3
STAGE_PACK_LIMIT = 1536 * 1024 ** 2
STAGE_REF_PREFIX = "refs/heads/privacy-maintenance-stage-"


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
        "http2_transport_failed": (b"http/2 stream", b"http2 framing layer", b"http/2 framing layer"),
        "sideband_disconnected": (b"unexpected disconnect while reading sideband packet",),
        "connection_reset": (b"connection reset by peer",),
        "local_disk_full": (b"no space left on device",),
        "mirror_refspec_conflict": (b"--mirror can't be combined with refspecs",),
    }
    signals = sorted(code for code, needles in patterns.items() if any(n in sample for n in needles))
    if timed_out:
        signals.append("process_timeout")
    # Extract only bounded integers from recognized transport error lines.
    # No matching line, URL or substring is retained in evidence.
    error_lines = [line for line in sample.splitlines() if line.startswith(
        (b"error: rpc failed;", b"fatal: unable to access", b"remote: error:", b"remote: fatal:"))]
    http_statuses, curl_codes = set(), set()
    for line in error_lines:
        http_statuses.update(int(value) for value in re.findall(rb"\bhttp(?:/[0-9.]+)?[ :]+([1-5][0-9]{2})\b", line))
        http_statuses.update(int(value) for value in re.findall(rb"the requested url returned error: ([1-5][0-9]{2})\b", line))
        curl_codes.update(int(value) for value in re.findall(rb"\bcurl ([1-9][0-9]?)\b", line))
    return {"returncode": returncode, "timed_out": bool(timed_out),
            "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
            "scan_limit_per_stream": DIAGNOSTIC_SCAN_LIMIT,
            "http_statuses": sorted(http_statuses), "curl_codes": sorted(curl_codes),
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


def pack_revisions(before, after, stage_refs=None, target_oids=None):
    """Estimate new owned history against reviewed old owned refs only.

    Internal PR refs are not assumed to be advertised to receive-pack.
    """
    require(owned_refs(before).keys() == owned_refs(after).keys(), "owned_ref_set_changed")
    positive = set(owned_refs(after).values() if target_oids is None else target_oids)
    negative = set(owned_refs(before).values()) | set((stage_refs or {}).values())
    require(positive and negative, "empty_pack_revision_set")
    require(all(SHA1.fullmatch(s) and s != ZERO for s in positive | negative), "invalid_pack_revision")
    return ("".join(s + "\n" for s in sorted(positive)) +
            "".join("^" + s + "\n" for s in sorted(negative))).encode("ascii")


def final_push_arguments(before, after, stage_refs):
    require(not (set(before) & set(stage_refs)), "staging_ref_collides_with_baseline")
    require(all(r.startswith(STAGE_REF_PREFIX) and SHA1.fullmatch(s) and s != ZERO
                for r, s in stage_refs.items()), "invalid_staging_refs")
    args = push_arguments(before, after)
    index = args.index(REMOTE)
    return (args[:index] + [f"--force-with-lease={r}:{s}" for r, s in sorted(stage_refs.items())]
            + args[index:] + [":" + r for r in sorted(stage_refs)])


def auth_environment(token):
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
        "GIT_CONFIG_COUNT": "6",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode(),
        "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0",
        "GIT_CONFIG_KEY_2": "maintenance.auto", "GIT_CONFIG_VALUE_2": "false",
        "GIT_CONFIG_KEY_3": "pack.windowMemory", "GIT_CONFIG_VALUE_3": "256m",
        "GIT_CONFIG_KEY_4": "pack.threads", "GIT_CONFIG_VALUE_4": "2",
        # Use identical full traversal for measurement and actual push. This is
        # conservative; it does not promise to avoid repacking historic blobs.
        "GIT_CONFIG_KEY_5": "pack.useSparse", "GIT_CONFIG_VALUE_5": "false",
    })
    for key in list(env):
        if key.startswith("GIT_TRACE") or key in {
            "GIT_CURL_VERBOSE", "GIT_CONFIG_PARAMETERS", "PRIVACY_MAINTENANCE_PUSH_TOKEN"
        }:
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
    def __init__(self, code, work, token, run_id, sha, mode, refs_hash, manifest_hash, api=None, *, push_token=None):
        self.code, self.work = Path(code), Path(work)
        self.repo = self.work / "source.git"
        self.evidence = self.work / "evidence"
        self.env = auth_environment(token)
        # A repository-scoped owner credential authenticates Git pushes only.
        # API requests, clone, ref reads and pack measurement keep the job token.
        self.push_env = auth_environment(push_token) if push_token else None
        self.api = api or API(token)
        self.run_id, self.sha, self.mode = int(run_id), sha, mode
        self.refs_hash, self.manifest_hash = refs_hash, manifest_hash
        self.stages = {}
        self.verified_commit_ids = set()
        self.state = {"version": 1, "repository": REPOSITORY, "run_id": self.run_id,
                      "operation": mode, "phase": "starting", "push_verified": False,
                      "dedicated_push_token_present": bool(push_token),
                      "restore_complete": False, "workflow_states_before": {},
                      "resume_after_success": [], "paused_workflow_ids": [],
                      "resumed_workflow_ids": [], "cancel_requested_run_ids": [],
                      "force_cancel_requested_run_ids": [], "force_cancel_conflict_run_ids": [],
                      "confirmed_stopped_run_ids": [], "stage_refs": {},
                      "publication_journal": []}
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
        is_push = bool(args and args[0] == "push")
        if is_push:
            require(self.mode == "apply", "push_requires_apply")
            require(self.push_env is not None, "dedicated_push_token_required")
        try:
            result = subprocess.run(["git", *args], cwd=cwd or self.repo,
                                    env=self.push_env if is_push else self.env,
                                    input=input, capture_output=True,
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
        require(not any(r.startswith(STAGE_REF_PREFIX) for r in observed), "staging_recovery_required")
        require(refs_digest(observed) == self.refs_hash, "approved_refs_mismatch")
        require(observed["refs/heads/main"] == self.sha, "workflow_sha_is_not_approved_main")

    def check_push_authorization(self):
        self.phase("check_push_credential_presence")
        # Presence is not proof of scope or permission. A rejected push still
        # follows the exact existing remote-outcome and paused-writer rules.
        require(self.mode != "apply" or self.push_env is not None,
                "dedicated_push_token_required")

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
        require(not any(r.startswith(STAGE_REF_PREFIX) for r in self.before), "staging_recovery_required")
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
        # Set only after every tree, manifest and reachability check succeeded.
        self.verified_commit_ids = set(mapping.values())
        self.save()

    def measure_push_pack(self, timeout=3600, *, target_oids=None, enforce_limit=True,
                          limit_bytes=None):
        self.phase("measure_offline_push_pack")
        limit_bytes = PUSH_PACK_LIMIT if limit_bytes is None else limit_bytes
        revisions = pack_revisions(self.before, self.after, self.stages, target_oids)
        record = {"version": 1, "is_offline_estimate": True,
                  "exclusion_basis": "reviewed_old_owned_and_verified_current_stage_refs",
                  "stage_ref_count": len(self.stages),
                  "target_oids": sorted(set(owned_refs(self.after).values() if target_oids is None else target_oids)),
                  "expected_remote_refs_sha256": refs_digest(self.expected_staged_snapshot()),
                  "full_object_traversal": True,
                  "receive_pack_advertisement_unverified": True,
                  "limit_bytes": limit_bytes, "bytes": 0,
                  "complete": False, "returncode": None}
        self.state["push_pack_measurement"] = record
        self.save()
        deadline = time.monotonic() + timeout
        process = None
        header = b""
        try:
            # Only count the stream. Never create a pack file or retain its contents.
            # stderr is discarded; fixed errors below cannot expose source paths.
            process = subprocess.Popen(
                ["git", "pack-objects", "--revs", "--stdout", "--thin", "--delta-base-offset", "--quiet"],
                cwd=self.repo, env=self.env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with process.stdin:
                process.stdin.write(revisions)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0 and selector.select(remaining), "push_pack_measurement_timeout")
                    block = os.read(process.stdout.fileno(), 1024 * 1024)
                    if not block:
                        break
                    if len(header) < 12:
                        header += block[:12 - len(header)]
                    record["bytes"] += len(block)
            record["returncode"] = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            require(record["returncode"] == 0, "push_pack_measurement_failed")
            require(len(header) == 12 and header[:4] == b"PACK" and
                    int.from_bytes(header[4:8], "big") in (2, 3), "invalid_push_pack_header")
            record["object_count"] = int.from_bytes(header[8:12], "big")
            record["complete"] = True
        except subprocess.TimeoutExpired:
            raise Halt("push_pack_measurement_timeout") from None
        except OSError:
            raise Halt("push_pack_measurement_io_failure") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                record["returncode"] = process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stdin is not None:
                    process.stdin.close()
            self.save()
        if enforce_limit:
            require(record["bytes"] < limit_bytes, "estimated_push_pack_exceeds_limit")
        return record

    def expected_staged_snapshot(self):
        require(not (set(self.before) & set(self.stages)), "staging_ref_collides_with_baseline")
        return dict(self.before, **self.stages)

    def publication_guards(self):
        self.phase("prepush_checks")
        self.verify_paused()
        require(not self.pending_runs(), "new_run_appeared")
        require(self.remote_refs() == self.expected_staged_snapshot(), "remote_refs_changed_before_push")

    def stage_checkpoint(self, ref, target, measurement):
        require(self.mode == "apply", "stage_write_requires_apply")
        require(ref.startswith(STAGE_REF_PREFIX) and ref not in self.before, "invalid_staging_ref")
        require(target in self.verified_commit_ids, "unverified_staging_commit")
        require(measurement["complete"] and measurement["bytes"] < STAGE_PACK_LIMIT,
                "estimated_stage_pack_exceeds_limit")
        require(measurement["target_oids"] == [target] and
                measurement["expected_remote_refs_sha256"] == refs_digest(self.expected_staged_snapshot()),
                "stage_measurement_target_or_snapshot_mismatch")
        old = self.stages.get(ref, "")
        require(old != target, "staging_checkpoint_did_not_advance")
        if old:
            try:
                self.git(["merge-base", "--is-ancestor", old, target])
            except Halt:
                raise Halt("staging_checkpoint_not_descendant") from None
        # Measurements do not authorize a write after a changed ref or new writer.
        self.publication_guards()
        before = self.expected_staged_snapshot()
        after = dict(before, **{ref: target})
        entry = {"kind": "stage", "ref": ref, "expected_old": old,
                 "target": target, "measured_pack_bytes": measurement["bytes"],
                 "outcome": "planned"}
        self.state["publication_journal"].append(entry)
        self.save()
        self.phase("leased_stage_push")
        failed = False
        try:
            self.git(["push", "--atomic", "--porcelain", f"--force-with-lease={ref}:{old}",
                      REMOTE, f"{target}:{ref}"], timeout=3600)
        except Halt:
            failed = True
        try:
            observed = self.remote_refs()
        except Halt:
            entry["outcome"] = "unreadable"
            self.save()
            raise Halt("stage_remote_outcome_unknown") from None
        if observed != after:
            entry["outcome"] = "unchanged" if observed == before else "divergent"
            self.save()
            raise Halt("stage_push_not_accepted" if observed == before else "stage_remote_outcome_unknown")
        self.stages[ref] = target
        self.state["stage_refs"] = dict(self.stages)
        entry["outcome"] = "accepted_after_transport_error" if failed else "accepted"
        (self.evidence / "refs-staged-verified.tsv").write_bytes(canonical_refs(observed))
        self.save()

    def stage_branch_history(self, branch, target, index):
        require(branch.startswith("refs/heads/"), "staging_requires_branch_targets")
        require(target in self.verified_commit_ids, "unverified_staging_commit")
        ref = STAGE_REF_PREFIX + str(self.run_id) + "-" + str(index)
        chain = self.git(["rev-list", "--first-parent", "--reverse", target]).decode("ascii").splitlines()
        require(chain and chain[-1] == target and len(chain) == len(set(chain)), "invalid_staging_chain")
        require(set(chain) <= self.verified_commit_ids, "unverified_staging_commit")
        cursor = -1
        while cursor + 1 < len(chain):
            candidate = len(chain) - 1
            while True:
                measurement = self.measure_push_pack(target_oids=[chain[candidate]],
                    enforce_limit=False, limit_bytes=STAGE_PACK_LIMIT)
                if measurement["bytes"] < STAGE_PACK_LIMIT:
                    break
                # Pack sizes need not be monotone. Halving proposes another
                # candidate; only its own actual measurement can allow a push.
                require(candidate > cursor + 1, "stage_minimum_pack_exceeds_limit")
                candidate = (cursor + 1 + candidate) // 2
            self.stage_checkpoint(ref, chain[candidate], measurement)
            cursor = candidate

    def stage_large_publication(self):
        require(self.mode == "apply", "stage_write_requires_apply")
        require(all(r.startswith("refs/heads/") for r in owned_refs(self.after)),
                "staging_requires_branch_targets")
        targets = owned_refs(self.after)
        ordered = sorted(targets, key=lambda r: (r != "refs/heads/main", r))
        for index, branch in enumerate(ordered):
            self.stage_branch_history(branch, targets[branch], index)
            measurement = self.measure_push_pack(enforce_limit=False, limit_bytes=STAGE_PACK_LIMIT)
            if measurement["bytes"] < STAGE_PACK_LIMIT:
                return measurement
        raise Halt("final_pack_exceeds_staging_limit")

    def publish(self):
        self.publication_guards()
        measurement = self.measure_push_pack(enforce_limit=False, limit_bytes=STAGE_PACK_LIMIT)
        # Packing may take time. Recheck every production guard before publishing.
        self.publication_guards()
        if self.mode == "preflight":
            self.phase("preflight_verified_writers_remain_paused")
            return
        if measurement["bytes"] >= STAGE_PACK_LIMIT:
            measurement = self.stage_large_publication()
            self.publication_guards()
        require(measurement["complete"] and measurement["bytes"] < STAGE_PACK_LIMIT,
                "estimated_push_pack_exceeds_limit")
        require(measurement["target_oids"] == sorted(set(owned_refs(self.after).values())) and
                measurement["expected_remote_refs_sha256"] == refs_digest(self.expected_staged_snapshot()),
                "final_measurement_target_or_snapshot_mismatch")
        entry = {"kind": "final", "measured_pack_bytes": measurement["bytes"],
                 "owned_ref_count": len(owned_refs(self.before)),
                 "deleted_stage_ref_count": len(self.stages), "outcome": "planned"}
        self.state["publication_journal"].append(entry)
        self.save()
        self.phase("atomic_leased_push")
        failed = False
        try:
            self.git(final_push_arguments(self.before, self.after, self.stages), timeout=3600)
        except Halt:
            # A transport error after server acceptance is possible. Never guess its outcome.
            failed = True
        try:
            observed = self.remote_refs()
        except Halt:
            entry["outcome"] = "unreadable"
            self.save()
            raise Halt("final_remote_outcome_unknown") from None
        if owned_refs(observed) != owned_refs(self.after):
            entry["outcome"] = "unchanged" if observed == self.expected_staged_snapshot() else "divergent"
            self.save()
            raise Halt("push_failed_or_remote_outcome_unverified")
        entry["outcome"] = "accepted_after_transport_error" if failed else "accepted"
        if failed:
            self.state["push_transport_error_but_remote_verified"] = True
        self.stages = {}
        self.state["stage_refs"] = {}
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
            self.check_push_authorization()
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
    push_token = os.environ.pop("PRIVACY_MAINTENANCE_PUSH_TOKEN", "")
    code = Path(__file__).resolve().parent
    # Add the installed tool's bin directory, without credential-bearing command arguments.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    job = Maintenance(code, Path(os.environ["RUNNER_TEMP"]) / "privacy-maintenance", token,
                      os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_SHA"],
                      os.environ["MAINTENANCE_OPERATION"], os.environ["APPROVED_REFS_SHA256"],
                      os.environ["APPROVED_MANIFEST_SHA256"], push_token=push_token)
    return job.execute()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Halt as exc:
        print("Maintenance configuration rejected:", str(exc))
        sys.exit(1)
