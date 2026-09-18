#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.zvg-portal.de/"
SEARCH_REFERER = urljoin(BASE, "index.php?button=Suchen")

DETAIL_FIELDS = [
    "auction_type",
    "land_registry",
    "auction_venue",
    "creditor_info",
    "detail_description",
    "court_url",
    "geoserver_urls",
    "portal_google_maps_urls",
    "detail_attachments",
    "detail_source_url",
    "detail_fetched_at",
    "detail_source_last_updated",
    "detail_error",
]

def repair_mojibake(text: str) -> str:
    if not text:
        return text
    # Repair common UTF-8 bytes decoded as cp1252/latin1 without damaging
    # strings that are already valid German text.
    if "Ã" not in text and "Â" not in text and "â" not in text:
        return text
    for enc in ("cp1252", "latin1"):
        try:
            fixed = text.encode(enc).decode("utf-8")
            if fixed != text:
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text

def clean(text: str) -> str:
    text = repair_mojibake(text or "")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()

def decode_document(content: bytes) -> str:
    # The portal historically declares Latin-1/Windows-1252 and sometimes
    # contains UTF-8 byte sequences. Parsing as cp1252 then repairing fields
    # is the least destructive strategy for the mixed corpus.
    return content.decode("cp1252", errors="replace")

def canonical_detail_url(land: str, zvg_id: str) -> str:
    return f"{BASE}index.php?button=showZvg&zvg_id={zvg_id}&land_abk={land}"

def label_key(label: str) -> str:
    return clean(label).rstrip(":").casefold()

def row_map(soup: BeautifulSoup) -> list[tuple[str, Any]]:
    scope = soup.find("table", id="anzeige") or soup
    rows = []
    for tr in scope.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = clean(cells[0].get_text(" ", strip=True)).rstrip(":")
        if label:
            rows.append((label, cells[1]))
    return rows

def classify_attachment(label: str, name: str) -> str:
    s = (label + " " + name).casefold()
    if "gutachten" in s:
        return "gutachten"
    if "expos" in s:
        return "expose"
    if "foto" in s or "bild" in s:
        return "foto"
    if "bekanntmach" in s:
        return "bekanntmachung"
    if "hinweis" in s:
        return "hinweis"
    return "dokument"

def attachment_from_anchor(label: str, anchor, land: str, zvg_id: str, row_text: str) -> dict[str, Any] | None:
    href = (anchor.get("href") or "").replace("&amp;", "&")
    if "showAnhang" not in href:
        return None
    qs = parse_qs(urlparse(href).query)
    file_id = (qs.get("file_id") or [""])[0]
    name = clean(anchor.get_text(" ", strip=True)) or clean(label)
    if not file_id:
        return None
    size_match = re.search(r"([\d.,]+)\s*kB\b", row_text, re.I)
    size_kb = None
    if size_match:
        try:
            size_kb = float(size_match.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            pass
    url = f"{BASE}index.php?button=showAnhang&land_abk={land}&file_id={file_id}&zvg_id={zvg_id}"
    return {
        "type": classify_attachment(label, name),
        "file_id": file_id,
        "name": name or None,
        "size_kb": size_kb,
        "url": url,
        "requires_portal_context": True,
    }

def parse_detail_html(content: bytes, land: str, zvg_id: str) -> dict[str, Any]:
    html = decode_document(content)
    if len(html.strip()) < 30 and "error" in html.lower():
        raise RuntimeError("ZVG-Portal verlangt einen gültigen Referer")
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, Any] = {
        "auction_type": None,
        "land_registry": None,
        "auction_venue": None,
        "creditor_info": None,
        "detail_description": None,
        "court_url": None,
        "geoserver_urls": [],
        "portal_google_maps_urls": [],
        "detail_attachments": [],
    }

    rows = row_map(soup)
    for label, cell in rows:
        key = label_key(label)
        value = clean(cell.get_text(" ", strip=True))
        anchors = cell.find_all("a", href=True)

        if key == "art der versteigerung":
            result["auction_type"] = value or None
        elif key == "grundbuch":
            result["land_registry"] = value or None
        elif key == "ort der versteigerung":
            result["auction_venue"] = value or None
        elif key == "informationen zum gläubiger":
            result["creditor_info"] = value or None
        elif key == "beschreibung":
            result["detail_description"] = value or None
        elif key == "gericht":
            for a in anchors:
                href = (a.get("href") or "").strip()
                if href and not href.lower().startswith("javascript:"):
                    result["court_url"] = urljoin(BASE, href)
                    break
        elif key == "geoserver":
            result["geoserver_urls"] = [
                urljoin(BASE, a.get("href")) for a in anchors
                if a.get("href") and not a.get("href").lower().startswith("javascript:")
            ]
        elif "google" in key and "map" in key:
            result["portal_google_maps_urls"] = [
                urljoin(BASE, a.get("href")) for a in anchors
                if a.get("href") and not a.get("href").lower().startswith("javascript:")
            ]

        row_text = clean(cell.parent.get_text(" ", strip=True) if cell.parent else value)
        for a in anchors:
            att = attachment_from_anchor(label, a, land, zvg_id, row_text)
            if att:
                result["detail_attachments"].append(att)

    # Fallback: some portal variants put attachment anchors outside the detail rows.
    seen = {(a.get("file_id"), a.get("type")) for a in result["detail_attachments"]}
    for a in soup.find_all("a", href=re.compile(r"showAnhang", re.I)):
        row = a.find_parent("tr")
        label = ""
        row_text = clean(row.get_text(" ", strip=True) if row else a.parent.get_text(" ", strip=True))
        if row:
            cells = row.find_all(["td", "th"])
            if cells:
                label = clean(cells[0].get_text(" ", strip=True)).rstrip(":")
        att = attachment_from_anchor(label, a, land, zvg_id, row_text)
        if att and (att.get("file_id"), att.get("type")) not in seen:
            result["detail_attachments"].append(att)
            seen.add((att.get("file_id"), att.get("type")))

    # Deduplicate external URLs while preserving order.
    for key in ("geoserver_urls", "portal_google_maps_urls"):
        vals = []
        for u in result[key]:
            if u and u not in vals:
                vals.append(u)
        result[key] = vals

    return result

def merge_attachments(record: dict[str, Any], detail: dict[str, Any]) -> None:
    merged = []
    seen = set()
    for item in (record.get("attachments") or []) + (detail.get("detail_attachments") or []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("file_id") or ""), str(item.get("type") or ""), str(item.get("name") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    record["attachments"] = merged
    record["has_report"] = any(a.get("type") == "gutachten" for a in merged)
    record["gutachten_url"] = next((a.get("url") for a in merged if a.get("type") == "gutachten"), None)
    record["expose_url"] = next((a.get("url") for a in merged if a.get("type") == "expose"), None)

def detail_completeness(record: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "auction_venue": bool(record.get("auction_venue")),
        "auction_type": bool(record.get("auction_type")),
        "land_registry": bool(record.get("land_registry")),
        "creditor_info": bool(record.get("creditor_info")),
        "court_url": bool(record.get("court_url")),
        "geoserver": bool(record.get("geoserver_urls")),
        "gutachten": any(a.get("type") == "gutachten" for a in record.get("attachments") or []),
        "announcement": any(a.get("type") == "bekanntmachung" for a in record.get("attachments") or []),
        "photos": any(a.get("type") == "foto" for a in record.get("attachments") or []),
    }
    score = round(sum(1 for v in checks.values() if v) / len(checks) * 100)
    return {"score": score, "checks": checks}

def should_fetch(record: dict[str, Any]) -> bool:
    if not record.get("zvg_id") or not record.get("state_code"):
        return False
    if record.get("cancelled"):
        return False
    if not record.get("detail_fetched_at"):
        return True
    return record.get("detail_source_last_updated") != record.get("last_updated")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/auctions.json")
    ap.add_argument("--limit", type=int, default=int(os.getenv("ZVG_DETAIL_LIMIT", "300")))
    ap.add_argument("--pause", type=float, default=float(os.getenv("ZVG_DETAIL_PAUSE", "0.35")))
    args = ap.parse_args()

    path = Path(args.data)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("results", [])
    if not isinstance(records, list):
        raise RuntimeError("results ist keine Liste")

    candidates = [r for r in records if should_fetch(r)]
    candidates.sort(key=lambda r: (str(r.get("auction_date") or "9999"), str(r.get("last_updated") or ""), str(r.get("id") or "")))
    selected = candidates[: max(0, args.limit)]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "ZVGRadarDetailCollector/1.0 (+public court-auction research; incremental scheduled fetch)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        "Referer": SEARCH_REFERER,
    })

    fetched = 0
    errors = 0
    for idx, record in enumerate(selected):
        land = str(record.get("state_code"))
        zvg_id = str(record.get("zvg_id"))
        url = canonical_detail_url(land, zvg_id)
        try:
            resp = session.get(url, headers={"Referer": SEARCH_REFERER}, timeout=45)
            resp.raise_for_status()
            detail = parse_detail_html(resp.content, land, zvg_id)
            for k, v in detail.items():
                record[k] = v
            merge_attachments(record, detail)
            record["detail_source_url"] = url
            record["detail_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record["detail_source_last_updated"] = record.get("last_updated")
            record["detail_error"] = None
            record["dossier_completeness"] = detail_completeness(record)
            fetched += 1
            print(f"{record.get('id')}: Detaildaten OK")
        except Exception as exc:
            record["detail_error"] = str(exc)
            errors += 1
            print(f"{record.get('id')}: FEHLER {exc}", file=sys.stderr)
        if idx + 1 < len(selected):
            time.sleep(max(0.0, args.pause))

    for record in records:
        if record.get("detail_fetched_at"):
            record["dossier_completeness"] = detail_completeness(record)

    enriched_total = sum(1 for r in records if r.get("detail_fetched_at"))
    with_gutachten = sum(1 for r in records if any(a.get("type") == "gutachten" for a in r.get("attachments") or []))
    with_photos = sum(1 for r in records if any(a.get("type") == "foto" for a in r.get("attachments") or []))
    meta = payload.setdefault("meta", {})
    meta["detail_enrichment"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fetched_this_run": fetched,
        "errors_this_run": errors,
        "enriched_total": enriched_total,
        "active_candidates_remaining": max(0, len(candidates) - len(selected)),
        "with_gutachten": with_gutachten,
        "with_photos": with_photos,
        "limit_per_run": args.limit,
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(json.dumps(meta["detail_enrichment"], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
