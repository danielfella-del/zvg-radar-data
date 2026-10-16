"""Regressions from the official HH/MV PDFs and the October 2026 MV relaunch."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
from collect_mv_official import discover_pdfs, INDEX
from hh_mv_notices import (auction_datetime, collect_issues, issue_key,
                           market_value, market_values, parse_notices, property_location)
from merge_supplemental import reconcile_checked_notices

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(name, state, today=date(2026, 10, 16)):
    records, errors = parse_notices(state, 'https://example.invalid',
                                    (FIXTURES / name).read_text(), today=today)
    assert errors == [], errors
    return {r['file_number']: r for r in records}


class NoticeTests(unittest.TestCase):
    def test_hamburg_soft_hyphens_and_separate_cases(self):
        r = fixture('hh-00-notices.txt', 'hh')
        self.assertEqual(len(r), 3)
        self.assertEqual(r['71 K 56/25']['auction_date'], '2027-01-12T09:30')
        self.assertEqual(r['71 K 56/25']['market_value'], 2286000)
        self.assertEqual(r['802 K 44/24']['court'], 'Hamburg-Barmbek')
        self.assertEqual(r['802 K 44/24']['market_value'], 143000)
        self.assertIsNone(r['802 K 44/24']['postcode'])  # 22083 is the court's address
        self.assertEqual(r['802 K 42/25']['postcode'], '22395')
        self.assertEqual(r['802 K 42/25']['market_value'], 640000)

    def test_hamburg_neighboring_court_does_not_leak(self):
        r = fixture('hh-08-notices.txt', 'hh')
        self.assertEqual(r['417 K 10/25']['court'], 'Hamburg-Bergedorf')
        self.assertEqual(r['417 K 10/25']['market_value'], 596000)
        self.assertEqual(r['902 K 23/25']['auction_date'], '2026-11-26T11:00')

    def test_mv_not_publication_date_and_not_next_case(self):
        r = fixture('mv-00-notices.txt', 'mv')
        self.assertEqual(len(r), 3)
        self.assertEqual(r['41 K 43/24']['auction_date'], '2027-02-03T10:30')
        self.assertEqual(r['41 K 25/24']['auction_date'], '2027-01-20T10:00')
        self.assertEqual(r['41 K 43/24']['market_values'], [78000, 8000])
        self.assertIsNone(r['41 K 43/24']['market_value'])  # no invented total

    def test_cancellation_does_not_cancel_next_proceeding(self):
        r = fixture('mv-cancellation-notices.txt', 'mv', date(2026, 8, 1))
        self.assertEqual(r['41 K 24/25']['auction_date'], '2026-10-09T09:30')
        self.assertTrue(r['41 K 24/25']['cancelled'])
        self.assertFalse(r['15 K 35/25']['cancelled'])
        self.assertEqual(r['15 K 35/25']['auction_date'], '2026-11-26T09:30')
        self.assertEqual(r['15 K 35/25']['court'], 'Ludwigslust')
        self.assertEqual(r['15 K 35/25']['market_value'], 299000)

    def test_unrelated_liquidation_address_is_not_property_address(self):
        r = fixture('mv-08-notices.txt', 'mv')
        self.assertIsNone(r['69 K 63/25']['postcode'])
        self.assertIsNone(r['69 K 63/25']['city'])

    def test_cross_reference_does_not_start_another_notice(self):
        r = fixture('mv-30-notices.txt', 'mv', date(2026, 3, 1))
        self.assertEqual(r['701 K 32/23']['market_value'], 32000)
        self.assertEqual(r['701 K 62/22']['market_value'], 48000)

    def test_invalid_or_unanchored_dates_rejected(self):
        for text in ('Vom 3. März 2026 55 K 12/25', 'soll am 31. Februar 2027, 10 Uhr',
                     'soll am 3. März 2027, 55:00 Uhr', 'soll am 3. März 2027, 10:65 Uhr',
                     'soll am 3. März 2027 55 K 12/25'):
            self.assertIsNone(auction_datetime(text), text)
        self.assertEqual(auction_datetime('soll am Donnerstag, 11 Juni 2026, um 9:30 Uhr'), '2026-06-11T09:30')
        self.assertEqual(auction_datetime('soll am 03.03.2027, 9 Uhr'), '2027-03-03T09:00')

    def test_postcode_and_value_ambiguities(self):
        self.assertEqual(property_location('hh', 'Blatt 17999 BV 1.'), (None, None))
        self.assertEqual(property_location('mv', 'in 23996 Bad Kleinen, Hauptstraße'), ('23996', 'Bad Kleinen'))
        self.assertEqual(property_location('mv', 'in 19395 Plau am See, Hafen'), ('19395', 'Plau am See'))
        self.assertEqual(property_location('mv', 'in 17091 Groß Teetzleben, Straße'), ('17091', 'Groß Teetzleben'))
        text = 'Verkehrswert: 400.000,– Euro bzw. 200.000,– Euro je hälftigen Anteil.'
        self.assertEqual(market_value(text, market_values(text)), 400000)
        text = 'Verkehrswert: 384.844,– Euro. Lfd. Nummer 2 Verkehrswert: 156,– Euro. Gesamtverkehrswert lfd. Nummer 1 bis 3: 400.000,– €'
        self.assertEqual(market_value(text, market_values(text)), 400000)

    def test_mv_visible_and_collapsed_links(self):
        path = '/sites/default/files/2026-10/Amtlicher_Anzeiger_M-V_42_2026.pdf'
        hidden = '/sites/default/files/2026-07/Amtlicher_Anzeiger_M-V_16_2026.pdf'
        entry = {'name': 'Amtlicher_Anzeiger Nr. 16', 'field_media_document': {'uri': {'url': hidden}}}
        stream = '2a:' + json.dumps([entry]) + '\n'
        html = f'<a href="{path}">Amtlicher Anzeiger Nr. 42</a><button class="link-list__show-more">Mehr anzeigen</button>'
        script = '<script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'
        self.assertEqual(discover_pdfs(html + script), [INDEX.split('/landesregierung')[0] + path, INDEX.split('/landesregierung')[0] + hidden])
        with self.assertRaises(RuntimeError):
            discover_pdfs(html)
        self.assertEqual(discover_pdfs('<h1>Ministerium</h1>'), [])
        old = 'https://www.regierung-mv.de/static/Amtlicher%20Anzeiger%20_17_2026.pdf'
        self.assertEqual(issue_key('mv', old), 'mv:2026:17')

    def test_failed_issue_is_not_marked_as_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'mv.json')
            def broken(url):
                raise RuntimeError('temporary source failure')
            with self.assertRaises(RuntimeError):
                collect_issues('mv', INDEX, ['https://example.invalid'], broken, output)
            meta = json.loads(Path(output).read_text())['meta']
            self.assertFalse(meta['ok'])
            self.assertEqual(meta['checked_issues'], [])

    def test_merge_scope_failure_retention_and_correction(self):
        old = {'id': 'mv-one', 'state_code': 'mv', 'source_adapter': 'mv-amtlicher-anzeiger',
               'source_url': 'https://www.regierung-mv.de/static/Amtlicher_Anzeiger_M-V_33_2026.pdf',
               'cancelled': True, 'market_value': 1, 'history': [{'test': 'keep'}]}
        failed = {**old, 'id': 'mv-two', 'source_url': old['source_url'].replace('_33_', '_32_')}
        others = [{'id': state, 'state_code': state, 'unchanged': [1, {'data': 'original'}]}
                  for state in ('bw','by','be','br','hb','he','ni','nw','rp','sl','sn','st','sh','th')]
        portal = {'id': 'mv-portal', 'state_code': 'mv', 'source_adapter': 'portal'}
        rows = [old, failed, portal] + others
        snapshot = copy.deepcopy(rows)
        fresh = {**old, 'notice_parser_version': 2, 'cancelled': False, 'market_value': 299000}
        supplement = {'meta': {'state_code': 'mv', 'notice_parser_version': 2,
                       'checked_issues': ['mv:2026:33'], 'ok': False}, 'results': [fresh]}
        kept, incoming, count = reconcile_checked_notices(rows, supplement)
        self.assertEqual(count, 1)
        self.assertEqual(kept, [failed, portal] + others)
        self.assertFalse(incoming[0]['cancelled'])
        self.assertEqual(incoming[0]['history'], old['history'])
        self.assertEqual(incoming[0]['market_value'], 299000)
        self.assertEqual(rows, snapshot)
        legacy = {'meta': {}, 'results': [old]}
        self.assertEqual(reconcile_checked_notices(rows, legacy), (rows, [old], 0))


def run_tests():
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(NoticeTests))
    if not result.wasSuccessful():
        raise AssertionError('HH/MV notice regression tests failed')


if __name__ == '__main__':
    run_tests()
