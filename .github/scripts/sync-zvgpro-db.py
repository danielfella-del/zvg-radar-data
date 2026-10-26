"""Authenticated, lossless JSON sync. No third-party packages required."""
import errno
import hashlib
import http.client
import json
import os
import signal
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

PROTOCOL = 'field-chunks-v1'
MAX_BYTES = 128 * 1024
MAX_VALUES = 900  # Includes envelope fields and fragment paths; margin below 1000.
MAX_PARTS = 128


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def values(value):
    if isinstance(value, dict):
        return sum(values(v) for v in value.values())
    if isinstance(value, list):
        return sum(values(v) for v in value)
    return 1


def fits(payload):
    return values(payload) <= MAX_VALUES and len(encode(payload)) <= MAX_BYTES


def leaves(value, path=()):
    if len(path) > 32:
        raise RuntimeError('Objekt ist zu tief verschachtelt; keine Daten gekürzt.')
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from leaves(child, path + (key,))
    elif isinstance(value, list) and value:
        for key, child in enumerate(value):
            yield from leaves(child, path + (key,))
    else:
        yield [list(path), value]


def object_parts(row, run_id, offset):
    identity = hashlib.sha256(encode(row)).hexdigest()
    base = dict(action='object_part', run_id=run_id, offset=offset,
                object_id=row['id'], transfer_id=identity, part_index=127, parts=128)
    chunks, chunk = [], []
    for entry in leaves(row):
        if not fits(dict(base, entries=chunk + [entry])):
            if not chunk:
                raise RuntimeError(f'Position {offset}: ein einzelner Wert überschreitet das Transportlimit; keine Daten gekürzt.')
            chunks.append(chunk)
            chunk = []
        if not fits(dict(base, entries=[entry])):
            raise RuntimeError(f'Position {offset}: ein einzelner Wert überschreitet das Transportlimit; keine Daten gekürzt.')
        chunk.append(entry)
    if chunk:
        chunks.append(chunk)
    if len(chunks) > MAX_PARTS:
        raise RuntimeError(f'Position {offset}: zu viele Objektteile; Import nicht gestartet.')
    return [dict(base, entries=entries, part_index=i, parts=len(chunks))
            for i, entries in enumerate(chunks)]


def plan(rows, run_id, start=0):
    """Plan from acknowledged absolute offset, preserving every field and order."""
    packets = []
    offset = start
    while offset < len(rows):
        base = dict(action='batch', run_id=run_id, offset=offset)
        batch = []
        for row in rows[offset:offset + 75]:
            if not fits(dict(base, objects=batch + [row])):
                break
            batch.append(row)
        if batch:
            packets.append(dict(base, objects=batch))
            offset += len(batch)
        else:
            parts = object_parts(rows[offset], run_id, offset)
            packets.extend(parts)
            # These exact byte hashes are checked by PHP before assembly/import.
            packets.append(dict(action='object_commit', run_id=run_id, offset=offset,
                                transfer_id=parts[0]['transfer_id'],
                                part_hashes=[hashlib.sha256(encode(p)).hexdigest() for p in parts]))
            offset += 1
    if not all(fits(p) for p in packets):
        raise RuntimeError('Interner Paketfehler; Import nicht gestartet.')
    return packets


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward either authentication header to a redirect.


def failure_category(error):
    """Classify only exception types/numeric codes, never remote text or URLs."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        category, field, code = 'tls_certificate', 'verify_code', getattr(reason, 'verify_code', None)
    elif isinstance(reason, ssl.SSLError):
        category, field, code = 'tls_error', 'errno', reason.errno
    elif isinstance(reason, socket.gaierror):
        category, field, code = 'name_resolution', 'errno', reason.errno
    elif isinstance(reason, TimeoutError):
        category, field, code = 'timeout', 'errno', reason.errno
    elif isinstance(reason, http.client.IncompleteRead):
        category, field, code = 'incomplete_response', '', None
    elif isinstance(reason, http.client.HTTPException):
        category, field, code = 'http_transport', '', None
    elif isinstance(reason, OSError):
        category = {errno.ECONNRESET: 'connection_reset', errno.ECONNREFUSED: 'connection_refused',
                    errno.ETIMEDOUT: 'timeout'}.get(reason.errno, 'os_error')
        field, code = 'errno', reason.errno
    elif isinstance(reason, ValueError):
        category, field, code = 'invalid_response', '', None
    else:
        category, field, code = 'url_error', '', None
    return category + (f' {field}={code}' if type(code) is int else '')


def response_int(reply, field, default=-1):
    try:
        return int(reply.get(field, default))
    except (TypeError, ValueError, OverflowError):
        # int() exceptions can echo a server-supplied string, including secrets.
        raise RuntimeError(f'Ungültiger numerischer Serverwert ({field}); kein Abschluss.') from None


def make_post(token):
    opener = urllib.request.build_opener(NoRedirect())

    def post(payload, attempts=3):
        body = encode(payload)
        if not fits(payload):
            raise RuntimeError('Paket überschreitet das Transportlimit.')
        action = payload.get('action')
        if action not in ('ping', 'begin', 'batch', 'object_part', 'object_commit', 'finalize', 'status', 'abort'):
            action = 'unknown'
        offset = payload.get('offset') if type(payload.get('offset')) is int else '-'
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request('https://zvgpro.de/api/github-sync.php', data=body, method='POST', headers={
                'Authorization': 'Bearer ' + token, 'X-ZVGPro-Sync-Token': token,
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'User-Agent': 'ZVGPro-GitHub-Sync/field-chunks-v1',
            })
            try:
                with opener.open(req, timeout=60) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                if not isinstance(result, dict) or not result.get('ok'):
                    raise RuntimeError('Server hat das Paket nicht bestätigt.')
                return result
            except urllib.error.HTTPError as e:
                # Do not read error bodies: even JSON "error" can contain secrets,
                # object data, forged log commands, or raise IncompleteRead itself.
                try:
                    e.close()
                except (OSError, http.client.IncompleteRead):
                    pass
                category = f'http_error HTTP={e.code}'
                retry = not (e.code < 500 and e.code not in (408, 429))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.IncompleteRead) as e:
                category, retry = failure_category(e), True
            error = RuntimeError(f'Sync-Anfrage fehlgeschlagen: Aktion {action}, Offset {offset}, '
                                 f'Versuch {attempt}/{attempts}, Kategorie {category}, '
                                 f'{len(body)} Bytes/{values(payload)} Werte.')
            print(str(error), file=sys.stderr, flush=True)
            if not retry:
                raise error from None
            if attempt < attempts:
                time.sleep(attempt * 3)
        raise error from None
    return post


def run(raw, post):
    data = json.loads(raw)
    rows = data.get('results', data) if isinstance(data, dict) else data
    if not isinstance(rows, list) or len(rows) < 500:
        raise RuntimeError('Ungültiger Datenbestand: weniger als 500 Verfahren.')
    ids = [row.get('id') if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(i, str) or not i or len(i.encode('utf-8')) > 160 for i in ids) or len(set(ids)) != len(ids):
        raise RuntimeError('Fehlende oder doppelte Objekt-IDs; Import nicht gestartet.')
    # Validate the entire snapshot before opening a server-side run.
    checked = plan(rows, 9999999999999999999)
    print(f'Vorprüfung: {len(rows)} Verfahren, {len(checked)} Pakete, maximal {MAX_VALUES} Werte/{MAX_BYTES} Bytes.', flush=True)
    ping = post({'action': 'ping'})
    if ping.get('sync_protocol') != PROTOCOL:
        raise RuntimeError('Strato-PHP-Patch field-chunks-v1 fehlt. Zuerst alle drei API-Dateien hochladen; Import nicht gestartet.')
    dataset_hash = hashlib.sha256(raw).hexdigest()
    generated_at = data.get('meta', {}).get('generated_at') if isinstance(data, dict) else None
    begin = post(dict(action='begin', total=len(rows), dataset_hash=dataset_hash, generated_at=generated_at))
    if begin.get('not_modified'):
        if response_int(begin, 'count') != len(rows):
            raise RuntimeError('Datensatz-Hash stimmt, aber die Objektanzahl weicht ab.')
        print(f'Bereits aktuell: {len(rows)} Verfahren.')
        return begin
    run_id = response_int(begin, 'run_id')
    if run_id < 1:
        raise RuntimeError('Ungültige Lauf-ID; kein Abschluss oder Abbruch eines unbekannten Laufs.')
    try:
        processed = response_int(begin, 'processed', 0)
        if response_int(begin, 'total') != len(rows) or not 0 <= processed <= len(rows):
            raise RuntimeError('Ungültiger Fortsetzungsstatus; kein Abschluss.')
        packets = plan(rows, run_id, processed)
        print(f'Sync {run_id}: Fortsetzung bei {processed}/{len(rows)}, {len(packets)} Pakete.', flush=True)
        for n, packet in enumerate(packets, 1):
            reply = post(packet)
            action = packet['action']
            if action == 'object_part':
                expected_hash = hashlib.sha256(encode(packet)).hexdigest()
                if reply.get('received_sha256') != expected_hash or response_int(reply, 'part_index') != packet['part_index']:
                    raise RuntimeError('Objektteil nicht unverändert bestätigt; kein Abschluss.')
            else:
                processed = packet['offset'] + (len(packet['objects']) if action == 'batch' else 1)
            confirmed = response_int(reply, 'processed')
            if confirmed != processed:
                raise RuntimeError(f'Server bestätigt {confirmed} statt {processed} Verfahren; kein Abschluss.')
            if n == 1 or n % 25 == 0 or n == len(packets):
                print(f'Paket {n}/{len(packets)}: {processed}/{len(rows)} verarbeitet.', flush=True)
        recovered = False
        try:
            final = post(dict(action='finalize', run_id=run_id))
        except Exception:
            # Read-only receipt, never begin a second import for recovery.
            status = post(dict(action='status', run_id=run_id, dataset_hash=dataset_hash))
            if status.get('completed') is not True:
                raise
            final = status.get('result')
            recovered = True
        if (not isinstance(final, dict) or not final.get('ok') or
                response_int(final, 'run_id') != run_id or response_int(final, 'count') != len(rows)):
            raise RuntimeError('Abschluss wurde nicht mit vollständiger Objektanzahl bestätigt.')
        if recovered:
            print('Abschlussantwort verloren; abgeschlossener Import über Beleg bestätigt.')
        print('DB-Sync abgeschlossen:', json.dumps({k: final.get(k) if type(final.get(k)) is int else None
              for k in ('count', 'inserted', 'updated', 'unchanged', 'deactivated')}, ensure_ascii=False))
        return final
    except BaseException:
        # Only this run + this snapshot may be released. Never deactivate data here.
        try:
            abort = post(dict(action='abort', run_id=run_id, dataset_hash=dataset_hash))
            if abort.get('ok') and abort.get('aborted') is True and abort.get('run_id') == run_id:
                print(f'Lauf {run_id}: Abbruch bestätigt. Bereits vorhandene Objekte bleiben erhalten.', flush=True)
            elif abort.get('ok') and abort.get('already_completed') is True:
                print(f'Lauf {run_id}: laut Server bereits abgeschlossen; kein Abbruch ausgeführt.', flush=True)
            elif abort.get('ok') and abort.get('not_active') is True:
                print(f'Lauf {run_id}: laut Server nicht mehr aktiv; keine fremde Sperre aufgehoben.', flush=True)
            elif abort.get('ok') and abort.get('not_running') is True:
                print(f'Lauf {run_id}: laut Server nicht mehr laufend; kein Abbruch ausgeführt.', flush=True)
            else:
                raise RuntimeError('Abbruchstatus nicht bestätigt.')
        except Exception:
            print(f'::warning::Lauf {run_id}: Abbruch konnte nicht bestätigt werden; keine fremde Sperre aufgehoben.', flush=True)
        raise


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise InterruptedError('Sync wurde unterbrochen.')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        token = os.environ.get('ZVGPRO_DB_SYNC_TOKEN', '').strip()
        if not token:
            raise RuntimeError('GitHub Secret ZVGPRO_DB_SYNC_TOKEN fehlt.')
        with open('auctions.json', 'rb') as source:
            run(source.read(), make_post(token))
    except Exception as e:
        print(f'::error::{e}', file=sys.stderr)
        raise SystemExit(1)
