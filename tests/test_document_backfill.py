import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backfill', ROOT / '.github/scripts/backfill-documents.py')
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def record(oid):
    land, zvg = oid.split('-')
    return {'id': oid, 'state_code': land, 'zvg_id': zvg,
            'source_url': f'https://www.zvg-portal.de/index.php?button=showZvg&land_abk={land}&zvg_id={zvg}',
            'attachments': []}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'data.json'
        self.output = Path(self.tmp.name) / 'report'
        self.before = {'meta': {'marker': 'keep'}, 'results': [record('he-1'), record('nw-2'), record('be-3')]}
        self.data.write_text(json.dumps(self.before))

    def simulate(self, command, **kwargs):
        self.assertNotIn('shell', kwargs)
        self.assertEqual(command[-4:], ['--object-id', 'he-1', '--object-id', 'nw-2'])
        payload = json.loads(self.data.read_text())
        a, b, unrelated = payload['results']
        a.update(detail_attempted_at='2026-11-16T10:00:00+00:00', detail_fetched_at='2026-11-16T10:00:00+00:00', detail_error=None)
        a['attachments'] = [{'type': 'gutachten', 'file_id': '11'}]
        b.update(detail_attempted_at='2026-11-16T10:00:00+00:00', detail_error='source unavailable')
        unrelated['dossier_completeness'] = {'score': 100}
        self.data.write_text(json.dumps(payload))

    def test_partial_source_failure_counted_and_unrelated_object_preserved(self):
        summary = backfill.run(self.data, self.output, ['he-1', 'nw-2'], self.simulate)
        self.assertEqual((summary['checked'], summary['errors'], summary['new_report_links']), (1, 1, 1))
        after = json.loads(self.data.read_text())
        self.assertEqual(after['results'][2], self.before['results'][2])
        self.assertEqual(after['meta'], self.before['meta'])
        self.assertEqual(json.loads((self.output / 'result.json').read_text()), summary)

    def test_invalid_and_empty_input_rejected(self):
        for value in ['', 'he-1;echo test', '../he-1', 'he-1 $(touch file)', 'he-1\nnw-invalid']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                backfill.target_ids(value)
        self.assertEqual(backfill.target_ids('he-1, he-1\nnw-2'), ['he-1', 'nw-2'])

    def test_limit(self):
        with self.assertRaises(ValueError):
            backfill.target_ids(' '.join(f'he-{i}' for i in range(1001)))

    def test_unknown_or_wrong_source_does_not_call_collector(self):
        def forbidden(*args, **kwargs):
            self.fail('collector must not be called')
        with self.assertRaises(ValueError):
            backfill.run(self.data, self.output, ['he-99'], forbidden)
        self.before['results'][0]['source_url'] = 'https://example.invalid/'
        self.data.write_text(json.dumps(self.before))
        with self.assertRaises(ValueError):
            backfill.run(self.data, self.output, ['he-1'], forbidden)

    def test_collector_abort_does_not_report_success(self):
        def fail(*args, **kwargs):
            raise subprocess.CalledProcessError(2, 'collector')
        with self.assertRaises(subprocess.CalledProcessError):
            backfill.run(self.data, self.output, ['he-1'], fail)
        self.assertFalse((self.output / 'result.json').exists())

    def test_missing_attempt_rejected(self):
        with self.assertRaises(ValueError):
            backfill.run(self.data, self.output, ['he-1'], lambda *a, **kw: None)

    def test_changed_population_rejected(self):
        def remove(*args, **kwargs):
            payload = json.loads(self.data.read_text())
            payload['results'].pop()
            self.data.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            backfill.run(self.data, self.output, ['he-1'], remove)
        self.assertFalse((self.output / 'result.json').exists())


if __name__ == '__main__':
    unittest.main()
