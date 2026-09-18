#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
SEARCH_URL = urljoin(BASE, "index.php?button=Suchen&all=1")

STATES = {
    "bw": "Baden-Württemberg",
    "by": "Bayern",
    "be": "Berlin",
    "br": "Brandenburg",
    "hb": "Bremen",
    "he": "Hessen",
    "ni": "Niedersachsen",
    "nw": "Nordrhein-Westfalen",
    "rp": "Rheinland-Pfalz",
    "sl": "Saarland",
    "sn": "Sachsen",
    "st": "Sachsen-Anhalt",
    "sh": "Schleswig-Holstein",
    "th": "Thüringen",
}

STATE_CENTERS = {
    "bw": (48.65, 9.35), "by": (48.95, 11.35), "be": (52.52, 13.405),
    "br": (52.4, 13.05), "hb": (53.08, 8.8), "he": (50.65, 9.0),
    "ni": (52.75, 9.4), "nw": (51.45, 7.55), "rp": (49.9, 7.45),
    "sl": (49.38, 6.95), "sn": (51.05, 13.3), "st": (51.95, 11.7),
    "sh": (54.2, 9.85), "th": (50.9, 11.0),
}

DATE_RE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})(?:\s+(\d{1,2}:\d{2}))?\b")
PLZ_RE = re.compile(r"\b(\d{5})\b")
VALUE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d{4,})(?:\s*)(?:€|EUR)\b", re.I)
FILE_RE = re.compile(r"\b(?:\d{1,4}\s*)?K\s*\d{1,5}\s*/\s*\d{2,4}\b", re.I)
COURT_RE = re.compile(r"\bAmtsgericht\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\- .()]+?)(?=\s{2,}|\s\d{1,2}\.\d{1,2}\.\d{4}|\s\d{5}\b|$)")

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def stable_jitter(key: str) -> tuple[float, float]:
    d = hashlib.sha256(key.encode("utf-8", "ignore")).digest()
    return ((d[0] / 255 - 0.5) * 0.7, (d[1] / 255 - 0.5) * 1.0)

def parse_money(text: str) -> int | None:
    vals = []
    for m in VALUE_RE.finditer(text):
        raw = m.group(1).replace(".", "").replace(",", ".")
        try:
            vals.append(int(round(float(raw))))
        except ValueError:
            pass
    return max(vals) if vals else None

def parse_date(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day = m.group(1)
    clock = m.group(2) or "00:00"
    try:
        dt = datetime.strptime(day + " " + clock, "%d.%m.%Y %H:%M")
        return dt.isoformat(timespec="minutes")
    except ValueError:
        return None

def property_type(text: str) -> str:
    t = text.lower()
    rules = [
        ("Mehrfamilienhaus", ["mehrfamilienhaus", "mfh", "mietshaus"]),
        ("Einfamilienhaus", ["einfamilienhaus", "efh", "wohnhaus", "reihenhaus", "doppelhaushälfte", "doppelhaushaelfte"]),
        ("Eigentumswohnung", ["eigentumswohnung", "wohnungseigentum", "wohnung"]),
        ("Grundstück", ["grundstück", "grundstueck", "bauplatz", "acker", "landwirtschaftsfläche", "landwirtschaftsflaeche"]),
        ("Gewerbe", ["gewerbe", "laden", "halle", "büro", "buero", "hotel", "gaststätte", "gaststaette"]),
    ]
    for label, needles in rules:
        if any(n in t for n in needles):
            return label
    return "Sonstige"

def city_from_text(text: str) -> tuple[str | None, str | None]:
    m = PLZ_RE.search(text)
    if not m:
        return None, None
    plz = m.group(1)
    rest = text[m.end():]
    cm = re.match(r"\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-/. ]{1,45})", rest)
    city = clean(cm.group(1)) if cm else None
    if city:
        city = re.split(r"\s(?:Amtsgericht|Verkehrswert|Termin|K\s*\d)", city, maxsplit=1, flags=re.I)[0].strip(" ,-;/")
    return plz, city or None

def court_from_text(text: str) -> str | None:
    m = COURT_RE.search(text)
    return clean("Amtsgericht " + m.group(1)) if m else None

def detail_identity(href: str, state: str) -> tuple[str, str]:
    url = urljoin(BASE, href.replace("&amp;", "&"))
    qs = parse_qs(urlparse(url).query)
    zvg_id = (qs.get("zvg_id") or [""])[0]
    land = (qs.get("land_abk") or [state])[0]
    return zvg_id, land

def record_from_anchor(anchor, state: str) -> dict[str, Any]:
    href = anchor.get("href", "")
    zvg_id, land = detail_identity(href, state)
    tr = anchor.find_parent("tr")
    row_text = clean(tr.get_text(" ", strip=True) if tr else anchor.parent.get_text(" ", strip=True))
    plz, city = city_from_text(row_text)
    court = court_from_text(row_text)
    file_match = FILE_RE.search(row_text)
    center = STATE_CENTERS[state]
    jlat, jlng = stable_jitter(zvg_id or row_text)
    return {
        "id": f"{land}-{zvg_id or hashlib.sha1(row_text.encode()).hexdigest()[:12]}",
        "zvg_id": zvg_id or None,
        "state_code": land,
        "state": STATES.get(land, land),
        "court": court,
        "file_number": clean(file_match.group(0)) if file_match else None,
        "auction_date": parse_date(row_text),
        "property_type": property_type(row_text),
        "postcode": plz,
        "city": city,
        "address": None,
        "market_value": parse_money(row_text),
        "description": row_text,
        "source_url": urljoin(BASE, href.replace("&amp;", "&")),
        "gutachten_url": None,
        "has_report": False,
        "score": None,
        "lat": round(center[0] + jlat, 5),
        "lng": round(center[1] + jlng, 5),
        "position_precision": "state_approximation",
    }

def parse_search_html(html: bytes | str, state: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select('a[href*="button=showZvg"]')
    out: dict[str, dict[str, Any]] = {}
    for a in anchors:
        rec = record_from_anchor(a, state)
        out[rec["id"]] = rec
    return list(out.values())

def request_state(session: requests.Session, code: str, timeout: int = 60) -> list[dict[str, Any]]:
    data = {"land_abk": code, "ger_id": "0", "order_by": "2", "art": "0"}
    response = session.post(SEARCH_URL, data=data, timeout=timeout)
    response.raise_for_status()
    records = parse_search_html(response.content, code)
    if not records:
        raise RuntimeError(f"0 Detail-Links erkannt (HTTP {response.status_code}, {len(response.content)} Bytes)")
    return records

def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        arr = obj.get("results", obj) if isinstance(obj, dict) else obj
        return arr if isinstance(arr, list) else []
    except Exception:
        return []

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/auctions.json")
    ap.add_argument("--pause", type=float, default=float(os.getenv("ZVG_PAUSE", "0.7")))
    ap.add_argument("--states", default=os.getenv("ZVG_STATES", ",".join(STATES.keys())))
    args = ap.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing(output)
    previous_by_state: dict[str, list[dict[str, Any]]] = {}
    for r in existing:
        previous_by_state.setdefault(str(r.get("state_code", "")), []).append(r)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "ZVGRadarDataCollector/1.0 (+public court-auction index; respectful scheduled fetch)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        "Referer": "https://www.zvg-portal.de/index.php?button=Termine+suchen",
    })

    selected = [s.strip() for s in args.states.split(",") if s.strip() in STATES]
    all_records: list[dict[str, Any]] = []
    statuses: dict[str, Any] = {}
    success_count = 0

    for i, code in enumerate(selected):
        try:
            records = request_state(session, code)
            all_records.extend(records)
            statuses[code] = {"ok": True, "count": len(records)}
            success_count += 1
            print(f"{code}: {len(records)} Treffer")
        except Exception as exc:
            fallback = previous_by_state.get(code, [])
            all_records.extend(fallback)
            statuses[code] = {"ok": False, "error": str(exc), "kept_previous": len(fallback)}
            print(f"{code}: FEHLER: {exc}; alte Treffer behalten: {len(fallback)}", file=sys.stderr)
        if i + 1 < len(selected):
            time.sleep(max(0.0, args.pause))

    for code, old in previous_by_state.items():
        if code and code not in selected:
            all_records.extend(old)

    dedup = {str(r.get("id")): r for r in all_records if r.get("id")}
    all_records = list(dedup.values())

    if success_count == 0:
        print("Kein Bundesland erfolgreich. Vorhandene Datei wird NICHT überschrieben.", file=sys.stderr)
        return 2
    if len(all_records) < 10 and len(existing) >= 10:
        print("Verdächtig niedrige Gesamtzahl. Vorhandene Datei wird NICHT überschrieben.", file=sys.stderr)
        return 3

    all_records.sort(key=lambda r: (r.get("auction_date") or "9999", r.get("state_code") or "", r.get("id") or ""))
    payload = {
        "meta": {
            "source": "https://www.zvg-portal.de/",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "collector": "github-actions",
            "count": len(all_records),
            "states": statuses,
            "note": "Positionen sind ohne Geocoder nur auf Bundesland-Ebene angenähert. Amtliche Quelle bleibt maßgeblich.",
        },
        "results": all_records,
    }
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output)
    print(f"Gespeichert: {len(all_records)} Treffer -> {output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

# Trigger: initial GitHub Actions collection
