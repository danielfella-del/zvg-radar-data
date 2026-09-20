#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz
import requests

from photo_quality import analyze_pixmap, photo_score, looks_like_document_scan, quality_payload

RAW_BASE = "https://raw.githubusercontent.com/danielfella-del/zvg-radar-data/main/"
BASE = "https://www.zvg-portal.de/"
SEARCH_URL = BASE + "index.php?button=Suchen&all=1"
PORTAL_REFERER = BASE + "index.php?button=Suchen"


def safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))[:100].strip("._") or "file"


def parse_date(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def active_record(record: dict, horizon_days: int) -> bool:
    if record.get("cancelled"):
        return False
    d = parse_date(record.get("auction_date"))
    if not d:
        return True
    now = datetime.now(d.tzinfo or timezone.utc)
    return now - timedelta(days=3) <= d <= now + timedelta(days=horizon_days)


def attachments(record: dict) -> list[dict]:
    return [a for a in (record.get("attachments") or []) if isinstance(a, dict)]


def display_photos(record: dict) -> list[dict]:
    out = []
    for a in attachments(record):
        if a.get("type") != "foto":
            continue
        if a.get("preview_url") or (a.get("cached_url") and str(a.get("content_type") or "").startswith("image/")):
            out.append(a)
    return out


def gutachten(record: dict) -> list[dict]:
    return [a for a in attachments(record) if a.get("type") == "gutachten" and (a.get("url") or a.get("cached_path"))]


def prime_state_session(session: requests.Session, land: str) -> None:
    data = {
        "ger_name": "-- Alle Amtsgerichte --", "order_by": "2", "land_abk": land, "ger_id": "0",
        "az1": "", "az2": "", "az3": "", "az4": "", "art": "", "obj": "", "str": "", "hnr": "",
        "plz": "", "ort": "", "ortsteil": "", "vtermin": "", "btermin": "",
    }
    r = session.post(SEARCH_URL, data=data, headers={"Referer": BASE + "index.php?button=Termine+suchen"}, timeout=45)
    r.raise_for_status()
    if "showZvg" not in r.text:
        raise RuntimeError(f"ZVG-Suche fuer {land} lieferte keine Detailverweise")


def detail_url(record: dict) -> str:
    return f"{BASE}index.php?button=showZvg&zvg_id={record.get('zvg_id')}&land_abk={record.get('state_code')}"


def prepare_context(session: requests.Session, record: dict, primed_states: set[str]) -> str:
    land = str(record.get("state_code") or "")
    if land and land not in primed_states:
        prime_state_session(session, land)
        primed_states.add(land)
    durl = detail_url(record)
    r = session.get(durl, headers={"Referer": PORTAL_REFERER}, timeout=45)
    r.raise_for_status()
    if "showAnhang" not in r.text:
        raise RuntimeError("Detailseite ohne Anhangskontext")
    return durl


def local_cached_pdf(att: dict) -> Path | None:
    p = att.get("cached_path")
    if not p:
        return None
    path = Path(str(p))
    if path.is_file() and path.suffix.lower() == ".pdf":
        return path
    return None


def download_pdf(session: requests.Session, record: dict, att: dict, referer: str, temp_dir: Path, max_bytes: int) -> tuple[Path, int]:
    cached = local_cached_pdf(att)
    if cached:
        return cached, cached.stat().st_size
    url = att.get("url")
    if not url:
        raise RuntimeError("Gutachten ohne URL")
    r = session.get(url, headers={"Referer": referer}, timeout=90, stream=True)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "text/html" in ctype:
        raise RuntimeError("Portal lieferte HTML statt Gutachten")
    declared = r.headers.get("content-length")
    if declared and int(declared) > max_bytes:
        raise RuntimeError(f"Gutachten groesser als Foto-Limit ({int(declared)/1024/1024:.1f} MB)")
    target = temp_dir / f"{safe_part(record.get('id'))}-{safe_part(att.get('file_id') or 'gutachten')}.pdf"
    size = 0
    with target.open("wb") as fh:
        for chunk in r.iter_content(512 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                target.unlink(missing_ok=True)
                raise RuntimeError("Gutachten ueberschreitet Foto-Limit")
            fh.write(chunk)
    with target.open("rb") as check_fh:
        magic = check_fh.read(4)
    if size < 5 or magic != b"%PDF":
        target.unlink(missing_ok=True)
        raise RuntimeError("Anhang ist kein gueltiges PDF")
    return target, size


def extract_candidates(pdf: Path, max_pages: int = 140) -> list[tuple[float, int, int, dict]]:
    doc = fitz.open(pdf)
    found = []
    seen = set()
    try:
        for page_no in range(min(doc.page_count, max_pages)):
            page = doc.load_page(page_no)
            page_area = max(1.0, float(page.rect.width * page.rect.height))
            page_ratio = float(page.rect.width / max(1.0, page.rect.height))
            page_text_chars = len((page.get_text("text") or "").strip())
            for info in page.get_images(full=True):
                xref = int(info[0])
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(doc, xref)
                    metrics = analyze_pixmap(pix)
                    score = photo_score(metrics)
                    if score is None:
                        continue
                    rects = page.get_image_rects(xref)
                    coverage = max(
                        ((float(r.width) * float(r.height)) / page_area for r in rects),
                        default=0.0,
                    )
                    image_ratio = float(pix.width / max(1, pix.height))
                    page_ratio_match = abs(image_ratio - page_ratio) / max(0.01, page_ratio) < 0.10
                    if looks_like_document_scan(
                        metrics,
                        coverage=coverage,
                        page_text_chars=page_text_chars,
                        page_ratio_match=page_ratio_match,
                    ):
                        continue
                    adjusted = score + min(coverage, 0.8) * 55 - page_no * 0.30
                    found.append((adjusted, page_no, xref, metrics))
                except Exception:
                    continue
    finally:
        doc.close()
    found.sort(key=lambda x: (-x[0], x[1]))
    return found

def save_photos(pdf: Path, record: dict, att: dict, media_root: Path, max_photos: int) -> list[dict]:
    candidates = extract_candidates(pdf)
    if not candidates:
        return []
    out_dir = media_root / safe_part(record.get("id"))
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    created = []
    hashes = set()
    try:
        for score, page_no, xref, metrics in candidates:
            if len(created) >= max_photos:
                break
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n != 3 or pix.alpha:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                while max(pix.width, pix.height) > 1500:
                    pix.shrink(1)
                data = pix.tobytes("jpeg", jpg_quality=76)
                digest = hashlib.sha1(data).hexdigest()[:16]
                if digest in hashes:
                    continue
                hashes.add(digest)
                idx = len(created) + 1
                name = f"gutachten-photo-{safe_part(att.get('file_id') or 'report')}-{idx:02d}.jpg"
                target = out_dir / name
                target.write_bytes(data)
                try:
                    rel = target.relative_to(Path.cwd()).as_posix()
                except ValueError:
                    rel = target.as_posix()
                item = {
                    "type": "foto",
                    "file_id": f"report-photo-{att.get('file_id') or 'gutachten'}-{idx}",
                    "name": f"Objektfoto aus Gutachten {idx}",
                    "cached_path": rel,
                    "cached_url": RAW_BASE + rel,
                    "preview_path": rel,
                    "preview_url": RAW_BASE + rel,
                    "cached_bytes": len(data),
                    "content_type": "image/jpeg",
                    "preview_content_type": "image/jpeg",
                    "photo_source": "gutachten",
                    "generated_from_file_id": str(att.get("file_id") or ""),
                    "generated_from_type": "gutachten",
                    "source_page": page_no + 1,
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                item.update(quality_payload(metrics, score, mode="gutachten-embedded-image", page=page_no + 1))
                created.append(item)
            except Exception:
                continue
    finally:
        doc.close()
    return created


def candidate_key(record: dict, priority: set[str]):
    rid = str(record.get("id") or "")
    d = parse_date(record.get("auction_date"))
    ts = d.timestamp() if d else 9e18
    return (0 if rid in priority else 1, ts, rid)


def recount_photo_records(records: list[dict]) -> int:
    return sum(1 for r in records if display_photos(r))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/auctions.json")
    ap.add_argument("--media-dir", default="media")
    ap.add_argument("--max-reports", type=int, default=int(os.getenv("ZVG_REPORT_PHOTO_REPORTS", "6")))
    ap.add_argument("--max-photos", type=int, default=int(os.getenv("ZVG_REPORT_PHOTO_MAX", "4")))
    ap.add_argument("--max-report-mb", type=float, default=float(os.getenv("ZVG_REPORT_PHOTO_MAX_MB", "180")))
    ap.add_argument("--horizon-days", type=int, default=int(os.getenv("ZVG_REPORT_PHOTO_HORIZON", "120")))
    ap.add_argument("--priority-id", action="append", default=[])
    args = ap.parse_args()

    data_path = Path(args.data)
    media_root = Path(args.media_dir)
    media_root.mkdir(parents=True, exist_ok=True)
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    records = payload.get("results") or []
    priority = {str(x) for x in args.priority_id if x}
    candidates = [r for r in records if active_record(r, args.horizon_days) and gutachten(r) and len(display_photos(r)) < 2]
    candidates.sort(key=lambda r: candidate_key(r, priority))

    session = requests.Session()
    session.headers.update({
        "User-Agent": "ZVGRadarReportPhotoExtractor/1.0 (+public appraisal photo thumbnails)",
        "Referer": PORTAL_REFERER,
        "Accept": "application/pdf,*/*;q=0.5",
    })
    primed_states: set[str] = set()
    processed = 0
    with_new_photos = 0
    photos_created = 0
    errors = 0
    downloaded_bytes = 0
    samples = []
    max_bytes = int(args.max_report_mb * 1024 * 1024)

    with tempfile.TemporaryDirectory(prefix="zvg-report-photo-") as tmp:
        tmp_dir = Path(tmp)
        for record in candidates:
            if processed >= args.max_reports:
                break
            processed += 1
            rid = str(record.get("id") or "")
            try:
                referer = prepare_context(session, record, primed_states)
                report = gutachten(record)[0]
                pdf, size = download_pdf(session, record, report, referer, tmp_dir, max_bytes)
                if not report.get("cached_path"):
                    downloaded_bytes += size
                new = save_photos(pdf, record, report, media_root, args.max_photos)
                if new:
                    existing = attachments(record)
                    existing_keys = {(str(a.get("file_id") or ""), str(a.get("preview_url") or a.get("cached_url") or "")) for a in existing}
                    for item in new:
                        key = (str(item.get("file_id") or ""), str(item.get("preview_url") or ""))
                        if key not in existing_keys:
                            existing.append(item)
                    record["attachments"] = existing
                    record["report_photo_extracted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    record["report_photo_source_file_id"] = str(report.get("file_id") or "")
                    with_new_photos += 1
                    photos_created += len(new)
                    print(f"{rid}: {len(new)} Gutachten-Fotos erzeugt")
                else:
                    record["report_photo_error"] = "Keine geeigneten eingebetteten Objektbilder erkannt"
                    print(f"{rid}: keine geeigneten eingebetteten Fotos")
            except Exception as exc:
                errors += 1
                record["report_photo_error"] = str(exc)
                if len(samples) < 12:
                    samples.append({"id": rid, "error": str(exc)})
                print(f"{rid}: Foto-Extraktion FEHLER: {exc}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = payload.setdefault("meta", {})
    photo_records = recount_photo_records(records)
    meta["report_photo_extraction"] = {
        "generated_at": now,
        "processed_this_run": processed,
        "records_with_new_photos": with_new_photos,
        "photos_created": photos_created,
        "errors_this_run": errors,
        "downloaded_bytes": downloaded_bytes,
        "records_with_display_photos": photo_records,
        "candidate_backlog": max(0, len(candidates) - processed),
        "max_reports": args.max_reports,
        "max_photos_per_report": args.max_photos,
        "max_report_mb": args.max_report_mb,
        "priority_ids": sorted(priority),
        "error_samples": samples,
    }
    if isinstance(meta.get("media_cache"), dict):
        meta["media_cache"]["with_cached_photos"] = photo_records
        meta["media_cache"]["report_photo_extraction_at"] = now

    tmp_out = data_path.with_suffix(data_path.suffix + ".tmp")
    tmp_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_out.replace(data_path)
    print(json.dumps(meta["report_photo_extraction"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
