#!/usr/bin/env python3
"""Synthetic transport/policy/cutover regressions; no production connections."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'collector'))
import private_runtime as runtime
import publication_policy as policy
spec = importlib.util.spec_from_file_location('private_pipeline', ROOT / '.github/scripts/private-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class Store:
    def __init__(self):
        self.raw = b''
        self.generation = ''
        self.uploads = {}
        self.media = {}
        self.calls = []
        self.active = True
        self.lose_commit = False
        self.corrupt_chunk = False
        self.withdrawn = []

    def info(self):
        return dict(exists=bool(self.raw), generation=self.generation, sha256=runtime.digest(self.raw), bytes=len(self.raw))

    def post(self, request):
        request = dict(request)
        self.calls.append(dict(request))
        action = request.pop('action')
        if action == 'health':
            return dict(ok=True, protocol=runtime.PROTOCOL, storage_ready=True, active=self.active)
        if action == 'publication_policy':
            hashes = sorted(self.withdrawn)
            return dict(withdrawn_id_hashes=hashes, policy_sha256=runtime.digest(json.dumps(hashes, separators=(',', ':')).encode()))
        if action == 'snapshot_info':
            return self.info()
        if action == 'upload_begin':
            if request['kind'] == 'snapshot' and request['expected_generation'] != self.generation:
                raise runtime.RuntimeFailure('Privater Speicher HTTP 409.')
            upload_id = f'{len(self.uploads)+1:032x}'
            self.uploads[upload_id] = dict(request, raw=b'')
            return dict(upload_id=upload_id, chunk_bytes=65536)
        if action == 'upload_chunk':
            item = self.uploads[request['upload_id']]
            raw = base64.b64decode(request['data'])
            assert request['offset'] == len(item['raw'])
            assert runtime.digest(raw) == request['sha256']
            item['raw'] += raw
            return dict(received_bytes=len(item['raw']))
        if action == 'upload_commit':
            item = self.uploads[request['upload_id']]
            assert runtime.digest(item['raw']) == item['sha256'] and len(item['raw']) == item['bytes']
            if item['kind'] == 'snapshot':
                if item['expected_generation'] != self.generation:
                    raise runtime.RuntimeFailure('Privater Speicher HTTP 409.')
                self.raw, self.generation = item['raw'], request['upload_id']
                reply = self.info()
            else:
                self.media[(item['object_id'], item['filename'], item['sha256'])] = item['raw']
                reply = dict(sha256=item['sha256'], bytes=item['bytes'])
            if self.lose_commit:
                raise runtime.RuntimeFailure('Private Operation ohne bestätigte Antwort.')
            return reply
        if action == 'snapshot_chunk':
            if request['generation'] != self.generation:
                raise runtime.RuntimeFailure('Privater Speicher HTTP 409.')
            return self.chunk(self.raw, request['offset'], generation=self.generation)
        if action in ('media_info', 'media_chunk'):
            raw = self.media.get((request['object_id'], request['filename'], request['sha256']))
            if raw is None:
                raise runtime.RuntimeFailure('Privater Speicher HTTP 404.', status=404)
            if action == 'media_info':
                return dict(sha256=runtime.digest(raw), bytes=len(raw))
            return self.chunk(raw, request['offset'])
        raise AssertionError('Unexpected operation')

    def chunk(self, raw, offset, **extra):
        chunk = raw[offset:offset+65536]
        return dict(offset=offset, bytes=len(chunk), data=base64.b64encode(chunk).decode(), total_bytes=len(raw),
                    sha256=('0'*64 if self.corrupt_chunk else runtime.digest(chunk)), eof=offset+len(chunk)==len(raw), **extra)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop('ZVGPRO_PRIVATE_RUNTIME', None)
        os.environ.pop('ZVGPRO_WITHDRAWAL_HASHES_FILE', None)
        policy._read_private_hashes.cache_clear()
        self.store = Store()
        self.client = runtime.Client(post=self.store.post)

    def tearDown(self):
        os.chdir(self.previous)
        self.env.stop()
        self.temp.cleanup()
        policy._read_private_hashes.cache_clear()

    def source(self, raw, name='input.json'):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_snapshot_roundtrip_exact_bytes_and_chunks(self):
        raw = ('{"field":"' + 'Ä' * 90000 + '"}').encode()
        receipt = self.client.upload(self.source(raw), 'snapshot', expected_generation='')
        actual, info = self.client.snapshot()
        self.assertEqual(raw, actual)
        self.assertEqual(receipt['sha256'], info['sha256'])
        self.assertGreater(sum(c['action']=='upload_chunk' for c in self.store.calls), 2)

    def test_snapshot_compare_and_swap_does_not_overwrite(self):
        self.client.upload(self.source(b'old'), 'snapshot', expected_generation='')
        with self.assertRaises(runtime.RuntimeFailure):
            self.client.upload(self.source(b'new'), 'snapshot', expected_generation='')
        self.assertEqual(self.store.raw, b'old')

    def test_lost_commit_uses_readonly_receipt(self):
        self.store.lose_commit = True
        receipt = self.client.upload(self.source(b'synthetic'), 'snapshot', expected_generation='')
        self.assertEqual(receipt['sha256'], runtime.digest(b'synthetic'))
        self.assertEqual(sum(c['action']=='upload_begin' for c in self.store.calls), 1)
        self.assertEqual(self.store.calls[-1]['action'], 'snapshot_info')

    def test_media_roundtrip_pinned_hash(self):
        raw = b'%PDF-synthetic\n' + b'x' * 140000
        receipt = self.client.upload(self.source(raw), 'media', key='media/test-1/report.pdf')
        self.client.media('media/test-1/report.pdf', receipt['sha256'], 'download.pdf')
        self.assertEqual(Path('download.pdf').read_bytes(), raw)

    def test_corrupt_chunk_never_leaves_output(self):
        raw = b'%PDF-synthetic'
        self.client.upload(self.source(raw), 'media', key='media/test-1/report.pdf')
        self.store.corrupt_chunk = True
        with self.assertRaises(runtime.RuntimeFailure):
            self.client.media('media/test-1/report.pdf', runtime.digest(raw), 'output.pdf')
        self.assertFalse(Path('output.pdf').exists())
        self.assertFalse(Path('output.pdf.part').exists())

    def test_missing_asset_does_not_fallback(self):
        with self.assertRaises(runtime.RuntimeFailure):
            self.client.media('media/test-1/report.pdf', '1'*64, 'output.pdf')
        self.assertEqual([c['action'] for c in self.store.calls], ['media_info'])

    def test_bootstrap_retry_skips_only_verified_media(self):
        raw = b'%PDF-synthetic'
        path = self.source(raw)
        self.client.ensure_media(path, key='media/test-1/report.pdf')
        self.store.calls.clear()
        receipt = self.client.ensure_media(path, key='media/test-1/report.pdf')
        self.assertEqual(receipt['sha256'], runtime.digest(raw))
        self.assertEqual([c['action'] for c in self.store.calls], ['media_info'])

    def test_media_store_failure_is_not_treated_as_absence(self):
        with patch.object(self.client, 'post', side_effect=runtime.RuntimeFailure('Privater Speicher HTTP 503.', status=503)):
            with self.assertRaises(runtime.RuntimeFailure):
                self.client.ensure_media(self.source(b'%PDF-synthetic'), key='media/test-1/report.pdf')

    def test_inactive_store_blocks_regular_processing(self):
        self.store.active = False
        with self.assertRaises(runtime.RuntimeFailure):
            self.client.health()
        self.assertFalse(self.client.health(require_active=False)['active'])

    def test_path_traversal_and_withdrawal_rejected_before_transport(self):
        with patch.object(policy, 'WITHDRAWN_ID_HASHES', frozenset({runtime.digest(b'zz-900000001')})):
            for path in ('media/../file.pdf', 'media/test-1/../file.pdf', '/media/test-1/a.pdf', 'media/zz-900000001/a.pdf'):
                with self.assertRaises(runtime.RuntimeFailure):
                    runtime.media_key(path)
        self.assertEqual(self.store.calls, [])

    def test_dynamic_withdrawals_apply_to_nested_data(self):
        self.store.withdrawn = [runtime.digest(b'test-99')]
        pipeline.refresh_policy(self.client)
        os.environ['ZVGPRO_PRIVATE_RUNTIME'] = '1'
        payload = {'results':[{'id':'test-99'}, {'id':'test-2'}], 'history':['media/test-99/a.pdf']}
        self.assertEqual(policy.sanitize_payload(payload), {'results':[{'id':'test-2'}], 'history':[]})
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.media_key('media/test-99/a.pdf')

    def test_missing_dynamic_policy_fails_closed(self):
        os.environ['ZVGPRO_PRIVATE_RUNTIME'] = '1'
        with self.assertRaises(ValueError):
            policy.withdrawn_id('test-1')

    def payload(self, attachment=None):
        rows = [{'id':f'test-{i}', 'attachments':[]} for i in range(500)]
        if attachment:
            rows[0]['attachments']=[attachment]
        return {'meta': {'count':500}, 'results':rows}

    def test_new_hash_pinned_asset_is_uploaded_before_snapshot(self):
        path = 'media/test-0/report.pdf'
        raw = b'%PDF-synthetic'
        self.source(raw, path)
        payload = self.payload({'cached_path':path, 'cached_url':runtime.media_url(path, runtime.digest(raw))})
        self.source(json.dumps(payload).encode(), str(pipeline.DATA))
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.publish(self.client, '', trusted_refs={})
        begins = [c for c in self.store.calls if c['action']=='upload_begin']
        self.assertEqual([c['kind'] for c in begins], ['media','snapshot'])

    def test_missing_legacy_asset_prevents_snapshot(self):
        path = 'media/test-0/report.pdf'
        url = 'https://raw.githubusercontent.com/danielfella-del/zvg-radar-data/main/' + path
        self.source(json.dumps(self.payload({'cached_path':path,'cached_url':url})).encode(), str(pipeline.DATA))
        with self.assertRaises(runtime.RuntimeFailure):
            pipeline.publish(self.client, '')
        self.assertFalse(self.store.raw)

    def test_unchanged_pinned_asset_does_not_need_hydration(self):
        path, sha = 'media/test-0/report.pdf', 'a'*64
        self.source(json.dumps(self.payload({'cached_path':path,'cached_url':runtime.media_url(path,sha)})).encode(), str(pipeline.DATA))
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.publish(self.client, '', trusted_refs={path:{sha}})
        self.assertEqual([c['kind'] for c in self.store.calls if c['action']=='upload_begin'], ['snapshot'])

    def test_bootstrap_rejects_unpinned_source_without_network(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(runtime.RuntimeFailure):
                pipeline.BootstrapSource('main')
            opener.assert_not_called()

    def test_auth_headers_are_not_redirected(self):
        self.assertIsNone(runtime.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://example.invalid/'))

    def test_changed_report_updates_private_hash_before_analysis(self):
        import extract_report_photos as photos
        pipeline.refresh_policy(self.client)
        os.environ['ZVGPRO_PRIVATE_RUNTIME'] = '1'
        path = 'media/test-0/report.pdf'
        old, new = b'%PDF-synthetic-old', b'%PDF-synthetic-new'
        self.source(old, path)
        source = self.source(new, 'source.pdf')
        attachment = {'type':'gutachten', 'file_id':'1', 'url':'https://example.invalid/report.pdf',
                      'size_kb':1, 'cached_path':path, 'cached_url':runtime.media_url(path,runtime.digest(old))}
        photos.mark_report(attachment, 'photos', datetime.now(timezone.utc))
        attachment['size_kb'] = 2
        self.source(json.dumps({'results':[{'id':'test-0','attachments':[attachment]}]}).encode(), str(pipeline.DATA))
        with patch.object(sys,'argv',['extract']), patch.object(photos,'prepare_context',return_value='context'), \
                patch.object(photos,'download_pdf',return_value=(source,len(new))), patch.object(photos,'save_photos',return_value=[]), \
                patch.object(runtime.Client,'_make_post',side_effect=AssertionError('unexpected network')), \
                contextlib.redirect_stdout(io.StringIO()):
            photos.main()
            updated = json.loads(pipeline.DATA.read_text())['results'][0]['attachments'][0]
            self.assertEqual(updated['cached_url'], runtime.media_url(path,runtime.digest(new)))
            self.assertEqual(runtime.hydrate_attachment(updated).read_bytes(),new)


if __name__ == '__main__':
    unittest.main()
