"""Exercise the real regional merge using temporary synthetic JSON only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = 'sh-schlha-teil-b'


def row(identifier='sh-official-old', **changes):
    return dict({'id': identifier, 'state_code': 'sh', 'source_adapter': ADAPTER,
                 'source_url': 'https://example.invalid/issue.pdf',
                 'court': 'Falsches Gericht', 'file_number': '12 K 4/26',
                 'auction_date': '2030-01-01T10:00', 'market_value': 120000,
                 'source_data_status': 'verified_notice'}, **changes)


class SHMergeTests(unittest.TestCase):
    def merge(self, before, incoming, *, success=True):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'base.json'
            supplement = Path(temporary) / 'supplement.json'
            original = json.dumps({'meta': {}, 'results': before})
            base.write_text(original)
            supplement.write_text(json.dumps({'meta': {'state_code': 'sh',
                'source_parser_version': 2}, 'results': incoming}))
            result = subprocess.run([sys.executable,
                str(ROOT / 'collector/merge_supplemental.py'), '--data', str(base),
                '--supplement', str(supplement)], capture_output=True, text=True)
            if success:
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(base.read_text(), original)
            return json.loads(base.read_text())['results']

    def test_verified_same_id_replaces_incorrect_court_and_date(self):
        old = row()
        fresh = row(court='Lübeck', auction_date='2030-11-20T08:30', market_value=169000,
                    source_notice_hash='synthetic-verified-notice')
        self.assertEqual(self.merge([old], [fresh]), [fresh])

    def test_failed_source_rows_and_unread_sources_are_unchanged(self):
        old = row(history=[{'market_value': 100000}])
        unread = row('sh-official-unread', source_url='https://example.invalid/other.pdf')
        result = self.merge([old, unread], [copy.deepcopy(old)])
        self.assertEqual({r['id']: r for r in result}, {r['id']: r for r in [old, unread]})

    def test_portal_other_state_and_other_adapter_are_unchanged(self):
        old = row()
        others = [row('sh-123', source_adapter=None),
                  row('he-123', state_code='he', source_adapter=None),
                  row('sh-other', source_adapter='other-provider')]
        fresh = row(court='Lübeck')
        result = self.merge([old] + others, [fresh])
        self.assertEqual({r['id']: r for r in result},
                         {r['id']: r for r in [fresh] + others})

    def test_same_case_notices_and_review_ids_stay_separate(self):
        first = row('sh-official-first', court='Lübeck')
        second = row('sh-official-second', court='Lübeck')
        pending = row(source_data_status='unverified', court=None,
                      auction_date=None, market_value=None)
        result = self.merge([row()], [first, second, pending])
        self.assertEqual(len(result), 3)
        self.assertEqual({r['id'] for r in result}, {first['id'], second['id'], pending['id']})
        self.assertEqual(next(r for r in result if r['id'] == pending['id']), pending)

    def test_foreign_incoming_row_rejected_without_changing_data(self):
        self.merge([row()], [row(state_code='he')], success=False)

    def test_duplicate_incoming_ids_rejected_without_changing_data(self):
        self.merge([row()], [row(), row(court='Lübeck')], success=False)


if __name__ == '__main__':
    unittest.main()
