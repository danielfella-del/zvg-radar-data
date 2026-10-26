"""Sync regressions: synthetic snapshots and mocked urllib only, never HTTP/DB."""
import contextlib
import errno
import hashlib
import http.client
import importlib.util
import io
import json
import socket
import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'sync_client', Path(__file__).resolve().parents[1] / '.github/scripts/sync-zvgpro-db.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

SECRET = 'fixture-token-never-a-real-secret'
PRIVATE = 'fixture-object-content-not-for-logs'


def response(value):
    return io.BytesIO(sync.encode(value))


def http_error(code, body=b''):
    return urllib.error.HTTPError('https://example.invalid/' + SECRET, code,
                                  SECRET, {'X-Secret': SECRET}, io.BytesIO(body))


class SyncServer:
    """Minimal responses for the reviewed PHP contract; no database emulation."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.processed = 0
        self.hashes = {}
        self.result = None
        self.dataset_hash = None
        self.run_id = 42

    def __call__(self, payload):
        self.calls.append(payload)
        action = payload['action']
        if action == 'ping':
            return {'ok': True, 'sync_protocol': sync.PROTOCOL}
        if action == 'begin':
            self.dataset_hash = payload['dataset_hash']
            return {'ok': True, 'run_id': self.run_id,
                    'total': len(self.rows), 'processed': self.processed}
        if action == 'batch':
            offset = payload['offset']
            digest = hashlib.sha256(sync.encode(payload['objects'])).hexdigest()
            if offset in self.hashes:
                assert self.hashes[offset] == digest
                return {'ok': True, 'already_processed': True, 'processed': self.processed}
            assert offset == self.processed
            assert payload['objects'] == self.rows[offset:offset + len(payload['objects'])]
            self.hashes[offset] = digest
            self.processed += len(payload['objects'])
            return {'ok': True, 'processed': self.processed}
        if action == 'finalize':
            assert self.processed == len(self.rows)
            self.result = {'ok': True, 'run_id': self.run_id, 'count': len(self.rows),
                           'inserted': len(self.rows), 'updated': 0, 'unchanged': 0, 'deactivated': 0}
            return self.result
        if action == 'status':
            assert payload['dataset_hash'] == self.dataset_hash
            return {'ok': True, 'completed': self.result is not None, 'result': self.result}
        if action == 'abort':
            assert payload['run_id'] == self.run_id
            assert payload['dataset_hash'] == self.dataset_hash
            return {'ok': True, 'already_completed': True} if self.result else {
                'ok': True, 'aborted': True, 'run_id': self.run_id}
        raise AssertionError('Unexpected fixture action')


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.build = patch.object(sync.urllib.request, 'build_opener', return_value=self.opener).start()
        self.sleep = patch.object(sync.time, 'sleep').start()
        self.addCleanup(patch.stopall)
        self.stderr = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.payload = {'action': 'batch', 'run_id': 42, 'offset': 5,
                        'objects': [{'id': 'fixture', 'description': PRIVATE}]}

    def post(self):
        return sync.make_post(SECRET)(self.payload)

    def assert_safe(self, error=None):
        text = self.stderr.getvalue() + (str(error) if error else '')
        for secret in (SECRET, PRIVATE, 'Bearer', 'X-Secret', 'forged-log-line'):
            self.assertNotIn(secret, text)

    def test_temporary_url_error_retries_same_request_then_succeeds(self):
        self.opener.open.side_effect = [urllib.error.URLError(OSError(errno.ECONNRESET, SECRET)),
                                        response({'ok': True})]
        self.assertTrue(self.post()['ok'])
        self.assertEqual(self.opener.open.call_count, 2)
        self.sleep.assert_called_once_with(3)
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertTrue(all(call.kwargs['timeout'] == 60 for call in self.opener.open.call_args_list))
        self.assertIn('Aktion batch', self.stderr.getvalue())
        self.assertIn('Offset 5', self.stderr.getvalue())
        self.assertIn('Versuch 1/3', self.stderr.getvalue())
        self.assertIn('connection_reset', self.stderr.getvalue())
        self.assertIn('errno=' + str(errno.ECONNRESET), self.stderr.getvalue())
        self.assert_safe()

    def test_incomplete_response_uses_existing_retry(self):
        self.opener.open.side_effect = [http.client.IncompleteRead(b''), response({'ok': True})]
        self.assertTrue(self.post()['ok'])
        self.assertEqual(self.opener.open.call_count, 2)
        self.sleep.assert_called_once_with(3)
        self.assertIn('incomplete_response', self.stderr.getvalue())
        self.assert_safe()

    def test_incomplete_read_from_response_body_retries(self):
        broken = Mock()
        broken.__enter__ = Mock(return_value=broken)
        broken.__exit__ = Mock(return_value=False)
        broken.read.side_effect = http.client.IncompleteRead(SECRET.encode())
        self.opener.open.side_effect = [broken, response({'ok': True})]
        self.assertTrue(self.post()['ok'])
        self.assertEqual(self.opener.open.call_count, 2)
        self.assert_safe()

    def test_http_error_body_is_not_read_or_logged(self):
        for code, attempts in ((401, 1), (503, 3)):
            with self.subTest(code=code):
                error = http_error(code)
                error.read = Mock(side_effect=http.client.IncompleteRead(SECRET.encode()))
                self.opener.open.reset_mock()
                self.opener.open.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    self.post()
                self.assertEqual(self.opener.open.call_count, attempts)
                error.read.assert_not_called()
                self.assertIn('http_error', str(caught.exception))
                self.assertIn('HTTP=' + str(code), str(caught.exception))
                self.assert_safe(caught.exception)

    def test_http_error_free_text_and_encoded_secret_never_logged(self):
        body = sync.encode({'error': PRIVATE + '\n::error::forged-log-line ' +
                            SECRET.replace('-', '%2D')})
        self.opener.open.side_effect = http_error(400, body)
        with self.assertRaises(RuntimeError) as caught:
            self.post()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assert_safe(caught.exception)
        self.assertNotIn('%2D', str(caught.exception))

    def test_final_transport_failure_keeps_three_attempts(self):
        self.opener.open.side_effect = urllib.error.URLError('forged-log-line ' + SECRET)
        with self.assertRaises(RuntimeError) as caught:
            self.post()
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [3, 6])
        self.assertIn('url_error', str(caught.exception))
        self.assertIn('Versuch 3/3', str(caught.exception))
        self.assert_safe(caught.exception)

    def test_existing_http_retry_policy_is_preserved(self):
        for code, attempts in ((302, 1), (400, 1), (401, 1), (403, 1), (413, 1),
                               (408, 3), (429, 3), (500, 3), (503, 3)):
            with self.subTest(code=code):
                self.opener.open.reset_mock()
                self.sleep.reset_mock()
                self.opener.open.side_effect = http_error(code)
                with self.assertRaises(RuntimeError):
                    self.post()
                self.assertEqual(self.opener.open.call_count, attempts)
                self.assertEqual(self.sleep.call_count, attempts - 1)

    def test_safe_error_categories_do_not_print_reason_strings(self):
        cert = ssl.SSLCertVerificationError(1, SECRET)
        cert.verify_code = 20
        cases = [(socket.gaierror(socket.EAI_AGAIN, SECRET), 'name_resolution'),
                 (TimeoutError(SECRET), 'timeout'), (cert, 'tls_certificate'),
                 (ssl.SSLError(1, SECRET), 'tls_error'),
                 (OSError(errno.ECONNREFUSED, SECRET), 'connection_refused')]
        for reason, category in cases:
            with self.subTest(category=category):
                self.opener.open.side_effect = urllib.error.URLError(reason)
                with self.assertRaises(RuntimeError) as caught:
                    self.post()
                self.assertIn(category, str(caught.exception))
                self.assert_safe(caught.exception)

    def test_invalid_json_and_encoding_retry_without_dump(self):
        for invalid in (SECRET.encode(), b'\xff' + SECRET.encode()):
            with self.subTest(invalid=invalid[:1]):
                self.opener.open.side_effect = [io.BytesIO(invalid), response({'ok': True})]
                self.assertTrue(self.post()['ok'])
                self.assertIn('invalid_response', self.stderr.getvalue())
                self.assert_safe()

    def test_redirect_does_not_forward_either_auth_header(self):
        self.assertIsNone(sync.NoRedirect().redirect_request(Mock(), Mock(), 302,
                          SECRET, {}, 'https://example.invalid/' + SECRET))
        self.opener.open.side_effect = http_error(302)
        with self.assertRaises(RuntimeError):
            self.post()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertIsInstance(self.build.call_args.args[0], sync.NoRedirect)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': 'fixture-' + str(i), 'description': PRIVATE} for i in range(500)]
        self.raw = sync.encode({'meta': {'generated_at': '2026-10-26T00:00:00Z'}, 'results': self.rows})
        self.server = SyncServer(self.rows)
        self.output = io.StringIO()

    def run_sync(self, post):
        with contextlib.redirect_stdout(self.output):
            return sync.run(self.raw, post)

    def test_success_preserves_full_objects_and_finishes_once(self):
        self.assertEqual(self.run_sync(self.server)['count'], 500)
        self.assertEqual(sum(p['action'] == 'begin' for p in self.server.calls), 1)
        self.assertEqual(sum(p['action'] == 'finalize' for p in self.server.calls), 1)
        self.assertFalse(any(p['action'] == 'abort' for p in self.server.calls))
        self.assertNotIn(PRIVATE, self.output.getvalue())

    def test_missing_or_invalid_run_id_never_aborts_an_unknown_run(self):
        for value in (None, 0, -1, SECRET):
            with self.subTest(value=value):
                self.server = SyncServer(self.rows)
                def post(payload):
                    reply = self.server(payload)
                    if payload['action'] == 'begin': reply['run_id'] = value
                    return reply
                with self.assertRaises(RuntimeError) as caught:
                    self.run_sync(post)
                self.assertEqual([p['action'] for p in self.server.calls], ['ping', 'begin'])
                self.assertNotIn(SECRET, str(caught.exception))

    def test_lost_batch_reply_retries_identical_packet_without_duplicate_import(self):
        lost = False
        def open_request(req, **kwargs):
            nonlocal lost
            payload = json.loads(req.data)
            result = self.server(payload)
            if payload['action'] == 'batch' and not lost:
                lost = True
                raise urllib.error.URLError(OSError(errno.ECONNRESET, SECRET))
            return response(result)
        opener = Mock(open=Mock(side_effect=open_request))
        with patch.object(sync.urllib.request, 'build_opener', return_value=opener), \
                patch.object(sync.time, 'sleep'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_sync(sync.make_post(SECRET))['count'], 500)
        batches = [p for p in self.server.calls if p['action'] == 'batch']
        self.assertEqual(batches[0], batches[1])
        self.assertEqual(self.server.processed, 500)
        self.assertFalse(any(p['action'] == 'abort' for p in self.server.calls))

    def test_batch_failure_aborts_only_owned_snapshot_and_never_finalizes(self):
        def post(payload):
            if payload['action'] == 'batch':
                self.server.calls.append(payload)
                raise RuntimeError('simulated transport exhausted')
            return self.server(payload)
        with self.assertRaisesRegex(RuntimeError, 'simulated transport exhausted'):
            self.run_sync(post)
        abort = self.server.calls[-1]
        self.assertEqual(abort, {'action': 'abort', 'run_id': 42,
                                'dataset_hash': hashlib.sha256(self.raw).hexdigest()})
        self.assertIn('Abbruch bestätigt', self.output.getvalue())
        self.assertFalse(any(p['action'] == 'finalize' for p in self.server.calls))

    def test_abort_failure_preserves_original_error_and_does_not_force_unlock(self):
        def post(payload):
            if payload['action'] == 'batch':
                raise RuntimeError('original batch failure')
            if payload['action'] == 'abort':
                self.server.calls.append(payload)
                raise RuntimeError('abort failure')
            return self.server(payload)
        with self.assertRaisesRegex(RuntimeError, 'original batch failure'):
            self.run_sync(post)
        self.assertIn('Abbruch konnte nicht bestätigt', self.output.getvalue())
        self.assertEqual([p['action'] for p in self.server.calls], ['ping', 'begin', 'abort'])

    def test_abort_contract_outcomes_are_distinguished(self):
        cases = [({'ok': True}, 'Abbruch konnte nicht bestätigt'),
                 ({'ok': True, 'aborted': True, 'run_id': 99}, 'Abbruch konnte nicht bestätigt'),
                 ({'ok': True, 'already_completed': True}, 'bereits abgeschlossen'),
                 ({'ok': True, 'not_active': True}, 'nicht mehr aktiv'),
                 ({'ok': True, 'not_running': True}, 'nicht mehr laufend')]
        for reply, message in cases:
            with self.subTest(reply=reply):
                self.output = io.StringIO()
                def post(payload):
                    if payload['action'] == 'batch': raise RuntimeError('batch failure')
                    if payload['action'] == 'abort': return reply
                    return self.server(payload)
                with self.assertRaisesRegex(RuntimeError, 'batch failure'):
                    self.run_sync(post)
                self.assertIn(message, self.output.getvalue())
                self.assertNotIn('Abbruch bestätigt.', self.output.getvalue())

    def test_lost_finalize_uses_read_only_receipt_without_new_begin_or_abort(self):
        def post(payload):
            result = self.server(payload)
            if payload['action'] == 'finalize': raise RuntimeError('final response lost')
            return result
        self.assertEqual(self.run_sync(post)['count'], 500)
        actions = [p['action'] for p in self.server.calls]
        self.assertEqual(actions.count('begin'), 1)
        self.assertEqual(actions[-2:], ['finalize', 'status'])
        self.assertIn('über Beleg bestätigt', self.output.getvalue())

    def test_three_lost_finalize_responses_recover_through_status(self):
        def open_request(req, **kwargs):
            payload = json.loads(req.data)
            reply = self.server(payload)
            if payload['action'] == 'finalize':
                raise http.client.IncompleteRead(SECRET.encode())
            return response(reply)
        opener = Mock(open=Mock(side_effect=open_request))
        with patch.object(sync.urllib.request, 'build_opener', return_value=opener), \
                patch.object(sync.time, 'sleep') as sleep, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_sync(sync.make_post(SECRET))['count'], 500)
        self.assertEqual([p['action'] for p in self.server.calls][-4:],
                         ['finalize', 'finalize', 'finalize', 'status'])
        self.assertEqual(sum(p['action'] == 'begin' for p in self.server.calls), 1)
        self.assertFalse(any(p['action'] == 'abort' for p in self.server.calls))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [3, 6])
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_failed_status_query_is_uncertain_and_still_only_owned_abort(self):
        def post(payload):
            if payload['action'] == 'finalize': raise RuntimeError('final response lost')
            if payload['action'] == 'status':
                self.server.calls.append(payload)
                raise RuntimeError('status unavailable')
            return self.server(payload)
        with self.assertRaisesRegex(RuntimeError, 'status unavailable'):
            self.run_sync(post)
        self.assertEqual([p['action'] for p in self.server.calls][-2:], ['status', 'abort'])
        self.assertNotIn('über Beleg bestätigt', self.output.getvalue())

    def test_missing_or_malformed_completion_never_logs_confirmed_receipt(self):
        for result in (None, {'ok': True, 'run_id': 42, 'count': 499},
                       {'ok': True, 'run_id': 99, 'count': 500},
                       {'ok': True, 'run_id': 42, 'count': SECRET}):
            with self.subTest(result=result):
                self.server = SyncServer(self.rows)
                self.output = io.StringIO()
                def post(payload):
                    if payload['action'] == 'finalize': raise RuntimeError('final response lost')
                    if payload['action'] == 'status': return {'ok': True, 'completed': True, 'result': result}
                    return self.server(payload)
                with self.assertRaises(RuntimeError) as caught:
                    self.run_sync(post)
                self.assertNotIn('über Beleg bestätigt', self.output.getvalue())
                self.assertNotIn(SECRET, str(caught.exception) + self.output.getvalue())
                self.assertEqual(self.server.calls[-1]['action'], 'abort')

    def test_no_completion_receipt_preserves_error_and_aborts(self):
        def post(payload):
            if payload['action'] == 'finalize': raise RuntimeError('final response lost')
            return self.server(payload)
        with self.assertRaisesRegex(RuntimeError, 'final response lost'):
            self.run_sync(post)
        self.assertEqual([p['action'] for p in self.server.calls][-2:], ['status', 'abort'])

    def test_response_fields_and_summary_cannot_leak_secrets(self):
        for action, field in (('begin', 'run_id'), ('begin', 'processed'),
                              ('begin', 'total'), ('batch', 'processed'), ('finalize', 'count')):
            with self.subTest(action=action, field=field):
                self.server = SyncServer(self.rows)
                self.output = io.StringIO()
                def post(payload):
                    reply = self.server(payload)
                    if payload['action'] == action: reply[field] = SECRET
                    return reply
                with self.assertRaises(RuntimeError) as caught:
                    self.run_sync(post)
                self.assertNotIn(SECRET, str(caught.exception) + self.output.getvalue())
        self.server = SyncServer(self.rows)
        def post(payload):
            reply = self.server(payload)
            if payload['action'] == 'finalize': reply['updated'] = SECRET
            return reply
        self.run_sync(post)
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_fragment_hash_rejection_never_finalizes(self):
        self.rows[0]['details'] = [{'key': PRIVATE, 'value': i} for i in range(1000)]
        self.raw = sync.encode({'results': self.rows})
        self.server = SyncServer(self.rows)
        def post(payload):
            if payload['action'] == 'object_part':
                self.server.calls.append(payload)
                return {'ok': True, 'part_index': payload['part_index'],
                        'received_sha256': '0' * 64, 'processed': 0}
            return self.server(payload)
        with self.assertRaisesRegex(RuntimeError, 'Objektteil'):
            self.run_sync(post)
        self.assertTrue(any(p['action'] == 'object_part' for p in self.server.calls))
        self.assertFalse(any(p['action'] in ('object_commit', 'finalize') for p in self.server.calls))


if __name__ == '__main__':
    unittest.main()
