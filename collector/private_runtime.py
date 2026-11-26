"""Authenticated private runtime transport; never persists data in the code tree."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL = 'private-runtime-v1'
ENDPOINT = 'https://zvgpro.de/api/runtime-storage.php'
MEDIA_ENDPOINT = 'https://zvgpro.de/api/media.php'
CHUNK_BYTES = 65536
MAX_SNAPSHOT = 256 * 1024 * 1024
MAX_MEDIA = 180 * 1024 * 1024
HASH = re.compile(r'[0-9a-f]{64}')
PART = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}')


class RuntimeFailure(RuntimeError):
    """Messages must contain only fixed labels and safe numeric status codes."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def media_key(path):
    parts = str(path).replace('\\', '/').split('/')
    if len(parts) != 3 or parts[0] != 'media' or any(not PART.fullmatch(p) or '..' in p for p in parts[1:]):
        raise RuntimeFailure('Ungültiger privater Medienpfad.')
    from publication_policy import withdrawn_id
    if withdrawn_id(parts[1]):
        raise RuntimeFailure('Zurückgezogenes Medium gesperrt.')
    return parts[1], parts[2]


def media_url(path, sha256=None):
    oid, filename = media_key(path)
    query = {'object_id': oid, 'asset': filename}
    if sha256 is not None:
        if not HASH.fullmatch(sha256):
            raise RuntimeFailure('Ungültiger Medienhash.')
        query['sha256'] = sha256
    return MEDIA_ENDPOINT + '?' + urllib.parse.urlencode(query)


def media_reference(value):
    """Return canonical local path/hash for this repository or private gateway."""
    if not isinstance(value, str):
        return None
    url = urllib.parse.urlsplit(value)
    if url.scheme == 'https' and url.netloc == 'raw.githubusercontent.com':
        parts = url.path.split('/')
        if len(parts) == 7 and parts[1:3] == ['danielfella-del', 'zvg-radar-data'] and parts[4] == 'media':
            path = '/'.join(parts[4:])
            media_key(path)
            return path, None
    if url.scheme == 'https' and url.netloc == 'zvgpro.de' and url.path == '/api/media.php':
        query = urllib.parse.parse_qs(url.query, strict_parsing=True)
        if set(query) != {'object_id', 'asset', 'sha256'} or any(len(v) != 1 for v in query.values()):
            raise RuntimeFailure('Privater Medienverweis ist nicht hashgebunden.')
        path = 'media/' + query['object_id'][0] + '/' + query['asset'][0]
        media_key(path)
        sha = query['sha256'][0]
        if not HASH.fullmatch(sha):
            raise RuntimeFailure('Ungültiger Medienhash.')
        return path, sha
    return None


class Client:
    def __init__(self, post=None, token=None):
        self.post = post or self._make_post(token or os.environ.get('ZVGPRO_DB_SYNC_TOKEN', ''))

    @staticmethod
    def _make_post(token):
        if not token.strip():
            raise RuntimeFailure('Privater Laufzeitzugang fehlt.')
        opener = urllib.request.build_opener(NoRedirect())

        def post(payload):
            body = json.dumps(dict(protocol=PROTOCOL, **payload), separators=(',', ':'), allow_nan=False).encode()
            if len(body) > 96 * 1024:
                raise RuntimeFailure('Privates Transportlimit überschritten.')
            for attempt in range(3):
                req = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
                    'X-ZVGPro-Sync-Token': token, 'Content-Type': 'application/json',
                    'Accept': 'application/json', 'User-Agent': 'ZVGPro-Private-Runtime/1',
                })
                try:
                    with opener.open(req, timeout=90) as response:
                        raw = response.read(512 * 1024 + 1)
                    if len(raw) > 512 * 1024:
                        raise RuntimeFailure('Private Serverantwort überschreitet das Limit.')
                    reply = json.loads(raw)
                    if not isinstance(reply, dict) or reply.get('ok') is not True:
                        raise RuntimeFailure('Private Operation nicht bestätigt.')
                    return reply
                except urllib.error.HTTPError as error:
                    status = error.code
                    error.close()
                    # Never retry ambiguous non-idempotent begin/commit requests.
                    if status < 500 or payload['action'] in ('upload_begin', 'upload_commit', 'activate'):
                        raise RuntimeFailure(f'Privater Speicher HTTP {status}.', status=status) from None
                except (OSError, ValueError, urllib.error.URLError):
                    if payload['action'] in ('upload_begin', 'upload_commit', 'activate'):
                        raise RuntimeFailure('Private Operation ohne bestätigte Antwort.') from None
                if attempt < 2:
                    time.sleep(attempt + 1)
            raise RuntimeFailure('Privater Speicher nicht erreichbar.')
        return post

    def health(self, require_active=True):
        reply = self.post({'action': 'health'})
        if reply.get('protocol') != PROTOCOL or reply.get('storage_ready') is not True:
            raise RuntimeFailure('Privater Speicher ist nicht bereit.')
        if require_active and reply.get('active') is not True:
            raise RuntimeFailure('Private Verarbeitung ist noch nicht aktiviert.')
        return reply

    def snapshot(self):
        info = self.post({'action': 'snapshot_info'})
        if info.get('exists') is not True or not re.fullmatch(r'[0-9a-f]{32}', str(info.get('generation', ''))):
            raise RuntimeFailure('Kein gültiger privater Ausgangsbestand.')
        raw = self._download(dict(action='snapshot_chunk', generation=info['generation']), info, MAX_SNAPSHOT)
        return raw, info

    def _download(self, request, info, limit, target=None):
        total = info.get('bytes')
        if type(total) is not int or not 0 < total <= limit or not HASH.fullmatch(str(info.get('sha256', ''))):
            raise RuntimeFailure('Ungültige private Objektmetadaten.')
        result, sha, offset = bytearray(), hashlib.sha256(), 0
        while offset < total:
            reply = self.post(dict(request, offset=offset))
            try:
                chunk = base64.b64decode(reply.get('data', ''), validate=True)
            except (ValueError, TypeError):
                raise RuntimeFailure('Ungültige private Übertragungsdaten.') from None
            if (reply.get('offset') != offset or reply.get('bytes') != len(chunk)
                    or not 0 < len(chunk) <= CHUNK_BYTES or reply.get('total_bytes') != total
                    or reply.get('sha256') != digest(chunk) or offset + len(chunk) > total
                    or reply.get('eof') is not (offset + len(chunk) == total)
                    or ('generation' in request and reply.get('generation') != request['generation'])):
                raise RuntimeFailure('Private Übertragungsprüfung fehlgeschlagen.')
            sha.update(chunk)
            if target:
                target.write(chunk)
            else:
                result.extend(chunk)
            offset += len(chunk)
        if sha.hexdigest() != info['sha256']:
            raise RuntimeFailure('Privater Gesamthash stimmt nicht überein.')
        return bytes(result)

    def media(self, path, sha256, destination):
        oid, filename = media_key(path)
        if not HASH.fullmatch(str(sha256)):
            raise RuntimeFailure('Medienabruf ohne unveränderlichen Hash verweigert.')
        info = self.post(dict(action='media_info', object_id=oid, filename=filename, sha256=sha256))
        if info.get('sha256') != sha256:
            raise RuntimeFailure('Private Medienversion wurde nicht bestätigt.')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + '.part')
        try:
            with temporary.open('wb') as stream:
                self._download(dict(action='media_chunk', object_id=oid, filename=filename, sha256=sha256), info, MAX_MEDIA, stream)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def upload(self, path, kind, expected_generation=None, key=None):
        path = Path(path)
        sha, size = file_digest(path), path.stat().st_size
        if size < 1 or size > (MAX_SNAPSHOT if kind == 'snapshot' else MAX_MEDIA):
            raise RuntimeFailure('Privates Objekt überschreitet das Limit.')
        payload = dict(action='upload_begin', kind=kind, bytes=size, sha256=sha)
        if kind == 'snapshot':
            payload['expected_generation'] = expected_generation
        else:
            payload['object_id'], payload['filename'] = media_key(key)
        begin = self.post(payload)
        upload_id = begin.get('upload_id')
        if not isinstance(upload_id, str) or not re.fullmatch(r'[0-9a-f]{32}', upload_id) or begin.get('chunk_bytes') != CHUNK_BYTES:
            raise RuntimeFailure('Privater Upload wurde nicht gültig begonnen.')
        offset = 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
                reply = self.post(dict(action='upload_chunk', upload_id=upload_id, offset=offset,
                                       data=base64.b64encode(chunk).decode(), sha256=digest(chunk)))
                if reply.get('received_bytes') != offset + len(chunk):
                    raise RuntimeFailure('Private Uploadposition nicht bestätigt.')
                offset += len(chunk)
        try:
            final = self.post(dict(action='upload_commit', upload_id=upload_id))
        except RuntimeFailure:
            # Lost response: verify receipt read-only; never re-begin the write.
            if kind == 'snapshot':
                final = self.post({'action': 'snapshot_info'})
                if final.get('generation') == expected_generation:
                    raise RuntimeFailure('Privater Snapshot-Abschluss nicht bestätigt.') from None
            else:
                final = self.post(dict(action='media_info', object_id=payload['object_id'], filename=payload['filename'], sha256=sha))
        if final.get('sha256') != sha or final.get('bytes') != size:
            raise RuntimeFailure('Privater Upload nicht vollständig bestätigt.')
        return final

    def ensure_media(self, path, key):
        """Safe retry of a staged migration: skip only a verified exact object."""
        oid, filename = media_key(key)
        sha, size = file_digest(path), Path(path).stat().st_size
        try:
            info = self.post(dict(action='media_info', object_id=oid, filename=filename, sha256=sha))
        except RuntimeFailure as error:
            if error.status != 404:
                raise
        else:
            if info.get('sha256') != sha or info.get('bytes') != size:
                raise RuntimeFailure('Bestehendes privates Medium nicht vollständig bestätigt.')
            return info
        return self.upload(path, 'media', key=key)


def hydrate_attachment(attachment, field='cached'):
    """Fetch only an asset actually needed by enrichment, using its pinned hash."""
    path = attachment.get(field + '_path')
    if not path:
        return None
    target = Path(path)
    if not os.environ.get('ZVGPRO_PRIVATE_RUNTIME'):
        return target if target.is_file() else None
    media_key(path)
    ref = media_reference(attachment.get(field + '_url'))
    if ref is None or ref[0] != path or not ref[1]:
        raise RuntimeFailure('Aktives Medium ohne privaten Hash-Verweis.')
    if target.is_file() and file_digest(target) == ref[1]:
        return target
    Client().media(path, ref[1], target)
    return target
