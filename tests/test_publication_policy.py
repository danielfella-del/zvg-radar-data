"""Synthetic withdrawal tests; no actual complaint data or network requests."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collector"))
import publication_policy as policy
import enrich_details
import cache_media
import extract_report_photos
import merge_history

BLOCKED = "by-900000001"
ALLOWED = "by-900000002"


def record(oid):
    state, number = oid.split("-")
    return {"id": oid, "state_code": state, "zvg_id": number,
            "source_url": f"https://www.zvg-portal.de/index.php?button=showZvg&land_abk={state}&zvg_id={number}",
            "address": "Synthetic fixture only", "auction_date": "2099-01-01",
            "attachments": [{"type": "gutachten", "file_id": "1", "url": "https://example.invalid/fixture.pdf"}]}


class PublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(policy, "WITHDRAWN_ID_HASHES", frozenset({hashlib.sha256(BLOCKED.encode()).hexdigest()})).start()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_normalization_and_no_prefix_overblocking(self):
        self.assertTrue(policy.withdrawn_id(" " + BLOCKED.upper() + " "))
        self.assertFalse(policy.withdrawn_id(ALLOWED))
        self.assertFalse(policy.withdrawn_id(BLOCKED + "0"))

    def test_old_snapshot_removed_with_nested_history_and_errors(self):
        keep = record(ALLOWED)
        data = {"meta": {"count": 2, "marker": "retain", "history_tracking": {"removed_sample": [record(BLOCKED), keep]},
                         "errors": [{"object_id": BLOCKED, "message": "fixture"}]},
                "results": [record(BLOCKED), keep]}
        clean = policy.sanitize_payload(data)
        self.assertEqual(clean["results"], [keep])
        self.assertEqual(clean["meta"]["history_tracking"]["removed_sample"], [keep])
        self.assertEqual(clean["meta"]["errors"], [])
        self.assertEqual(clean["meta"]["count"], 1)
        self.assertEqual(clean["meta"]["marker"], "retain")
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(policy.sanitize_payload(clean), clean)

    def test_separate_portal_identity_is_blocked(self):
        obj = record(BLOCKED)
        del obj["id"]
        self.assertTrue(policy.withdrawn_record(obj))

    def test_paths_urls_and_encoded_references(self):
        for text in [f"media/{BLOCKED}/preview.jpg", f"https://example.invalid/{BLOCKED}/document.pdf",
                     f"media/{BLOCKED.replace('-', '%252d')}/document.pdf", record(BLOCKED)["source_url"]]:
            self.assertTrue(policy.withdrawn_reference(text), text)
        self.assertFalse(policy.withdrawn_reference("media/" + BLOCKED + "0/file.pdf"))

    def test_mapped_keys_and_standalone_references_removed(self):
        clean = policy.sanitize_payload({BLOCKED: {"fixture": 1}, ALLOWED: 2,
                                         "references": [f"media/{BLOCKED}/a.pdf", "safe"]})
        self.assertEqual(clean, {ALLOWED: 2, "references": ["safe"]})

    def test_unrelated_data_exactly_preserved(self):
        data = {"meta": {"count": 999, "unrelated": [None, True, 123]}, "results": [record(ALLOWED)]}
        self.assertEqual(policy.sanitize_payload(data), data)

    def test_read_only_check_does_not_change_snapshot(self):
        path = self.root / "data.json"
        before = json.dumps({"results": [record(BLOCKED), record(ALLOWED)]})
        path.write_text(before)
        self.assertTrue(policy.enforce_file(path, check=True))
        self.assertEqual(path.read_text(), before)
        self.assertTrue(policy.enforce_file(path))
        self.assertEqual(json.loads(path.read_text())["results"], [record(ALLOWED)])
        self.assertFalse(policy.enforce_file(path, check=True))

    def test_invalid_json_fails_closed(self):
        path = self.root / "data.json"
        path.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            policy.enforce_file(path)
        self.assertEqual(path.read_text(), "{broken")

    def test_media_purge_limited_to_withdrawn_procedure(self):
        media = self.root / "media"
        for oid in [BLOCKED, ALLOWED]:
            directory = media / oid
            directory.mkdir(parents=True)
            for filename in ["fixture.pdf", "photo.jpg", "plan.png"]:
                (directory / filename).write_bytes(b"synthetic")
        self.assertEqual(policy.enforce_media(media, check=True), 1)
        self.assertTrue((media / BLOCKED / "fixture.pdf").exists())
        self.assertEqual(policy.enforce_media(media), 1)
        self.assertFalse((media / BLOCKED).exists())
        self.assertEqual(len(list((media / ALLOWED).iterdir())), 3)

    def test_media_symlink_does_not_delete_external_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        media = self.root / "media"
        media.mkdir()
        (media / BLOCKED).symlink_to(outside, target_is_directory=True)
        policy.enforce_media(media)
        self.assertTrue((outside / "keep.txt").exists())

    def test_data_symlink_rejected(self):
        target = self.root / "target.json"
        target.write_text("{}")
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            policy.enforce_file(link)

    def test_forced_detail_backfill_blocked_before_request(self):
        with self.assertRaisesRegex(RuntimeError, "Zurückgezogenes"):
            enrich_details.select_candidates([record(BLOCKED)], datetime.now(timezone.utc), [BLOCKED])
        self.assertFalse(enrich_details.should_fetch(record(BLOCKED)))

    def test_media_and_photo_queues_exclude_withdrawn(self):
        self.assertFalse(cache_media.active_record(record(BLOCKED), 100000))
        self.assertFalse(extract_report_photos.active_record(record(BLOCKED), 100000))
        self.assertEqual(extract_report_photos.pending_reports([record(BLOCKED)], 100000, {BLOCKED}, datetime.now(timezone.utc)), [])

    def test_history_never_reintroduces_removed_record_in_sample(self):
        before, current = self.root / "before.json", self.root / "current.json"
        before.write_text(json.dumps({"results": [record(BLOCKED), record(ALLOWED)]}))
        current.write_text(json.dumps({"results": [record(ALLOWED)]}))
        with patch.object(sys, "argv", ["merge_history.py", "--before", str(before), "--data", str(current)]):
            merge_history.main()
        self.assertNotIn(BLOCKED, current.read_text())
        self.assertEqual(json.loads(current.read_text())["meta"]["history_tracking"]["removed_sample"], [])

    def test_backfill_rejected_before_any_report_or_process(self):
        spec = importlib.util.spec_from_file_location("backfill_policy_test", ROOT / ".github/scripts/backfill-documents.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(ValueError, "Zurückgezogenes"):
            module.run(self.root / "missing.json", self.root / "reports", [BLOCKED],
                       lambda *a, **kw: self.fail("Process must not be invoked"))
        self.assertFalse((self.root / "reports").exists())


if __name__ == "__main__":
    unittest.main()
