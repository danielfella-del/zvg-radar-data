#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment

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

GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

FILE_RE = re.compile(r"\b(?:\d{1,4}\s*)?K\s*\d{1,5}\s*/\s*\d{2,4}\b", re.I)
PLZ_RE = re.compile(r"^(\d{5})\s*(.*)$")
MONEY_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?")

def repair_mojibake(text: str) -> str:
    if not text:
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
        return repaired if repaired != text else text
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(text or "")).strip()

def decode_document(html: bytes | str) -> str:
    if isinstance(html, str):
        return html
    # zvg-portal.de mixes Windows-1252 with occasional UTF-8 byte sequences.
    # Decode the full page conservatively as cp1252; clean() repairs mojibake
    # field-by-field without damaging genuine Latin-1 characters.
    return html.decode("cp1252", errors="replace")

def text_of(node) -> str:
    return clean(node.get_text(" ", strip=True) if node else "")

def stable_jitter(key: str) -> tuple[float, float]:
    d = hashlib.sha256(key.encode("utf-8", "ignore")).digest()
    return ((d[0] / 255 - 0.5) * 0.7, (d[1] / 255 - 0.5) * 1.0)

def parse_money_values(raw: str) -> list[int]:
    if not raw:
        return []
    text = clean(raw)
    # Monetary values on the portal almost always carry German decimal cents.
    # This avoids mistaking "Lfd. Nr. 1" or a Grundbuchblatt number for money.
    found = re.findall(r"(?<!\d)(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)", text)
    values = []
    for whole, frac in found:
        try:
            values.append(int(whole.replace(".", "")) + (1 if int(frac) >= 50 else 0))
        except ValueError:
            pass
    return values

def parse_money(raw: str) -> int | None:
    values = parse_money_values(raw)
    return sum(values) if values else None

def parse_portal_date(raw: str) -> str | None:
    raw = clean(raw)
    if not raw:
        return None
    numeric = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*,?\s*(\d{1,2}):(\d{2}))?", raw)
    if numeric:
        dd, mm, yyyy, hh, minute = numeric.groups()
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}T{int(hh or 0):02d}:{int(minute or 0):02d}"
    named = re.search(r"(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(\d{4})(?:,?\s*(\d{1,2}):(\d{2}))?", raw)
    if named:
        dd, month_name, yyyy, hh, minute = named.groups()
        mm = GERMAN_MONTHS.get(month_name.lower())
        if mm:
            return f"{yyyy}-{mm:02d}-{int(dd):02d}T{int(hh or 0):02d}:{int(minute or 0):02d}"
    return None

def normalize_property_type(raw: str | None) -> str:
    t = clean(raw or "").lower()
    if any(x in t for x in ["mehrfamilienhaus", "mietshaus"]):
        return "Mehrfamilienhaus"
    if any(x in t for x in ["einfamilienhaus", "zweifamilienhaus", "reihenhaus", "doppelhaushälfte", "doppelhaushaelfte", "wohnhaus"]):
        return "Einfamilienhaus"
    if any(x in t for x in ["eigentumswohnung", "wohnungseigentum", "wohnung"]):
        return "Eigentumswohnung"
    if any(x in t for x in ["grundstück", "grundstueck", "bauplatz", "acker", "landwirtschaft"]):
        return "Grundstück"
    if any(x in t for x in ["gewerbe", "laden", "halle", "büro", "buero", "hotel", "gaststätte", "gaststaette"]):
        return "Gewerbe"
    return "Sonstige"

def parse_address(raw: str) -> dict[str, str | None]:
    parts = [clean(p) for p in clean(raw).split(",") if clean(p)]
    out = {"street": None, "postcode": None, "city": None, "district": None}
    if not parts:
        return out
    out["street"] = parts[0]
    for i, part in enumerate(parts[1:], start=1):
        m = PLZ_RE.match(part)
        if m:
            out["postcode"] = m.group(1)
            out["city"] = clean(m.group(2)) or None
            if i + 1 < len(parts):
                out["district"] = ", ".join(parts[i+1:])
            return out
    if len(parts) > 1:
        out["city"] = ", ".join(parts[1:])
    return out

def detail_identity(href: str, state: str) -> tuple[str, str, str]:
    url = urljoin(BASE, href.replace("&amp;", "&"))
    qs = parse_qs(urlparse(url).query)
    zvg_id = (qs.get("zvg_id") or [""])[0]
    land = (qs.get("land_abk") or [state])[0]
    canonical = f"{BASE}index.php?button=showZvg&zvg_id={zvg_id}&land_abk={land}" if zvg_id else url
    return zvg_id, land, canonical

def attachment_type(label: str) -> str:
    l = label.lower()
    if "gutachten" in l:
        return "gutachten"
    if "expos" in l:
        return "expose"
    if "foto" in l:
        return "foto"
    return "bekanntmachung"

def parse_attachment(anchor, land: str, zvg_id: str) -> dict[str, Any] | None:
    href = (anchor.get("href") or "").replace("&amp;", "&")
    qs = parse_qs(urlparse(href).query)
    file_id = (qs.get("file_id") or [""])[0]
    if not file_id:
        return None
    label = text_of(anchor)
    url = f"{BASE}index.php?button=showAnhang&land_abk={land}&file_id={file_id}&zvg_id={zvg_id}"
    return {"type": attachment_type(label), "file_id": file_id, "name": label or None, "url": url}

def record_chunks(soup: BeautifulSoup):
    comments = soup.find_all(string=lambda t: isinstance(t, Comment) and re.search(r"Aktenzeichen", str(t), re.I))
    if not comments:
        return []
    chunks = []
    for c in comments:
        nodes = []
        node = c.next_sibling
        while node is not None:
            if isinstance(node, Comment) and re.search(r"Aktenzeichen", str(node), re.I):
                break
            nodes.append(str(node))
            node = node.next_sibling
        if nodes:
            chunks.append(BeautifulSoup("".join(nodes), "html.parser"))
    return chunks

def first_value_after_label(chunk: BeautifulSoup, label_pattern: str):
    pat = re.compile(label_pattern, re.I)
    for cell in chunk.find_all(["td", "th"]):
        if pat.search(text_of(cell)):
            nxt = cell.find_next_sibling(["td", "th"])
            if nxt:
                return nxt
    return None

def parse_chunk(chunk: BeautifulSoup, state: str) -> dict[str, Any] | None:
    detail = chunk.find("a", href=re.compile(r"button=showZvg", re.I))
    zvg_id = ""
    land = state
    source_url = None
    if detail:
        zvg_id, land, source_url = detail_identity(detail.get("href", ""), state)

    chunk_text = text_of(chunk)
    file_match = FILE_RE.search(chunk_text)
    file_number = clean(file_match.group(0)).replace(" / ", "/") if file_match else None

    court = None
    court_cell = first_value_after_label(chunk, r"^Amtsgericht$")
    if court_cell:
        full = text_of(court_cell.find("b") or court_cell)
        full = re.sub(r"\s+in\s+.+$", "", full, flags=re.I).strip()
        court = full or None

    cancelled_match = re.search(r"Der Termin\s+(.+?)\s+wurde aufgehoben\.", chunk_text, re.I)
    cancelled = bool(cancelled_match)

    object_type_raw = None
    address = {"street": None, "postcode": None, "city": None, "district": None}
    lage_cell = first_value_after_label(chunk, r"^Objekt/Lage$")
    if lage_cell:
        full = text_of(lage_cell)
        # Portal variants often put object type + address into the same <b>.
        # Example: "Einfamilienhaus : Dorfwiesenweg 3, 36124 Eichenzell, Büchenberg"
        if ":" in full:
            left, right = full.split(":", 1)
            object_type_raw = clean(left) or None
            address = parse_address(right)
        else:
            bold = lage_cell.find("b")
            if bold:
                object_type_raw = text_of(bold).rstrip(":") or None
                remainder = full[len(text_of(bold)):].lstrip(": ").strip()
                address = parse_address(remainder)
            else:
                object_type_raw = full or None

    value = None
    market_values = []
    value_cell = first_value_after_label(chunk, r"^Verkehrswert in")
    if value_cell:
        market_values = parse_money_values(text_of(value_cell))
        value = sum(market_values) if market_values else None

    auction_date = None
    if not cancelled:
        date_cell = first_value_after_label(chunk, r"^Termin$")
        if date_cell:
            auction_date = parse_portal_date(text_of(date_cell))

    last_updated = None
    um = re.search(r"letzte Aktualisierung:?\s*(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})", chunk_text, re.I)
    if um:
        dd, mm, yyyy, hh, minute = um.groups()
        last_updated = f"{yyyy}-{mm}-{dd}T{hh}:{minute}"

    attachments = []
    for a in chunk.find_all("a", href=re.compile(r"button=showAnhang", re.I)):
        att = parse_attachment(a, land, zvg_id)
        if att:
            attachments.append(att)

    gutachten = next((a["url"] for a in attachments if a["type"] == "gutachten"), None)
    expose = next((a["url"] for a in attachments if a["type"] == "expose"), None)

    center = STATE_CENTERS.get(land, (51.1, 10.4))
    key = zvg_id or file_number or chunk_text
    jlat, jlng = stable_jitter(key)

    identity = f"{land}-{zvg_id}" if zvg_id else f"{land}-{hashlib.sha1(chunk_text.encode()).hexdigest()[:12]}"
    if not (zvg_id or file_number or cancelled):
        return None

    return {
        "id": identity,
        "zvg_id": zvg_id or None,
        "state_code": land,
        "state": STATES.get(land, land),
        "court": court,
        "file_number": file_number,
        "auction_date": auction_date,
        "cancelled": cancelled,
        "cancelled_date_text": clean(cancelled_match.group(1)) if cancelled_match else None,
        "last_updated": last_updated,
        "property_type": normalize_property_type(object_type_raw),
        "property_type_raw": object_type_raw,
        "address": address["street"],
        "postcode": address["postcode"],
        "city": address["city"],
        "district": address["district"],
        "market_value": value,
        "market_values": market_values,
        "description": chunk_text[:1600],
        "source_url": source_url,
        "gutachten_url": gutachten,
        "expose_url": expose,
        "has_report": bool(gutachten),
        "attachments": attachments,
        "score": None,
        "lat": round(center[0] + jlat, 5),
        "lng": round(center[1] + jlng, 5),
        "position_precision": "state_approximation",
    }

def parse_search_html(html: bytes | str, state: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(decode_document(html), "html.parser")
    chunks = record_chunks(soup)
    out: dict[str, dict[str, Any]] = {}

    for chunk in chunks:
        rec = parse_chunk(chunk, state)
        if rec:
            out[rec["id"]] = rec

    # Fallback for portal variants without the record comments.
    if not out:
        anchors = soup.find_all("a", href=re.compile(r"button=showZvg", re.I))
        for a in anchors:
            tr = a.find_parent("tr")
            holder = BeautifulSoup(str(tr or a.parent), "html.parser")
            rec = parse_chunk(holder, state)
            if rec:
                out[rec["id"]] = rec

    return list(out.values())

def request_state(session: requests.Session, code: str, timeout: int = 60) -> list[dict[str, Any]]:
    data = {
        "ger_name": "-- Alle Amtsgerichte --",
        "order_by": "2",
        "land_abk": code,
        "ger_id": "0",
        "az1": "", "az2": "", "az3": "", "az4": "",
        "art": "", "obj": "", "str": "", "hnr": "",
        "plz": "", "ort": "", "ortsteil": "", "vtermin": "", "btermin": "",
    }
    response = session.post(SEARCH_URL, data=data, timeout=timeout)
    response.raise_for_status()
    records = parse_search_html(response.content, code)
    if not records:
        raise RuntimeError(f"0 Datensätze erkannt (HTTP {response.status_code}, {len(response.content)} Bytes)")
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

def load_postcode_centroids(session: requests.Session) -> dict[str, tuple[float, float]]:
    url = "https://download.geonames.org/export/zip/DE.zip"
    r = session.get(url, timeout=60)
    r.raise_for_status()
    points: dict[str, list[tuple[float, float]]] = {}
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".txt")), None)
        if not name:
            raise RuntimeError("GeoNames DE.zip enthält keine TXT-Datei")
        with zf.open(name) as fh:
            for raw in fh:
                parts = raw.decode("utf-8", errors="replace").rstrip("\n").split("\t")
                if len(parts) < 11:
                    continue
                postal = parts[1].strip()
                try:
                    lat = float(parts[9])
                    lng = float(parts[10])
                except ValueError:
                    continue
                if re.fullmatch(r"\d{5}", postal):
                    points.setdefault(postal, []).append((lat, lng))
    out = {}
    for postal, vals in points.items():
        out[postal] = (
            sum(v[0] for v in vals) / len(vals),
            sum(v[1] for v in vals) / len(vals),
        )
    return out

def apply_postcode_positions(records: list[dict[str, Any]], centroids: dict[str, tuple[float, float]]) -> int:
    changed = 0
    for r in records:
        plz = str(r.get("postcode") or "")
        pos = centroids.get(plz)
        if not pos:
            continue
        # Stable tiny jitter prevents identical markers from fully overlapping,
        # while keeping the location clearly PLZ-level rather than house-level.
        jlat, jlng = stable_jitter(str(r.get("id") or plz))
        r["lat"] = round(pos[0] + jlat * 0.04, 5)
        r["lng"] = round(pos[1] + jlng * 0.04, 5)
        r["position_precision"] = "postcode_centroid"
        changed += 1
    return changed

def quality_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records) or 1
    fields = ["court", "auction_date", "property_type_raw", "postcode", "city", "market_value", "gutachten_url"]
    stats = {}
    for key in fields:
        n = sum(1 for r in records if r.get(key))
        stats[key] = {"count": n, "pct": round(n * 100 / total, 1)}
    stats["cancelled"] = {"count": sum(1 for r in records if r.get("cancelled"))}
    return stats

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
        "User-Agent": "ZVGRadarDataCollector/1.2 (+public court-auction index; scheduled fetch)",
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
            statuses[code] = {"ok": True, "count": len(records), "quality": quality_stats(records)}
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

    postcode_positioned = 0
    try:
        centroids = load_postcode_centroids(session)
        postcode_positioned = apply_postcode_positions(all_records, centroids)
        print(f"PLZ-Positionen: {postcode_positioned}/{len(all_records)}")
    except Exception as exc:
        print(f"GeoNames-PLZ-Daten nicht verfügbar: {exc}", file=sys.stderr)

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
            "collector": "github-actions-v1.2-enriched",
            "count": len(all_records),
            "states": statuses,
            "quality": quality_stats(all_records),
            "postcode_positioned": postcode_positioned,
            "geodata_source": "GeoNames postal codes (CC BY 3.0) - https://www.geonames.org/",
            "note": "Amtliche Quelle bleibt maßgeblich. Kartenpositionen sind PLZ-Zentren bzw. ersatzweise Bundesland-Näherungen, keine Hauskoordinaten.",
        },
        "results": all_records,
    }
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output)
    print(f"Gespeichert: {len(all_records)} Treffer -> {output}")
    print(json.dumps(payload["meta"]["quality"], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
