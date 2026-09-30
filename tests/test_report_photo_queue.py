import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
import extract_report_photos as photos
from collect import preserve_enrichment


class ReportQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.report = {'type': 'gutachten', 'file_id': '111', 'url': 'https://example.invalid/111.pdf', 'size_kb': 20}
        self.record = {'id': 'he-1', 'attachments': [self.report]}

    def queue(self, records=None):
        return photos.pending_reports(records or [self.record], 120, set(), self.now)

    def test_completed_report_is_skipped_even_after_collector_refresh(self):
        photos.mark_report(self.report, 'no_photos', self.now)
        fresh = {'id': 'he-1', 'attachments': [{k: v for k, v in self.report.items() if k != 'report_photo_check'}]}
        preserve_enrichment([fresh], [self.record])
        self.assertEqual(self.queue([fresh]), [])

    def test_changed_report_is_due_even_with_two_existing_photos(self):
        photos.mark_report(self.report, 'photos', self.now)
        self.record['attachments'] += [{'type': 'foto', 'preview_url': 'one'}, {'type': 'foto', 'preview_url': 'two'}]
        self.assertEqual(self.queue(), [])
        self.report['size_kb'] = 21
        self.assertEqual(len(self.queue()), 1)

    def test_every_report_is_considered_and_fetch_timestamps_do_not_reset_check(self):
        photos.mark_report(self.report, 'no_photos', self.now)
        self.report['cached_at'] = self.now.isoformat()
        self.record['detail_fetched_at'] = self.now.isoformat()
        second = dict(self.report, file_id='222', url='https://example.invalid/222.pdf')
        second.pop('report_photo_check')
        self.record['attachments'].append(second)
        self.assertEqual(self.queue(), [(self.record, second)])

    def test_errors_back_off_and_new_reports_take_priority(self):
        photos.mark_report(self.report, 'error', self.now, 'timeout')
        self.assertEqual(self.queue(), [])
        self.now += timedelta(hours=7)
        new = {'id': 'he-2', 'attachments': [dict(self.report, file_id='new', url='https://example.invalid/new.pdf')]}
        new['attachments'][0].pop('report_photo_check')
        queued = photos.pending_reports([self.record, new], 120, {'he-1'}, self.now)
        self.assertEqual(queued[0][0]['id'], 'he-2')
        self.assertEqual(len(queued), 2)

    def test_changed_source_failure_keeps_refresh_requirement(self):
        photos.mark_report(self.report, 'photos', self.now)
        self.report['size_kb'] = 30
        photos.mark_report(self.report, 'error', self.now, 'timeout')
        self.assertTrue(self.report['report_photo_check']['refresh_required'])
        photos.mark_report(self.report, 'error', self.now + timedelta(hours=7), 'timeout')
        self.assertTrue(self.report['report_photo_check']['refresh_required'])

    def test_replacement_preserves_original_photos_and_other_report_photos(self):
        old = {'type': 'foto', 'photo_source': 'gutachten', 'generated_from_file_id': '111'}
        other = dict(old, generated_from_file_id='222')
        original = {'type': 'foto', 'preview_url': 'original'}
        self.record['attachments'] += [old, other, original]
        photos.replace_report_photos(self.record, self.report, [])
        self.assertNotIn(old, self.record['attachments'])
        self.assertIn(other, self.record['attachments'])
        self.assertIn(original, self.record['attachments'])

    def test_main_persists_no_photo_result_and_advances_to_next_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'auctions.json'
            second = copy.deepcopy(self.record)
            second['id'] = 'he-2'
            data.write_text(json.dumps({'results': [self.record, second]}))
            argv = ['extract', '--data', str(data), '--media-dir', str(Path(tmp) / 'media'), '--max-reports', '1']
            with patch.object(sys, 'argv', argv), patch.object(photos, 'prepare_context', return_value='context'), patch.object(photos, 'download_pdf', return_value=(Path(tmp) / 'test.pdf', 100)), patch.object(photos, 'save_photos', return_value=[]) as save:
                photos.main()
                photos.main()
                photos.main()
                self.assertEqual(save.call_count, 2)
            results = json.loads(data.read_text())['results']
            self.assertTrue(all(r['attachments'][0]['report_photo_check']['status'] == 'no_photos' for r in results))


if __name__ == '__main__':
    unittest.main()
