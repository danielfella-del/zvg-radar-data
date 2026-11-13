"""Offline regressions for nationwide attachment refresh and error fairness."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
import collect
import enrich_details as details

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def record(number=9001, state="he", **values):
    item = {
        "id": f"{state}-{number}", "zvg_id": str(number), "state_code": state,
        "auction_date": "2027-01-25T09:30", "last_updated": "2026-11-09T09:10",
        "source_url": details.canonical_detail_url(state, str(number)),
        "file_number": "0043 K 0023/2026", "attachments": [
            {"type": "bekanntmachung", "file_id": "8001", "url": "https://www.zvg-portal.de/index.php?button=showAnhang&land_abk=he&file_id=8001&zvg_id=" + str(number)},
        ],
    }
    item.update(values)
    return item


def successful(**values):
    return record(attachment_sync_version=details.ATTACHMENT_SYNC_VERSION,
                  detail_fetched_at=(NOW - timedelta(hours=1)).isoformat(),
                  detail_source_last_updated="2026-11-09T09:10", **values)


class QueueTests(unittest.TestCase):
    def test_never_fetched_is_eligible(self):
        self.assertTrue(details.should_fetch(record(), NOW))

    def test_unattempted_newer_procedure_precedes_recent_old_error(self):
        failed = record(1, auction_date="2026-11-14T09:00", detail_error="Aktenzeichen",
                        detail_attempted_at=(NOW - timedelta(hours=7)).isoformat())
        untouched = record(2, "by", auction_date="2027-03-01T09:00")
        self.assertEqual([r["id"] for r in details.select_candidates([failed, untouched], NOW)], ["by-2", "he-1"])

    def test_legacy_error_is_tried_once_then_does_not_block(self):
        failed, untouched = record(1, detail_error="error"), record(2, "sn")
        selected = details.select_candidates([failed, untouched], NOW)
        self.assertEqual(selected[0]["id"], "he-1")
        failed["detail_attempted_at"] = NOW.isoformat()
        self.assertEqual(details.select_candidates([failed, untouched], NOW), [untouched])

    def test_error_backoff_boundary(self):
        r = record(detail_error="temporary error", detail_attempted_at=(NOW - timedelta(hours=5, minutes=59)).isoformat())
        self.assertFalse(details.should_fetch(r, NOW))
        r["detail_attempted_at"] = (NOW - details.DETAIL_RETRY_DELAY).isoformat()
        self.assertTrue(details.should_fetch(r, NOW))

    def test_current_success_is_not_requested_again(self):
        self.assertFalse(details.should_fetch(successful(), NOW))

    def test_unmodified_success_gets_periodic_recheck(self):
        r = successful()
        r["detail_fetched_at"] = (NOW - details.DETAIL_MAX_AGE).isoformat()
        self.assertTrue(details.should_fetch(r, NOW))

    def test_source_change_is_due_before_periodic_age(self):
        r = successful(last_updated="2026-11-13T10:00")
        self.assertTrue(details.should_fetch(r, NOW))

    def test_future_or_invalid_timestamp_does_not_hide_record(self):
        for value in ["broken", "2099-01-01T00:00:00Z", None]:
            r = successful()
            r["detail_fetched_at"] = value
            self.assertTrue(details.should_fetch(r, NOW))

    def test_past_and_cancelled_are_not_in_normal_queue(self):
        for r in [record(auction_date="2026-11-12T23:59"), record(cancelled=True)]:
            self.assertFalse(details.should_fetch(r, NOW))

    def test_today_and_unknown_dates_remain_eligible(self):
        for value in ["2026-11-13T08:00", None, "unknown"]:
            self.assertTrue(details.should_fetch(record(auction_date=value), NOW))

    def test_german_calendar_date_used_near_midnight(self):
        late_utc = datetime(2026, 11, 13, 23, 30, tzinfo=timezone.utc)
        self.assertFalse(details.should_fetch(record(auction_date="2026-11-13T23:59"), late_utc))
        self.assertTrue(details.should_fetch(record(auction_date="2026-11-14T08:00"), late_utc))

    def test_all_16_states_can_be_selected(self):
        self.assertEqual(set(collect.STATES), details.PORTAL_STATES)
        rows = [record(i + 1, state) for i, state in enumerate(collect.STATES)]
        self.assertEqual(len(details.select_candidates(rows, NOW)), 16)

    def test_supplementary_nonportal_records_are_not_reassigned(self):
        r = record(zvg_id=None, source_url="https://example.invalid/official-notice.pdf")
        self.assertFalse(details.should_fetch(r, NOW))

    def test_bad_or_foreign_portal_identity_is_not_requested(self):
        for values in [
            {"zvg_id": "1&land_abk=by"}, {"state_code": "xx"},
            {"source_url": details.canonical_detail_url("by", "9001")},
            {"source_url": details.canonical_detail_url("he", "123")},
            {"source_url": "https://example.invalid/index.php?button=showZvg&land_abk=he&zvg_id=9001"},
            {"source_url": "https://[invalid/index.php"},
        ]:
            self.assertFalse(details.should_fetch(record(**values), NOW), values)

    def test_manual_selection_can_check_past_and_retrying_record(self):
        r = record(auction_date="2026-01-01", cancelled=True, detail_error="error", detail_attempted_at=NOW.isoformat())
        self.assertEqual(details.select_candidates([r], NOW, [r["id"]]), [r])

    def test_manual_unknown_or_foreign_identity_fails_explicitly(self):
        with self.assertRaises(RuntimeError):
            details.select_candidates([record()], NOW, ["he-99999"])
        r = record(source_url=details.canonical_detail_url("by", "9001"))
        with self.assertRaises(RuntimeError):
            details.select_candidates([r], NOW, [r["id"]])

    def test_collector_preserves_attempt_and_error_metadata(self):
        old = record(detail_attempted_at=NOW.isoformat(), detail_error="failed")
        fresh = record(last_updated="2026-11-13T12:01")
        collect.preserve_enrichment([fresh], [old])
        self.assertEqual(fresh["detail_attempted_at"], old["detail_attempted_at"])
        self.assertEqual(fresh["detail_error"], "failed")

    def test_list_without_report_preserves_confirmed_report(self):
        old = successful(detail_attempted_at=NOW.isoformat())
        report = {"type": "gutachten", "file_id": "8002", "cached_url": "https://example.invalid/8002.pdf"}
        old["attachments"].append(report)
        old["detail_attachments"] = copy.deepcopy(old["attachments"])
        fresh = record()
        collect.preserve_enrichment([fresh], [old])
        self.assertTrue(fresh["has_report"])
        self.assertIn(report, fresh["attachments"])


HTML = b'''<html><table id="anzeige">
<tr><td>Aktenzeichen:</td><td>43 K 23/2026</td></tr>
<tr><td>Art der Versteigerung:</td><td>Zwangsversteigerung</td></tr>
<tr><td>Objekt/Lage:</td><td>Beispielhaus, Beispielort</td></tr>
<tr><td>Gutachten:</td><td><a href="?button=showAnhang&amp;land_abk=he&amp;file_id=8002&amp;zvg_id=9001">Gutachten1.pdf</a> 100.00 kB</td></tr>
<tr><td>Amtliche Bekanntmachung:</td><td><a href="?button=showAnhang&amp;land_abk=he&amp;file_id=8001&amp;zvg_id=9001">Bekanntmachung.pdf</a> 10.00 kB</td></tr>
</table></html>'''


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


class Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, failure=False, prime_failure=False):
        self.headers = {}
        self.failure, self.prime_failure = failure, prime_failure
        self.post_count = self.get_count = 0

    def post(self, *args, **kwargs):
        self.post_count += 1
        if self.prime_failure:
            raise RuntimeError("source unavailable")
        return Response(b"showZvg")

    def get(self, *args, **kwargs):
        self.get_count += 1
        return Response(b"error" if self.failure else HTML)


class RunTests(unittest.TestCase):
    def run_pipeline(self, rows, session):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps({"meta": {}, "results": rows}))
            argv = ["enrich_details.py", "--data", str(path), "--limit", "25", "--pause", "0"]
            with patch.object(sys, "argv", argv), patch.object(details.requests, "Session", return_value=session), patch.object(details, "datetime", FrozenDateTime), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(details.main(), 0)
            return json.loads(path.read_text())

    def test_success_imports_missing_report_and_marks_check(self):
        result = self.run_pipeline([record()], FakeSession())
        r = result["results"][0]
        self.assertTrue(r["has_report"])
        self.assertEqual([a["file_id"] for a in r["attachments"]], ["8002", "8001"])
        self.assertEqual(r["detail_attempted_at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(r["detail_fetched_at"], r["detail_attempted_at"])
        self.assertIsNone(r["detail_error"])

    def test_invalid_detail_retains_files_and_is_deferred(self):
        before = record()
        result = self.run_pipeline([before], FakeSession(failure=True))
        r = result["results"][0]
        self.assertEqual(r["attachments"], before["attachments"])
        self.assertNotIn("detail_fetched_at", r)
        self.assertTrue(r["detail_error"])
        self.assertFalse(details.should_fetch(r, NOW))
        self.assertEqual(result["meta"]["detail_enrichment"]["active_errors_total"], 1)
        self.assertEqual(result["meta"]["detail_enrichment"]["retry_deferred"], 1)

    def test_one_state_failure_is_not_repeated_for_every_object(self):
        session = FakeSession(prime_failure=True)
        result = self.run_pipeline([record(9001), record(9002)], session)
        self.assertEqual(session.post_count, 1)
        self.assertEqual(session.get_count, 0)
        self.assertEqual(result["meta"]["detail_enrichment"]["errors_this_run"], 2)
        for r in result["results"]:
            self.assertTrue(r["detail_error"])
            self.assertTrue(r["attachments"])

    def test_past_record_data_not_deleted_or_rewritten(self):
        before = record(auction_date="2026-10-01")
        session = FakeSession()
        result = self.run_pipeline([before], session)
        self.assertEqual(result["results"], [before])
        self.assertEqual(session.get_count, 0)

    def test_parser_rejects_foreign_and_malformed_attachment_ids(self):
        for content in [HTML.replace(b"file_id=8002", b"file_id=bad"),
                        HTML.replace(b"land_abk=he", b"land_abk=by"),
                        HTML.replace(b"zvg_id=9001", b"zvg_id=9002")]:
            with self.assertRaises(RuntimeError):
                details.parse_detail_html(content, "he", "9001", "43 K 23/2026")

    def test_verified_portal_case_variants_keep_missing_report(self):
        for heading, expected in [
            (b"K 0018/ 2026", "K 0018/2026"),
            (b"0005 a K 0017/ 2025", "K 0017/2025"),
            (b"0005 a K 0017/ 2025", "5a K 17/25"),
            (b"0001 K K 0012/ 2025", "K 0012/2025"),
            (b"0001 K K 0012/ 2025", "1 K 12/2025"),
        ]:
            content = HTML.replace(b"43 K 23/2026", heading)
            d = details.parse_detail_html(content, "he", "9001", expected)
            self.assertTrue(any(a["type"] == "gutachten" for a in d["detail_attachments"]), (heading, expected))

    def test_different_case_components_are_still_rejected(self):
        for heading, expected in [
            (b"43 K 23/2026", "44 K 23/2026"),
            (b"43 K 23/2026", "43 K 24/2026"),
            (b"43 K 23/2026", "43 K 23/2025"),
            (b"0005 a K 0017/ 2025", "5b K 17/2025"),
            (b"K 0017/ 2025", "5 K 17/2025"),
            (b"K 0017/ 2025", "no case"),
        ]:
            with self.assertRaises(RuntimeError, msg=(heading, expected)):
                details.parse_detail_html(HTML.replace(b"43 K 23/2026", heading), "he", "9001", expected)

    def test_case_in_description_cannot_replace_missing_heading(self):
        content = HTML.replace(b"43 K 23/2026", b"Kein Aktenzeichen")
        content = content.replace(b"Beispielhaus, Beispielort", b"Beispielhaus, Bezug auf 43 K 23/2026")
        with self.assertRaises(RuntimeError):
            details.parse_detail_html(content, "he", "9001", "43 K 23/2026")

    def test_case_with_only_number_and_year_is_not_enough(self):
        with self.assertRaises(RuntimeError):
            details.parse_detail_html(HTML.replace(b"43 K 23/2026", b"23/2026"), "he", "9001", "K 23/2026")

    def test_truncated_response_cannot_remove_unseen_attachments(self):
        for content in [HTML.split(b"<tr><td>Gutachten:")[0], HTML.replace(b"</html>", b"")]:
            with self.assertRaises(RuntimeError):
                details.parse_detail_html(content, "he", "9001", "43 K 23/2026")


if __name__ == "__main__":
    # Any accidentally introduced live request fails, rather than touching data.
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Network disabled in offline tests")):
        unittest.main()
