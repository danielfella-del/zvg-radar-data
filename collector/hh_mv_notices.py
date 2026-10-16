"""Strict notice parsing used only by the Hamburg and MV collectors.

Keep this separate from official_notice_common: BW and SH retain their parser.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlparse

import fitz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from official_notice_common import MONTHS, STATE_CENTERS, STATE_NAMES, dump, infer_type, parse_eur

CASE = re.compile(r"\b(?:\d{1,4}\s*[A-Za-z]?\s*)?K\s*\d{1,5}\s*/\s*\d{2,4}\b")
COURTS = {
    "hh": ("Hamburg-St. Georg", "Hamburg-Barmbek", "Hamburg-Bergedorf",
           "Hamburg-Blankenese", "Hamburg-Harburg", "Hamburg-Altona",
           "Hamburg-Wandsbek", "Hamburg"),
    "mv": ("Neubrandenburg", "Greifswald", "Ludwigslust", "Hagenow", "Parchim",
           "Pasewalk", "Rostock", "Schwerin", "Stralsund", "Güstrow", "Waren (Müritz)", "Wismar"),
}


def session():
    client = requests.Session()
    client.headers["User-Agent"] = "ZVGProOfficialSourceCollector/2.0 (+public official notices)"
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET"}))
    client.mount("https://", HTTPAdapter(max_retries=retry))
    return client


def clean_notice(text):
    # Join discretionary PDF hyphens BEFORE removing control characters.
    text = re.sub(r"[\xad\x02][ \t]*\n[ \t]*", "", text)
    text = text.replace("\xad", "").replace("\x02", "")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=[A-Za-zÄÖÜäöüß])-\s+(?=[a-zäöüß])", "", text)
    text = re.sub(r"(?<=[A-Za-zÄÖÜäöüß])-\s+(?=[A-ZÄÖÜ])", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def read_pdf(content):
    # Native text order preserves the two/three newspaper columns. Sorting all
    # words by y would interleave different proceedings.
    with fitz.open(stream=content, filetype="pdf") as doc:
        parts = []
        for page in doc:
            blocks = page.get_text("blocks")
            parts.extend(b[4] for b in blocks if b[6] == 0 and b[1] >= 80
                         and b[3] < page.rect.height - 35)
    return clean_notice("\n".join(parts))


def issue_key(state, url):
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    if state == "hh":
        match = re.search(r"AANZ_HH_(20\d{2})-(\d+)", name, re.I)
        return f"hh:{match[1]}:{int(match[2])}" if match else None
    if state == "mv":
        match = re.search(r"(?:M[-_]V|Nr\.|Anzeiger)[ _]*(\d+(?:-\d+)?).*?(20\d{2})", name, re.I)
        return f"mv:{match[2]}:{match[1].lstrip('0')}" if match else None
    return None


DATE_TIME = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\.?\s*"
    r"(?P<month>[A-Za-zÄÖÜäöü]+|\d{1,2}\.?)\s*(?P<year>20\d{2})"
    r"\s*,?\s*(?:um\s+)?(?P<hour>\d{1,2})(?:[.:](?P<minute>\d{2}))?\s*Uhr\b", re.I)
ANCHOR = re.compile(r"\b(?:soll|sollen)\s+am\b|Versteigerungstermin\s+(?:wird\s+)?bestimmt\s+auf\b", re.I)


def auction_datetime(chunk):
    for anchor in ANCHOR.finditer(chunk):
        match = DATE_TIME.search(chunk, anchor.end(), anchor.end() + 160)
        if not match:
            continue
        month = match["month"].rstrip(".").lower()
        month = int(month) if month.isdigit() else MONTHS.get(month)
        if month is None:
            continue
        try:
            return datetime(int(match["year"]), month, int(match["day"]),
                            int(match["hour"]), int(match["minute"] or 0)).isoformat(timespec="minutes")
        except ValueError:
            return None
    return None


def cancelled_datetime(chunk):
    if not re.search(r"\bTermin\b.{0,600}\b(?:wurde|wird|ist)\s+aufgehoben", chunk, re.I):
        return None
    match = DATE_TIME.search(chunk[:400])
    if not match:
        return None
    # Reuse the same calendar/time validation, with an explicit date anchor.
    return auction_datetime('soll am ' + match.group())


def court_name(state, text):
    matches = []
    for name in COURTS[state]:
        pattern = re.escape(name).replace(r"\ ", r"\s*")
        match = re.search(r"Amtsgerichts?\s+" + pattern + r"(?![\w-])", text, re.I)
        if match:
            matches.append((match.start(), -len(name), name))
    return min(matches)[2] if matches else None


def notice_chunks(state, text):
    # Cross-references to another case are part of the current notice.
    cases = [m for m in CASE.finditer(text)
             if not re.search(r"\b(?:im|zum|des|dem) Verfahren\s*$", text[max(0, m.start()-60):m.start()], re.I)]
    for i, match in enumerate(cases):
        end = cases[i + 1].start() if i + 1 < len(cases) else len(text)
        chunk = text[match.end():end]
        # Do not consume the heading (and court) of the next proceeding.
        boundary = re.search(r"Bekanntmachung des Amtsgerichts|Termins?bestimmung\s*:|Termins?aufhebung\s*:|AmtsBl\.\s*M-V/AAz\.", chunk, re.I)
        if boundary:
            chunk = chunk[:boundary.start()]
        if state == "hh":
            signature = re.search(r"Hamburg,\s+den\s+\d{1,2}\.\s*\w+\s+20\d{2}\s+Das Amtsgericht", chunk)
            if signature:
                # All property facts precede the court's publication signature.
                chunk = chunk[:signature.start()]
        case = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", match.group())).strip()
        court = court_name(state, chunk)
        if not court and state == "mv":
            headings = list(re.finditer(r"Bekanntmachung des Amtsgerichts\s+[^:]+?(?=\s+Vom\b)", text[:match.start()]))
            if headings:
                court = court_name(state, headings[-1].group())
        yield case, chunk, court


def market_values(chunk):
    values = []
    # Restrict values to the labelled field, never purchase prices or security.
    for field in re.finditer(r"\bVerkehrswerte?\s*(?:gemäß[^:]{0,100})?\s*:\s*", chunk, re.I):
        tail = chunk[field.end():]
        tail = re.split(r"\b(?:Der|Die|Das|Hinweis|Auf|Weitere|weitere|Aufforderung|Lfd|Gesamtverkehrswert|Verkehrswert)\b", tail, maxsplit=1)[0]
        tail = re.sub(r",\s*[–—-]\s*(?=Euro|EUR|€)", ",00 ", tail)
        for match in re.finditer(r"(\d[\d. ]*(?:,\d{2})?)\s*(?:Euro|EUR|€)", tail):
            value = parse_eur(match[1])
            if value is not None:
                values.append(value)
    return values


def market_value(chunk, values):
    amount = r"(\d[\d. ]*(?:,\d{2}|,\s*[–—-])?)\s*(?:Euro|EUR|€)"
    total = re.search(r"\bGesamtverkehrswert[^:]{0,80}:\s*" + amount, chunk, re.I)
    if total:
        return parse_eur(re.sub(r",\s*[–—-]", ",00", total[1]))
    fields = list(re.finditer(r"\bVerkehrswert\s*(?:gemäß[^:]{0,100})?\s*:\s*" + amount, chunk, re.I))
    if len(fields) == 1 and not re.search(r"\bVerkehrswerte\b", chunk, re.I):
        return parse_eur(re.sub(r",\s*[–—-]", ",00", fields[0][1]))
    return values[0] if len(values) == 1 else None


def property_location(state, body):
    # A five-digit Grundbuchblatt number followed by 'BV' is not a postcode.
    city = r"(?:(?:Bad|Ostseebad|Groß|Klein)\s+)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+(?:\s+(?:am|an der|bei)\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)?"
    for match in re.finditer(r"\b(\d{5})\s+(" + city + r")", body):
        if re.search(r"\b(?:Blatt|Flurstück|Grundbuch)\s*$", body[max(0, match.start()-30):match.start()], re.I):
            continue
        if state == 'hh' and match[2] != 'Hamburg':
            continue
        if match[2] in {'BV', 'EUR', 'Euro'}:
            continue
        return match[1], match[2]
    return None, None


def parse_notices(state, url, text, *, today=None):
    if state not in COURTS:
        raise ValueError("This parser is restricted to HH and MV")
    today = today or date.today()
    text = clean_notice(text)
    records, rejected = [], []
    for case, chunk, court in notice_chunks(state, text):
        cancellation = cancelled_datetime(chunk)
        if not cancellation and not re.search(r"versteigert|Versteigerungstermin", chunk, re.I):
            continue
        if not cancellation and not ANCHOR.search(chunk) and not re.match(r"[.\s]*(?:Im Wege|Zum Zwecke|Folgender Grundbesitz)", chunk):
            # e.g. public service of documents in a K proceeding is not a date.
            continue
        iso = cancellation or auction_datetime(chunk)
        if not iso or not court:
            rejected.append({"file_number": case, "reason": "Termin oder Gericht nicht eindeutig erkannt"})
            continue
        if date.fromisoformat(iso[:10]) < today - timedelta(days=2):
            continue
        # Property text begins AFTER the hearing venue; its postcode is not the
        # property's address. Keep missing facts empty instead of guessing.
        body = re.split(r"versteigert\s+werden\s*:?", chunk, maxsplit=1, flags=re.I)
        body = body[1] if len(body) == 2 else ""
        body = re.split(r"Aufforderung:|Das über den Verkehrswert", body, maxsplit=1)[0]
        postcode, city = property_location(state, body)
        values = market_values(chunk)
        key = "|".join((state, court, case, iso))
        lat, lng = STATE_CENTERS[state]
        prop_type = infer_type(body)
        records.append({
            "id": f"{state}-official-" + hashlib.sha1(key.encode()).hexdigest()[:16],
            "source_adapter": f"{state}-amtlicher-anzeiger", "source_url": url,
            "source_publication": "Amtlicher Anzeiger " + STATE_NAMES[state],
            "source_issue": issue_key(state, url), "notice_parser_version": 2,
            "state_code": state, "state": STATE_NAMES[state], "court": court,
            "file_number": case, "auction_date": iso,
            "cancelled": bool(cancellation),
            "postcode": postcode, "city": city,
            "property_type": prop_type, "property_type_raw": prop_type,
            "market_value": market_value(chunk, values), "market_values": values,
            "description": None if state == "hh" else body[:1200] or None,
            "attachments": [], "has_report": False, "lat": lat, "lng": lng,
            "position_precision": "state_approximation",
        })
    return records, rejected


def collect_issues(state, index, urls, download, output):
    """Bounded I/O; parse PDFs serially (MuPDF is not thread-safe).

    Only completely parsed issues may replace older records from that issue.
    Partial source failures retain previous data and are visible as step errors.
    """
    if not urls:
        raise RuntimeError(f'{state}: keine Ausgaben gefunden')
    records, errors, checked = [], [], []
    scanned = 0

    def fetch(url):
        try:
            return url, download(url), None
        except Exception as exc:
            return url, None, str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for original, result, error in pool.map(fetch, urls):
            if error:
                errors.append({'url': original, 'error': error})
                continue
            pdf_url, content = result
            try:
                text = read_pdf(content)
                if not text:
                    raise ValueError('PDF enthält keinen auswertbaren Text')
                issue = issue_key(state, pdf_url)
                if not issue:
                    raise ValueError('Ausgabe nicht eindeutig zugeordnet')
                parsed, rejected = parse_notices(state, pdf_url, text)
                scanned += 1
                if scanned % 10 == 0:
                    print(f'{state}: {scanned}/{len(urls)} Ausgaben gelesen', flush=True)
                if rejected:
                    errors.extend({'url': pdf_url, **r} for r in rejected)
                else:
                    records.extend(parsed)
                    checked.append(issue)
            except Exception as exc:
                errors.append({'url': pdf_url, 'error': str(exc)})
    dump(output, index, records, {
        'state_code': state, 'notice_parser_version': 2,
        'ok': not errors, 'issues_discovered': len(urls), 'pdfs_scanned': scanned,
        'checked_issues': checked, 'errors': errors,
        'note': 'Termine aus amtlichen Bekanntmachungen; nicht belegte Angaben bleiben leer.',
    })
    if errors:
        raise RuntimeError(f'{state}: {len(errors)} Quellen-/Parserfehler; betroffene Ausgaben werden nicht ersetzt')
