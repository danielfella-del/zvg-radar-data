#!/usr/bin/env python3
"""Private runtime orchestration. Only counts, hashes and fixed statuses are logged."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / 'collector'))
from private_runtime import Client, RuntimeFailure, NoRedirect, MAX_MEDIA, MAX_SNAPSHOT, digest, file_digest, media_key, media_reference, media_url
from publication_policy import sanitize_payload, _read_private_hashes

REPOSITORY = 'danielfella-del/zvg-radar-data'
DATA = Path('data/auctions.json')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(payload):
    if (not isinstance(payload, dict) or not isinstance(payload.get('meta'), dict)
            or not isinstance(payload.get('results'), list) or len(payload['results']) < 500):
        raise RuntimeFailure('Unvollständiger privater Datenbestand; keine Veröffentlichung.')
    ids = [row.get('id') if isinstance(row, dict) else None for row in payload['results']]
    if any(not isinstance(oid, str) or not oid or len(oid.encode()) > 160 for oid in ids) or len(ids) != len(set(ids)):
        raise RuntimeFailure('Ungültige oder doppelte Objektzuordnung.')
    if sanitize_payload(payload) != payload:
        raise RuntimeFailure('Veröffentlichungsrücknahme nicht vollständig angewendet.')


def walk_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, str):
        yield value


def references(payload):
    found = {}
    for value in walk_strings(payload):
        ref = media_reference(value)
        if ref:
            path, sha = ref
            found.setdefault(path, set()).add(sha)
        elif value.startswith('media/'):
            media_key(value)
            found.setdefault(value, set())
        elif ('raw.githubusercontent.com/' + REPOSITORY + '/') in value and '/media/' in value:
            raise RuntimeFailure('Nicht unterstützter öffentlicher Medienverweis.')
    return found


def rewrite_urls(value, mapping):
    if isinstance(value, dict):
        return {key: rewrite_urls(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_urls(item, mapping) for item in value]
    if isinstance(value, str):
        ref = media_reference(value)
        if ref:
            return mapping[ref[0]]
    return value


def refresh_policy(client):
    reply = client.post({'action': 'publication_policy'})
    hashes = reply.get('withdrawn_id_hashes')
    if not isinstance(hashes, list) or any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in hashes):
        raise RuntimeFailure('Private Veröffentlichungsrichtlinie ungültig.')
    normalized = sorted(set(hashes))
    raw = json.dumps(normalized, separators=(',', ':')).encode()
    if reply.get('policy_sha256') != digest(raw):
        raise RuntimeFailure('Private Veröffentlichungsrichtlinie nicht bestätigt.')
    path = Path('withdrawals.json').resolve()
    path.write_bytes(raw)
    _read_private_hashes.cache_clear()
    os.environ['ZVGPRO_WITHDRAWAL_HASHES_FILE'] = str(path)


def publish(client, previous_generation, trusted_refs=None):
    refresh_policy(client)
    payload = sanitize_payload(json.loads(DATA.read_bytes()))
    validate(payload)
    mapping, uploaded = {}, 0
    for path, hashes in sorted(references(payload).items()):
        local = Path(path)
        if local.is_symlink():
            raise RuntimeFailure('Medienverknüpfungen sind nicht zulässig.')
        if local.is_file():
            sha = file_digest(local)
            # Pinned unchanged private media need not be uploaded each time.
            if sha not in (trusted_refs or {}).get(path, set()):
                receipt = client.ensure_media(local, key=path)
                if receipt.get('sha256') != sha:
                    raise RuntimeFailure('Privates Medium nicht bestätigt.')
                uploaded += 1
        else:
            known = {sha for sha in hashes if sha}
            if len(known) != 1 or None in hashes:
                raise RuntimeFailure('Referenziertes Medium fehlt; kein Snapshot veröffentlicht.')
            sha = next(iter(known))
        mapping[path] = media_url(path, sha)
    payload = rewrite_urls(payload, mapping)
    validate(payload)
    DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    receipt = client.upload(DATA, 'snapshot', expected_generation=previous_generation)
    if not re.fullmatch(r'[0-9a-f]{32}', str(receipt.get('generation', ''))):
        raise RuntimeFailure('Private Snapshotgeneration nicht bestätigt.')
    print(json.dumps({'private_snapshot': 'verified', 'generation': receipt['generation'],
                      'sha256': receipt['sha256'], 'records': len(payload['results']), 'media_verified': uploaded}))
    return receipt


def command(script, *args, optional=False, timeout=600):
    try:
        result = subprocess.run([sys.executable, str(CODE / 'collector' / script), *args],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        okay = result.returncode == 0
    except subprocess.TimeoutExpired:
        okay = False
    if not okay and not optional:
        raise RuntimeFailure('Privater Verarbeitungsschritt fehlgeschlagen; kein Snapshot veröffentlicht.')
    print(json.dumps({'step': script, 'status': 'ok' if okay else 'source_error_preserved'}))


def sync(raw):
    module = load_module('private_db_sync', CODE / '.github/scripts/sync-zvgpro-db.py')
    return module.run(raw, module.make_post(os.environ['ZVGPRO_DB_SYNC_TOKEN']))


def run_update():
    shutil.copyfile(DATA, 'before.json')
    command('collect.py', '--output', str(DATA))
    for state in ('bw', 'hh', 'mv', 'sh'):
        args = ['--output', f'data/supplemental_{state}.json']
        if state in ('bw', 'sh'):
            args += ['--previous', 'before.json']
        command(f'collect_{state}_official.py', *args, optional=True, timeout=180)
    args = [arg for state in ('bw', 'hh', 'mv', 'sh') for arg in ('--supplement', f'data/supplemental_{state}.json')]
    command('merge_supplemental.py', '--data', str(DATA), *args)
    command('merge_history.py', '--before', 'before.json', '--data', str(DATA))
    command('coverage_audit.py', '--data', str(DATA))
    command('enrich_details.py', '--data', str(DATA), '--limit', '20')
    command('cache_media.py', '--data', str(DATA), '--media-dir', 'media', '--quick-only',
            '--max-files', '180', '--max-run-mb', '120', '--max-file-mb', '12', optional=True, timeout=360)


def run_enrich():
    command('enrich_details.py', '--data', str(DATA), '--limit', '25')
    command('cache_media.py', '--data', str(DATA), '--media-dir', 'media', '--max-files', '80',
            '--max-run-mb', '90', '--max-file-mb', '24', optional=True, timeout=600)
    command('extract_report_photos.py', '--data', str(DATA), '--media-dir', 'media', '--max-reports', '12',
            '--max-photos', '2', '--max-report-mb', '180', optional=True, timeout=480)
    command('analyze_documents.py', '--data', str(DATA), '--repo-root', '.', '--limit', '20', optional=True, timeout=360)


class BootstrapSource:
    """One explicit immutable-source migration, unreachable in regular runs."""
    def __init__(self, sha):
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise RuntimeFailure('Vollständiger Bootstrap-Commit erforderlich.')
        self.sha = sha
        self.opener = urllib.request.build_opener(NoRedirect())
        self.check_head()
        tree = self.api('/git/trees/' + sha + '?recursive=1')
        if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
            raise RuntimeFailure('Quellbaum unvollständig; Migration abgebrochen.')
        self.entries = {entry['path']: entry for entry in tree['tree'] if entry.get('type') == 'blob'}

    def api(self, suffix):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'ZVGPro-Private-Migration/1'}
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + suffix, headers=headers)
        with self.opener.open(req, timeout=90) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise RuntimeFailure('Quellbaum überschreitet das Limit.')
        return json.loads(raw)

    def check_head(self):
        if self.api('/git/ref/heads/main').get('object', {}).get('sha') != self.sha:
            raise RuntimeFailure('Main wurde seit der Migrationsfreigabe geändert.')

    def download(self, path, target):
        if path != 'data/auctions.json':
            media_key(path)  # Includes withdrawal policy before any HTTP request.
        entry = self.entries.get(path)
        maximum = MAX_SNAPSHOT if path == 'data/auctions.json' else MAX_MEDIA
        if not entry or type(entry.get('size')) is not int or not 0 < entry['size'] <= maximum:
            raise RuntimeFailure('Quellmedium fehlt oder überschreitet das Limit.')
        sha = hashlib.sha1(b'blob ' + str(entry['size']).encode() + b'\0')
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        url = 'https://raw.githubusercontent.com/' + REPOSITORY + '/' + self.sha + '/' + urllib.parse.quote(path, safe='/')
        count = 0
        with self.opener.open(urllib.request.Request(url, headers={'User-Agent': 'ZVGPro-Private-Migration/1'}), timeout=120) as response, target.open('wb') as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                count += len(chunk)
                if count > entry['size']:
                    raise RuntimeFailure('Quelldatei überschreitet bestätigte Größe.')
                sha.update(chunk)
                output.write(chunk)
        if count != entry['size'] or sha.hexdigest() != entry['sha']:
            raise RuntimeFailure('Quellobjekt ist nicht unverändert bestätigt.')


def bootstrap(client, health):
    if health.get('active'):
        raise RuntimeFailure('Bootstrap nach Aktivierung gesperrt.')
    source = BootstrapSource(os.environ.get('PRIVATE_RUNTIME_SOURCE_SHA', ''))
    source.download('data/auctions.json', DATA)
    payload = sanitize_payload(json.loads(DATA.read_bytes()))
    validate(payload)
    DATA.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    mapping, trusted, count, byte_count = {}, {}, 0, 0
    for path in sorted(references(payload)):
        source.download(path, path)
        receipt = client.ensure_media(path, key=path)
        mapping[path] = media_url(path, receipt['sha256'])
        trusted[path] = {receipt['sha256']}
        count += 1
        byte_count += receipt['bytes']
        Path(path).unlink()
    payload = rewrite_urls(payload, mapping)
    DATA.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    source.check_head()
    current = client.post({'action': 'snapshot_info'})
    receipt = publish(client, current.get('generation', ''), trusted_refs=trusted)
    # Compare every transferred byte again before the separate cutover step.
    exported, info = client.snapshot()
    if info['generation'] != receipt['generation'] or digest(exported) != receipt['sha256']:
        raise RuntimeFailure('Privater Bootstrap-Nachweis fehlgeschlagen.')
    print(json.dumps({'bootstrap': 'verified', 'source_sha': source.sha, 'sha256': info['sha256'],
                      'generation': info['generation'], 'media_verified': count, 'media_bytes': byte_count, 'active': False}))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['update', 'enrich', 'sync', 'backfill', 'diagnose-media', 'diagnose-zvg', 'diagnose-sync', 'health', 'bootstrap', 'activate'])
    action = parser.parse_args(argv).action
    os.umask(0o077)
    client = Client()
    health = client.health(require_active=action not in ('health', 'bootstrap', 'activate'))
    if action == 'health':
        print(json.dumps({'protocol': 'private-runtime-v1', 'storage_ready': True, 'active': health['active']}))
        return
    parent = Path(os.environ.get('PRIVATE_RUNTIME_ROOT', tempfile.gettempdir() + '/zvg-private-runtime')).resolve()
    if parent == CODE or CODE in parent.parents:
        raise RuntimeFailure('Privates Laufzeitverzeichnis liegt im öffentlichen Checkout.')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='run-', dir=parent) as temporary:
        try:
            os.chdir(temporary)
            Path('data').mkdir()
            Path('media').mkdir()
            refresh_policy(client)
            os.environ['ZVGPRO_PRIVATE_RUNTIME'] = '1'
            if action == 'bootstrap':
                bootstrap(client, health)
                return
            raw, info = client.snapshot()
            payload = json.loads(raw)
            validate(payload)
            # Active jobs cannot use a snapshot containing a GitHub media fallback.
            if any(None in hashes for hashes in references(payload).values()):
                raise RuntimeFailure('Öffentliche Medienreferenz im privaten Snapshot.')
            DATA.write_bytes(raw)
            if action == 'activate':
                if (info['generation'] != os.environ.get('PRIVATE_RUNTIME_GENERATION')
                        or info['sha256'] != os.environ.get('PRIVATE_RUNTIME_SHA256')):
                    raise RuntimeFailure('Freigegebene Migrationsgeneration wurde geändert.')
                sync(raw)
                receipt = client.post({'action': 'activate', 'generation': info['generation'], 'sha256': info['sha256']})
                if receipt.get('active') is not True or client.health().get('active') is not True:
                    raise RuntimeFailure('Private Aktivierung nicht bestätigt.')
                print(json.dumps({'activation': 'verified', 'generation': info['generation'], 'sha256': info['sha256']}))
            elif action == 'sync':
                sync(raw)
            elif action.startswith('diagnose-'):
                print(json.dumps({'private_diagnostic': 'verified', 'records': len(payload['results']), 'sha256': info['sha256']}))
            else:
                if action == 'update':
                    run_update()
                elif action == 'enrich':
                    run_enrich()
                elif action == 'backfill':
                    module = load_module('private_backfill', CODE / '.github/scripts/backfill-documents.py')
                    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                        module.run(DATA.resolve(), Path('diagnostics/backfill'), module.target_ids(os.environ.get('ZVG_BACKFILL_IDS', '')))
                publish(client, info['generation'], trusted_refs=references(payload))
        finally:
            os.chdir(previous_cwd)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise InterruptedError()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # Exceptions from libraries/remote servers may include record data or secrets.
        label = str(error) if isinstance(error, RuntimeFailure) else 'Private Verarbeitung fehlgeschlagen; kein bestätigter Abschluss.'
        print('::error::' + label, file=sys.stderr)
        raise SystemExit(1)
