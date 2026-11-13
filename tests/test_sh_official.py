"""Offline regression tests; no real network or production files."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
import collect_sh_official as sh

ISSUE = sh.PDF_TMPL.format(ym='209910')


def notice(case='28 K 14/26', court='Itzehoe', day='16. Dezember 2099', value='100.000', extra=''):
    return (f'Im Wege der Zwangsversteigerung soll folgender Grundbesitz am {day} um 9 Uhr, '
            f'Amtsgericht {court}, 25524 Gerichtsstadt, versteigert werden: '
            f'Einfamilienhaus in 24800 Objektstadt. Verkehrswert: {value} EUR. {extra} '
            f'{court}, den 1. Oktober 2026. ({case}) Amtsgericht {court}.')


def parsed(text=None, source=ISSUE):
    return sh.parse_notices(text or notice(), source, keep_past=True)


def old_record(**changes):
    row = parsed()[0]
    row.update(id='legacy-id', court='Falsches Nachbargericht', auction_date='2099-11-25T09:00')
    row.pop('source_notice_hash')
    row.pop('source_data_status')
    row.update(changes)
    return row


class SHTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_neighbor_fields_stay_in_their_own_notice(self):
        rows = parsed(notice(extra='Versteigerungstermin wird auf 1. Januar 2001 verwiesen.') + ' ' +
                      notice('52 K 15/24', 'Luebeck', '25. November 2099', '800.000'))
        self.assertEqual([(r['court'], r['auction_date'], r['market_value']) for r in rows],
                         [('Itzehoe', '2099-12-16T09:00', 100000), ('Luebeck', '2099-11-25T09:00', 800000)])
        self.assertEqual(rows[0]['postcode'], '24800')  # not the court address in the intro
        self.assertIsNone(rows[0]['description'])

    def test_footer_court_wins_over_court_mentioned_inside_body(self):
        row = parsed(notice(extra='Das Grundbuch liegt beim Amtsgericht Nachbarstadt.'))[0]
        self.assertEqual(row['court'], 'Itzehoe')

    def test_abbreviated_city_fragment_is_not_published_as_city(self):
        for printed in ('25365 Kl. Offenseth-Sparrieshoop.', '24800 Gr. Beispielort.', '24800 St. Beispielort.'):
            with self.subTest(printed=printed):
                row = parsed(notice().replace('24800 Objektstadt.', printed))[0]
                self.assertEqual(row['postcode'], printed[:5])
                self.assertIsNone(row['city'])
        self.assertEqual(parsed()[0]['city'], 'Objektstadt')

    def test_body_phrase_does_not_start_a_second_notice(self):
        row = parsed(notice(extra='Der Erwerb im Wege der Zwangsvollstreckung, durch den Insolvenzverwalter.'))[0]
        self.assertEqual(row['file_number'], '28 K 14/26')

    def test_zur_aufhebung_and_zwangsvollstreckung(self):
        text = notice().replace('Zwangsversteigerung soll', 'Zwangsversteigerung zur Aufhebung der Gemeinschaft soll')
        self.assertEqual(len(parsed(text)), 1)
        self.assertEqual(len(parsed(notice().replace('Zwangsversteigerung', 'Zwangsvollstreckung'))), 1)

    def test_missing_footer_rejects_entire_issue(self):
        with self.assertRaises(ValueError):
            parsed(notice() + notice('52 K 15/24').split('(52 K')[0])

    def test_footer_without_intro_rejects_entire_issue(self):
        with self.assertRaises(ValueError):
            parsed(notice() + ' (52 K 15/24) Amtsgericht Luebeck.')

    def test_reversed_footer_and_start_rejected(self):
        with self.assertRaises(ValueError):
            parsed('(28 K 14/26) Amtsgericht Itzehoe. ' + notice().split('(28 K')[0])

    def test_missing_term_does_not_use_publication_date(self):
        with self.assertRaises(ValueError):
            parsed(notice().replace('am 16. Dezember 2099 um 9 Uhr, ', ''))

    def test_impossible_date_rejected(self):
        with self.assertRaises(ValueError):
            parsed(notice(day='31. Februar 2099'))

    def test_missing_department_rejected(self):
        with self.assertRaises(ValueError):
            parsed(notice(case='K 14/26'))
        self.assertIsNone(sh.case_identity('K 14/26'))
        self.assertEqual(sh.case_identity('0028 K 0014/2026'), sh.case_identity('28 K 14/26'))
        self.assertNotEqual(sh.case_identity('28 A K 14/26'), sh.case_identity('28 K 14/26'))

    def test_repeated_identical_notice_rejects_issue(self):
        with self.assertRaises(ValueError):
            parsed(notice() + notice())

    def test_pdf_columns_and_page_continuation(self):
        import fitz
        first, second, third = notice(), notice('52 K 15/24', 'Luebeck'), notice('82 K 19/26', 'Neumuenster')
        with fitz.open() as doc:
            p = doc.new_page(width=600, height=800)
            p.insert_text((180, 20), 'Publication date 1. Oktober 2026', fontsize=12)
            # A starts left and finishes right; B then continues on page two.
            for rect, text in ((fitz.Rect(30, 50, 285, 250), first[:first.index('Einfamilienhaus')]),
                               (fitz.Rect(315, 50, 570, 250), first[first.index('Einfamilienhaus'):]),
                               (fitz.Rect(315, 300, 570, 500), second[:second.index('Einfamilienhaus')])):
                self.assertGreaterEqual(p.insert_textbox(rect, text, fontsize=10), 0)
            p.insert_text((290, 780), '169')
            p = doc.new_page(width=600, height=800)
            self.assertGreaterEqual(p.insert_textbox(fitz.Rect(30, 50, 285, 250), second[second.index('Einfamilienhaus'):], fontsize=10), 0)
            self.assertGreaterEqual(p.insert_textbox(fitz.Rect(315, 50, 570, 400), third, fontsize=10), 0)
            rows = sh.parse_issue(doc.tobytes(), ISSUE, keep_past=True)
        self.assertEqual([r['court'] for r in rows], ['Itzehoe', 'Luebeck', 'Neumuenster'])

    def test_corrected_court_date_retains_unique_old_id_and_attachments(self):
        old = old_record(attachments=[{'id': 'existing-file'}], has_report=True)
        rows, diagnostics = sh.preserve_record_ids(parsed(), [old], [ISSUE])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], old['id'])
        self.assertEqual((rows[0]['court'], rows[0]['auction_date']), ('Itzehoe', '2099-12-16T09:00'))
        self.assertEqual(rows[0]['attachments'], old['attachments'])
        self.assertTrue(rows[0]['has_report'])
        self.assertEqual(diagnostics, [])
        again, diagnostics = sh.preserve_record_ids(parsed(), rows, [ISSUE])
        self.assertEqual(rows, again)

    def test_failed_and_unchecked_issues_are_preserved_exactly(self):
        old = old_record()
        untouched = copy.deepcopy(old)
        rows, diagnostics = sh.preserve_record_ids([], [old], [])
        self.assertEqual(rows, [untouched])
        self.assertEqual(old, untouched)
        self.assertEqual(diagnostics, [])

    def test_different_issue_never_borrows_old_id(self):
        other = ISSUE.replace('209910', '209909')
        old = old_record(source_url=other)
        rows, _ = sh.preserve_record_ids(parsed(), [old], [ISSUE])
        self.assertEqual(len(rows), 2)
        self.assertIn(old, rows)
        self.assertNotEqual(rows[0]['id'], old['id'])

    def test_different_complete_case_never_borrows_old_id(self):
        old = old_record(file_number='82 K 14/26')
        rows, _ = sh.preserve_record_ids(parsed(), [old], [ISSUE])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['source_data_status'], 'unverified')
        self.assertIsNone(rows[1]['court'])
        self.assertEqual(rows[1]['source_review_previous']['court'], old['court'])

    def test_duplicate_old_case_is_quarantined_and_idempotent(self):
        olds = [old_record(id='old-a'), old_record(id='old-b')]
        rows, diagnostics = sh.preserve_record_ids(parsed(), olds, [ISSUE])
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(diagnostics), 2)
        for row in rows[1:]:
            self.assertEqual(row['source_data_status'], 'unverified')
            self.assertIsNone(row['auction_date'])
            self.assertIsNone(row['court'])
            self.assertEqual(row['attachments'], [])
        again, _ = sh.preserve_record_ids(parsed(), rows, [ISSUE])
        self.assertEqual(rows, again)

    def test_duplicate_new_case_stays_separate_and_idempotent(self):
        fresh = parsed(notice() + notice(court='Meldorf', value='800.000'))
        rows, _ = sh.preserve_record_ids(fresh, [old_record()], [ISSUE])
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r['id'] for r in rows}), 3)
        self.assertEqual(rows[-1]['source_data_status'], 'unverified')
        again, _ = sh.preserve_record_ids(fresh, rows, [ISSUE])
        self.assertEqual(rows, again)

    def test_partial_source_keeps_all_previous_rows_in_main(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            previous = directory / 'previous.json'
            output = directory / 'output.json'
            old = old_record()
            previous.write_text(json.dumps({'results': [old]}))
            response = SimpleNamespace(status_code=200, content=b'%PDFdummy', raise_for_status=lambda: None)
            with patch.object(sh, 'month_candidates', return_value=['209910']), \
                 patch.object(sh.requests.Session, 'get', return_value=response), \
                 patch.object(sh, 'parse_issue', side_effect=ValueError('missing footer')), \
                 patch.object(sys, 'argv', ['sh', '--previous', str(previous), '--output', str(output)]):
                sh.main()
            payload = json.loads(output.read_text())
        self.assertEqual(payload['results'], [old])
        self.assertEqual(payload['meta']['checked_sources'], [])
        self.assertEqual(len(payload['meta']['errors']), 1)

    def test_foreign_records_rejected_and_other_states_ignored(self):
        foreign = old_record(state_code='bw')
        with self.assertRaises(ValueError):
            sh.preserve_record_ids([foreign], [], [ISSUE])
        self.assertEqual(sh.preserve_record_ids([], [foreign], [ISSUE]), ([], []))

    def test_interrupt_checkpoints_previous_before_first_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / 'before.json'
            output = Path(directory) / 'out.json'
            old = old_record()
            previous.write_text(json.dumps({'results': [old]}))
            def interrupt(*args, **kwargs):
                checkpoint = json.loads(output.read_text())
                self.assertEqual(checkpoint['results'], [old])
                self.assertTrue(checkpoint['meta']['incomplete'])
                raise KeyboardInterrupt()
            with patch.object(sh.requests.Session, 'get', side_effect=interrupt), \
                 patch.object(sys, 'argv', ['sh', '--previous', str(previous), '--output', str(output)]):
                with self.assertRaises(KeyboardInterrupt):
                    sh.main()
            self.assertEqual(json.loads(output.read_text())['results'], [old])

    def test_without_previous_existing_output_survives_failed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            old = old_record()
            output.write_text(json.dumps({'results': [old]}))
            with patch.object(sh, 'month_candidates', return_value=['209910']), \
                 patch.object(sh.requests.Session, 'get', side_effect=ValueError('unreachable')), \
                 patch.object(sys, 'argv', ['sh', '--output', str(output)]):
                sh.main()
            self.assertEqual(json.loads(output.read_text())['results'], [old])

    def test_unchecked_incoming_cannot_replace_previous(self):
        with self.assertRaises(ValueError):
            sh.preserve_record_ids(parsed(), [old_record()], [])

    def test_past_notice_confirms_old_without_adding_new_past_objects(self):
        fresh = parsed(notice(day='16. Dezember 2001'))
        self.assertEqual(sh.preserve_record_ids(fresh, [], [ISSUE]), ([], []))
        rows, _ = sh.preserve_record_ids(fresh, [old_record()], [ISSUE])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'legacy-id')
        self.assertEqual(rows[0]['auction_date'], '2001-12-16T09:00')

    def test_payload_preserves_distinct_ids_with_same_case_and_date(self):
        rows = parsed(notice() + notice(court='Meldorf'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            sh.write_payload(output, rows, {'checked_sources': [ISSUE]}, announce=False)
            payload = json.loads(output.read_text())
            self.assertEqual(payload['meta']['source_parser_version'], 2)
            self.assertEqual(payload['meta']['state_code'], 'sh')
            self.assertEqual(len(payload['results']), 2)
            with self.assertRaises(ValueError):
                sh.write_payload(output, [rows[0], rows[0]], {}, announce=False)
            self.assertEqual(json.loads(output.read_text()), payload)


if __name__ == '__main__':
    unittest.main()
