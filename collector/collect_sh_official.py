#!/usr/bin/env python3
"""SH notices: read columns and bind each notice to its own case/court footer."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import fitz
import requests

from official_notice_common import (
    clean, find_market_values, futureish, infer_plz_city, infer_type,
    normalize_case, parse_german_date, STATE_CENTERS, STATE_NAMES,
)

ROOT = 'https://www.schleswig-holstein.de/DE/justiz/themen/service/justizministerialblatt/Teil_B'
PDF_TMPL = ROOT + '/_documents/Aktuelle_Ausgabe/{ym}.pdf?__blob=publicationFile&v=2'
ADAPTER = 'sh-schlha-teil-b'
PARSER_VERSION = 2
START = re.compile(r'Im Wege der Zwangs(?:versteigerung|vollstreckung)\s+(?:zur Aufhebung der Gemeinschaft\s+)?soll(?:en)?\b', re.I)
FULL_CASE = r'\d{1,4}\s*[A-Za-z]?\s*K\s*\d{1,5}\s*/\s*\d{2,4}'
FOOTER = re.compile(r'\((' + FULL_CASE + r')\)\s*Amtsgericht\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß -]{1,79})\.', re.I)


def case_identity(value):
    match = re.fullmatch(r'(\d{1,4})\s*([A-Za-z]?)\s*K\s*(\d{1,5})\s*/\s*(\d{2}|\d{4})', clean(value or ''), re.I)
    if not match:
        return None
    department, suffix, number, year = match.groups()
    return (int(department), suffix.upper(), int(number), int(year) + (2000 if len(year) == 2 else 0))


def issue_identity(record):
    # An issue URL is evidence. Do not merge procedures across different issues.
    case = case_identity(record.get('file_number'))
    source = record.get('source_url')
    return (source, case) if isinstance(source, str) and source and case else None


def column_text(content):
    """Read left column before right, retaining continuations across pages."""
    parts = []
    with fitz.open(stream=content, filetype='pdf') as document:
        if not len(document):
            raise ValueError('Leere SH-Ausgabe')
        for page in document:
            middle = page.rect.width / 2
            columns = [[], []]
            for block in page.get_text('blocks'):
                x0, y0, x1, y1, value = block[:5]
                if not isinstance(value, str) or not value.strip():
                    continue
                if y0 >= page.rect.height * .93:
                    continue  # printed page number, outside the body columns
                if x0 < middle < x1:
                    if START.search(clean(value)) or FOOTER.search(clean(value)):
                        raise ValueError('SH-Bekanntmachung überschreitet die Spaltengrenze')
                    continue  # full-width publication title / section headings
                columns[0 if x1 <= middle else 1].append((y0, x0, value))
            for column in columns:
                parts.extend(item[2] for item in sorted(column))
    text = '\n'.join(parts)
    text = re.sub(r'\u00ad\s*\n\s*', '', text).replace('\u00ad', '')
    return clean(text)


def parse_notices(text, source_url, *, keep_past=False):
    """Fail an entire issue if any auction start/footer cannot be paired safely."""
    text = clean(text)
    starts = list(START.finditer(text))
    footers = list(FOOTER.finditer(text))
    if not starts or not footers or len(starts) != len(footers):
        raise ValueError('SH-Ausgabe ohne vollständig abgegrenzte Bekanntmachungen')
    records = []
    previous_end = 0
    for start, footer in zip(starts, footers):
        if start.start() < previous_end or footer.start() <= start.end():
            raise ValueError('Mehrdeutige SH-Bekanntmachungsgrenze')
        chunk = text[start.start():footer.end()]
        intro_end = re.search(r'\bversteigert\s+werden\b', chunk, re.I)
        if not intro_end or intro_end.start() > 1000:
            raise ValueError('SH-Termineinleitung fehlt')
        intro = chunk[:intro_end.end()]
        iso = parse_german_date(intro)
        if not iso:
            raise ValueError('SH-Versteigerungstermin fehlt')
        datetime.fromisoformat(iso)  # reject impossible dates before touching old data
        case = normalize_case(footer.group(1))
        if not case_identity(case):
            raise ValueError('Unvollständiges SH-Aktenzeichen')
        court = clean(footer.group(2))
        body = chunk[intro_end.end():chunk.rfind('(')]
        values = find_market_values(body)
        postcode, city = infer_plz_city(body)
        digest = hashlib.sha256(chunk.encode()).hexdigest()
        identity = '|'.join((source_url, repr(case_identity(case)), court, iso, digest))
        lat, lng = STATE_CENTERS['sh']
        record = {
            'id': 'sh-official-' + hashlib.sha1(identity.encode()).hexdigest()[:16],
            'source_adapter': ADAPTER, 'source_url': source_url,
            'source_publication': 'Schleswig-Holsteinische Anzeigen Teil B',
            'source_notice_hash': digest, 'source_data_status': 'verified_notice',
            'state_code': 'sh', 'state': STATE_NAMES['sh'],
            'court': court, 'file_number': case, 'auction_date': iso,
            'cancelled': False, 'postcode': postcode, 'city': city,
            'property_type': infer_type(body), 'property_type_raw': infer_type(body),
            'market_value': max(values) if values else None, 'market_values': values,
            'description': None, 'attachments': [], 'has_report': False,
            'lat': lat, 'lng': lng, 'position_precision': 'state_approximation',
        }
        if keep_past or futureish(iso):
            records.append(record)
        previous_end = footer.end()
    # A repeated identical notice cannot produce two distinguishable object IDs.
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Doppelte SH-Bekanntmachung in derselben Ausgabe')
    return records


def parse_issue(content, source_url, *, keep_past=False):
    return parse_notices(column_text(content), source_url, keep_past=keep_past)


def quarantine(old, reason):
    public_fields = ('court', 'market_value', 'market_values', 'auction_date', 'address',
                     'postcode', 'city', 'district', 'property_type', 'property_type_raw')
    previous = old.get('source_review_previous') or old
    clear = {key: None for key in (
        'court', 'market_value', 'auction_date', 'address', 'postcode', 'city', 'district',
        'property_type', 'property_type_raw', 'description', 'gutachten_url', 'expose_url',
        'detail_description', 'creditor_info', 'smart_analysis', 'dossier_completeness', 'lat', 'lng')}
    clear.update({'market_values': [], 'attachments': [], 'detail_attachments': [],
                  'has_report': False, 'position_precision': 'unknown',
                  'source_report_available': None, 'external_report_url': None,
                  'external_report_access': 'unknown', 'source_document_check_status': 'unknown',
                  'source_document_checked_at': None})
    return {**old, **clear, 'source_data_status': 'unverified', 'source_data_error': reason,
            'source_review_previous': {key: previous.get(key) for key in public_fields}}


def preserve_record_ids(records, previous, checked_sources=None):
    checked = set(checked_sources or [])
    prior = [r for r in previous if r.get('state_code') == 'sh' and r.get('source_adapter') == ADAPTER]
    old_keys, new_keys = defaultdict(list), defaultdict(list)
    for record in prior:
        if issue_identity(record):
            old_keys[issue_identity(record)].append(record)
    for record in records:
        if record.get('state_code') != 'sh' or record.get('source_adapter') != ADAPTER:
            raise ValueError('Fremder Datensatz im SH-Abgleich')
        if record.get('source_url') not in checked or not issue_identity(record):
            raise ValueError('SH-Abgleich benötigt eine vollständig geprüfte Ausgabe')
        new_keys[issue_identity(record)].append(record)
    output, diagnostics, reused = [], [], set()
    for record in records:
        key = issue_identity(record)
        matches = old_keys.get(key, [])
        exact = [old for old in matches if old['id'] == record['id']
                 and old.get('source_data_status') == 'verified_notice'
                 and old.get('source_notice_hash') == record.get('source_notice_hash')]
        old = matches[0] if len(matches) == 1 and len(new_keys[key]) == 1 else None
        # Already isolated source notices retain their deterministic IDs on
        # another run, even beside legacy quarantines of the same case.
        if old is None and len(exact) == 1:
            old = exact[0]
        if old:
            merged = {**old, **record, 'id': old['id']}
            if old.get('attachments'):
                merged['attachments'] = old['attachments']
                merged['has_report'] = bool(old.get('has_report'))
            for obsolete in ('source_data_error', 'source_review_previous'):
                merged.pop(obsolete, None)
            output.append(merged)
            reused.add(old['id'])
        elif futureish(record.get('auction_date')):
            output.append(dict(record))
    for old in prior:
        if old['id'] in reused:
            continue
        if old.get('source_url') not in checked:
            output.append(dict(old))
            continue
        key = issue_identity(old)
        reason = 'legacy_identity_ambiguous' if new_keys.get(key) else 'legacy_identity_not_confirmed'
        # On repeated runs, the same unverified legacy IDs remain distinct from
        # the source-bound new notices. Do not overwrite either by generic case.
        output.append(quarantine(old, reason))
        diagnostics.append({'id': old['id'], 'reason': reason})
    ids = [r['id'] for r in output]
    if len(ids) != len(set(ids)):
        raise ValueError('Doppelte SH-Objekt-ID nach Identitätsabgleich')
    return output, diagnostics


def write_payload(path, records, meta, announce=True):
    ids = [r['id'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Doppelte SH-Objekt-ID')
    rows = sorted(records, key=lambda r: (r.get('auction_date') or '9999', r.get('court') or '', r['id']))
    payload = {'meta': {'source': ROOT, 'state_code': 'sh', 'source_parser_version': PARSER_VERSION,
                        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        'count': len(rows), **meta}, 'results': rows}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    if announce:
        print(json.dumps(payload['meta'], ensure_ascii=False))


def month_candidates(n=8):
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    result = []
    for _ in range(n):
        result.append(f'{year:04d}{month:02d}')
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/supplemental_sh.json')
    parser.add_argument('--max-issues', type=int, default=8)
    parser.add_argument('--previous', help='Snapshot before collect.py, to preserve unread sources and opaque IDs')
    args = parser.parse_args()
    previous_path = Path(args.previous) if args.previous else Path(args.output)
    previous = json.loads(previous_path.read_text(encoding='utf-8')).get('results', []) if previous_path.exists() else []
    if args.previous and not previous_path.exists():
        raise ValueError('Angegebener SH-Vorbestand fehlt')
    # The workflow has a wall-clock timeout. An interruption before final
    # reconciliation must leave a valid merge input containing the old data.
    prior_sh = [r for r in previous if r.get('state_code') == 'sh' and r.get('source_adapter') == ADAPTER]
    write_payload(args.output, prior_sh, {
        'incomplete': True, 'issues_scanned': 0, 'checked_sources': [], 'errors': [], 'identity_review': [],
    }, announce=False)
    session = requests.Session()
    session.headers.update({'User-Agent': 'ZVGProOfficialSourceCollector/1.3 (+public court notices; low-rate fetch)'})
    records, errors, checked = [], [], []
    scanned = 0
    for month in month_candidates(args.max_issues):
        url = PDF_TMPL.format(ym=month)
        try:
            response = session.get(url, timeout=(8, 25))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            if response.content[:4] != b'%PDF':
                raise ValueError('SH-Quelle lieferte keine PDF-Ausgabe')
            scanned += 1
            # Include past notices for identity confirmation, but only new
            # future notices enter the output in preserve_record_ids().
            parsed = parse_issue(response.content, url, keep_past=True)
            records.extend(parsed)
            checked.append(url)
        except Exception as error:
            if len(errors) < 12:
                errors.append({'url': url, 'error': str(error)})
    records, diagnostics = preserve_record_ids(records, previous, checked)
    write_payload(args.output, records, {
        'incomplete': False, 'issues_scanned': scanned, 'checked_sources': checked, 'errors': errors,
        'identity_review': diagnostics,
        'note': 'Nur minimale Falldaten aus Teil B; Veröffentlichungstexte werden nicht übernommen.',
    })


if __name__ == '__main__':
    main()
