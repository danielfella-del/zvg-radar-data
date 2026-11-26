#!/usr/bin/env python3
"""Publication withdrawals, applied before processing and before every export.

Only hashes of normalized technical IDs belong here. This module does not erase
Git history, remote caches or backups. Those require a separate verified purge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from functools import lru_cache
from urllib.parse import unquote, urlparse, parse_qs

WITHDRAWN_ID_HASHES = frozenset({
    "547eb4d9fcc509af49e742dbcefb0a51590f2609c08fc82f78c47582f205f097",
})


@lru_cache(maxsize=4)
def _read_private_hashes(path):
    values = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(values, list) or any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for v in values):
        raise ValueError('Private Veröffentlichungsrichtlinie ungültig')
    return frozenset(values)


def private_withdrawal_hashes():
    """The authenticated runtime runner supplies a private policy file per run."""
    path = os.environ.get('ZVGPRO_WITHDRAWAL_HASHES_FILE')
    if not path:
        if os.environ.get('ZVGPRO_PRIVATE_RUNTIME'):
            raise ValueError('Private Veröffentlichungsrichtlinie fehlt')
        return frozenset()
    return _read_private_hashes(path)
ID_TOKEN = re.compile(r"(?<![a-z0-9_-])[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?![a-z0-9_-])", re.I)
_DROP = object()


def withdrawn_id(value) -> bool:
    normalized = str(value or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() in (WITHDRAWN_ID_HASHES | private_withdrawal_hashes())


def withdrawn_record(record) -> bool:
    if not isinstance(record, dict):
        return False
    if any(withdrawn_id(record.get(key)) for key in ("id", "object_id", "auction_id")):
        return True
    return withdrawn_id(f"{record.get('state_code', '')}-{record.get('zvg_id', '')}")


def withdrawn_reference(text: str) -> bool:
    decoded = unquote(unquote(text))
    if any(withdrawn_id(match.group()) for match in ID_TOKEN.finditer(decoded)):
        return True
    # Canonical portal URLs may identify the same procedure without a combined ID.
    if "zvg_id=" in decoded.lower():
        query = parse_qs(urlparse(decoded).query)
        for land in query.get("land_abk", []):
            for number in query.get("zvg_id", []):
                if withdrawn_id(f"{land}-{number}"):
                    return True
    return False


def _scrub(value):
    if isinstance(value, dict):
        if withdrawn_record(value):
            return _DROP
        result = {}
        for key, item in value.items():
            if withdrawn_reference(str(key)):
                continue
            cleaned = _scrub(item)
            if cleaned is not _DROP:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _scrub(item)) is not _DROP]
    if isinstance(value, str) and withdrawn_reference(value):
        return _DROP
    return value


def sanitize_payload(payload):
    """Remove withdrawn records, nested history samples and direct references."""
    cleaned = _scrub(payload)
    if cleaned is _DROP:
        cleaned = {} if isinstance(payload, dict) else None
    if isinstance(cleaned, dict) and isinstance(cleaned.get("results"), list):
        meta = cleaned.get("meta")
        if isinstance(meta, dict) and "count" in meta and len(cleaned["results"]) != len(payload["results"]):
            meta["count"] = len(cleaned["results"])
    return cleaned


def enforce_file(path: Path, check: bool = False) -> bool:
    if path.is_symlink():
        raise ValueError("Verknüpfte Datendatei wird nicht verarbeitet")
    original = json.loads(path.read_text(encoding="utf-8"))
    cleaned = sanitize_payload(original)
    changed = cleaned != original
    if changed and not check:
        temporary = path.with_name(path.name + ".publication.tmp")
        if temporary.is_symlink():
            raise ValueError("Unsicherer temporärer Dateipfad")
        temporary.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    return changed


def enforce_media(root: Path, check: bool = False) -> int:
    if root.is_symlink():
        raise ValueError("Verknüpftes Medienverzeichnis wird nicht verarbeitet")
    if not root.exists():
        return 0
    blocked = [child for child in root.iterdir() if withdrawn_id(child.name)]
    for child in blocked:
        if check:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
    return len(blocked)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/auctions.json")
    parser.add_argument("--media-dir")
    parser.add_argument("--check", action="store_true", help="Nur prüfen; bei Fund mit Fehler abbrechen")
    args = parser.parse_args()
    changed = enforce_file(Path(args.data), args.check)
    folders = enforce_media(Path(args.media_dir), args.check) if args.media_dir else 0
    print(json.dumps({"publication_policy": "blocked" if args.check and (changed or folders) else "ok",
                      "data_changed": changed, "withdrawn_media_folders": folders}))
    return 1 if args.check and (changed or folders) else 0


if __name__ == "__main__":
    raise SystemExit(main())
