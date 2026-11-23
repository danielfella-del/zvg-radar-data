#!/usr/bin/env python3
"""Explicit, bounded document catch-up; delegates parsing to the existing collector."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'collector'))
from enrich_details import portal_identity_valid  # noqa: E402
from publication_policy import sanitize_payload, withdrawn_id  # noqa: E402


def target_ids(value: str) -> list[str]:
    ids = list(dict.fromkeys(re.split(r'[\s,]+', value.strip())))
    if not 1 <= len(ids) <= 1000 or any(not re.fullmatch(r'[a-z]{2}-[0-9]+', oid) for oid in ids):
        raise ValueError('1 bis 1000 eindeutige Portal-Objekt-IDs erforderlich')
    return ids


def report_ids(record: dict) -> set[str]:
    return {str(a.get('file_id')) for a in record.get('attachments', [])
            if isinstance(a, dict) and a.get('type') == 'gutachten' and a.get('file_id')}


def run(data: Path, output: Path, ids: list[str], runner=subprocess.run) -> dict:
    if any(withdrawn_id(oid) for oid in ids):
        raise ValueError('Zurückgezogenes Verfahren darf nicht nachgepflegt werden')
    before = sanitize_payload(json.loads(data.read_text(encoding='utf-8')))
    records = before['results']
    index = {r['id']: r for r in records}
    if len(index) != len(records):
        raise ValueError('Doppelte Objekt-IDs im Ausgangsbestand')
    if any(oid not in index or not portal_identity_valid(index[oid]) for oid in ids):
        raise ValueError('Zielobjekt fehlt oder hat keine gültige Portalzuordnung')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'plan.json').write_text(json.dumps({'ids': ids, 'count': len(ids)}, indent=2), encoding='utf-8')
    command = [sys.executable, str(ROOT / 'collector/enrich_details.py'), '--data', str(data), '--limit', str(len(ids))]
    for oid in ids:
        command.extend(['--object-id', oid])
    with (output / 'collector.log').open('w', encoding='utf-8') as log:
        runner(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    after = json.loads(data.read_text(encoding='utf-8'))
    current = {r['id']: r for r in after['results']}
    if len(current) != len(after['results']) or set(current) != set(index):
        raise ValueError('Objektbestand wurde unerwartet verändert; kein Upload')
    # The existing collector also recalculates derived completeness for old
    # records. Restore unrelated records exactly: this job owns only its IDs.
    wanted = set(ids)
    after['results'] = [current[r['id']] if r['id'] in wanted else r for r in records]
    results = []
    for oid in ids:
        old, new = index[oid], current[oid]
        attempted = new.get('detail_attempted_at') != old.get('detail_attempted_at') and bool(new.get('detail_attempted_at'))
        success = attempted and not new.get('detail_error') and bool(new.get('detail_fetched_at'))
        if not attempted:
            raise ValueError('Zielobjekt ohne Prüfversuch; kein Upload')
        results.append({'id': oid, 'status': 'checked' if success else 'error',
                        'reports_before': sorted(report_ids(old)), 'reports_after': sorted(report_ids(new)),
                        'new_report_ids': sorted(report_ids(new) - report_ids(old))})
    summary = {'requested': len(ids), 'checked': sum(r['status'] == 'checked' for r in results),
               'errors': sum(r['status'] == 'error' for r in results),
               'objects_with_new_reports': sum(bool(r['new_report_ids']) for r in results),
               'new_report_links': sum(len(r['new_report_ids']) for r in results), 'results': results}
    (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    temp = data.with_suffix('.backfill.tmp')
    temp.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(data)
    return summary


if __name__ == '__main__':
    summary = run(ROOT / 'data/auctions.json', ROOT / 'diagnostics/document-backfill',
                  target_ids(os.environ.get('ZVG_BACKFILL_IDS', '')))
    totals = {key: value for key, value in summary.items() if key != 'results'}
    print(json.dumps(totals))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as note:
            note.write('## Dokumentnachpflege\n\n```json\n' + json.dumps(totals, indent=2) + '\n```\n')
            note.write('\nGeprüfte Metadaten sind kein Nachweis für den Download aller PDF-Inhalte.\n')
    if summary['errors']:
        print(f"::warning::{summary['errors']} Quellenprüfungen fehlgeschlagen; siehe Ergebnisprotokoll.")
