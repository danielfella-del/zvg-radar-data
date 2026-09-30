#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
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


PHOTO_CHECK_VERSION = 1


def source_signature(att: dict) -> str:
    # Only source metadata: hourly fetch/cache timestamps are not PDF changes.
    fields = ("file_id", "url", "size_kb", "etag", "last_modified", "sha256")
    value = {key: att.get(key) for key in fields}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def report_signature(att: dict) -> str:
    value = [PHOTO_CHECK_VERSION, source_signature(att), att.get("cached_bytes")]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def pending_reports(records: list[dict], horizon_days: int, priority: set[str], now: datetime):
    pending = []
    for record in records:
        if not active_record(record, horizon_days):
            continue
        for report in gutachten(record):
            check = report.get("report_photo_check") or {}
            unchanged = check.get("signature") == report_signature(report)
            if unchanged and check.get("status") in {"photos", "no_photos"}:
                continue
            retry = parse_date(check.get("retry_after"))
            if unchanged and check.get("status") == "error" and retry and retry > now:
                continue
            # New/changed reports come before retries, even for priority objects.
            rank = 1 if unchanged and check.get("status") == "error" else 0
            date = parse_date(record.get("auction_date"))
            key = (rank, 0 if str(record.get("id")) in priority else 1,
                   date.timestamp() if date else 9e18, str(record.get("id")),
                   str(report.get("file_id") or report.get("url") or ""))
            pending.append((key, record, report))
    pending.sort(key=lambda item: item[0])
    return [(record, report) for _, record, report in pending]


def mark_report(report: dict, status: str, now: datetime, error: str = "") -> None:
    previous = report.get("report_photo_check") or {}
    same = previous.get("signature") == report_signature(report)
    attempts = int(previous.get("attempts", 0)) + 1 if same else 1
    check = {"signature": report_signature(report), "source_signature": source_signature(report),
             "status": status, "checked_at": now.isoformat(timespec="seconds"),
             "attempts": attempts}
    if status == "error":
        check["refresh_required"] = bool(previous and (previous.get("refresh_required") or previous.get("source_signature") != source_signature(report)))
        check["retry_after"] = (now + timedelta(hours=min(24, 6 * attempts))).isoformat(timespec="seconds")
        check["error"] = error
    report["report_photo_check"] = check


def replace_report_photos(record: dict, report: dict, new: list[dict]) -> None:
    source_id = str(report.get("file_id") or "")
    source_url = str(report.get("url") or "")
    def from_this_report(item):
        if item.get("type") != "foto" or item.get("photo_source") != "gutachten":
            return False
        if source_id:
            return str(item.get("generated_from_file_id") or "") == source_id
        return bool(source_url and item.get("generated_from_url") == source_url)
    for item in new:
        item["generated_from_url"] = source_url
    record["attachments"] = [item for item in attachments(record) if not from_this_report(item)] + new


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
    candidates = pending_reports(records, args.horizon_days, priority, datetime.now(timezone.utc))

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
        for record, report in candidates:
            if processed >= args.max_reports:
                break
            processed += 1
            rid = str(record.get("id") or "")
            try:
                check = report.get("report_photo_check") or {}
                source_changed = bool(check and (check.get("refresh_required") or check.get("source_signature") != source_signature(report)))
                download_att = dict(report)
                if source_changed:
                    download_att.pop("cached_path", None)
                referer = (detail_url(record) if local_cached_pdf(download_att)
                           else prepare_context(session, record, primed_states))
                pdf, size = download_pdf(session, record, download_att, referer, tmp_dir, max_bytes)
                if not local_cached_pdf(download_att):
                    downloaded_bytes += size
                new = save_photos(pdf, record, report, media_root, args.max_photos)
                # A completed scan replaces only photos derived from this PDF.
                # On download/parse failure no existing photos are removed.
                if source_changed and local_cached_pdf(report):
                    shutil.copyfile(pdf, local_cached_pdf(report))
                    report["cached_bytes"] = size
                    report["cached_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                replace_report_photos(record, report, new)
                mark_report(report, "photos" if new else "no_photos", datetime.now(timezone.utc))
                record.pop("report_photo_error", None)
                if new:
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
                mark_report(report, "error", datetime.now(timezone.utc), str(exc))
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
