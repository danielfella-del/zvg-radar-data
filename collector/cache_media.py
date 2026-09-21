#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
import fitz

from photo_quality import analyze_pixmap, photo_score, looks_like_document_scan, quality_payload, VALIDATION_VERSION

RAW_BASE = "https://raw.githubusercontent.com/danielfella-del/zvg-radar-data/main/"
BASE = "https://www.zvg-portal.de/"
PORTAL_REFERER = BASE + "index.php?button=Suchen"
SEARCH_URL = BASE + "index.php?button=Suchen&all=1"
ALLOWED_TYPES = {"gutachten", "bekanntmachung", "expose", "foto", "hinweis", "dokument"}

def safe_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or ""))[:100].strip("._") or "file"

def parse_date(v):
    try:
        d = datetime.fromisoformat(str(v).replace("Z","+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None

def ext_from(content_type: str, url: str, name: str) -> str:
    ct=(content_type or "").split(";",1)[0].strip().lower()
    if ct=="application/pdf": return ".pdf"
    if ct in ("image/jpeg","image/jpg"): return ".jpg"
    if ct=="image/png": return ".png"
    if ct=="image/webp": return ".webp"
    if ct=="image/gif": return ".gif"
    for source in (name,url):
        p=urlparse(str(source)).path
        ext=Path(p).suffix.lower()
        if ext in {".pdf",".jpg",".jpeg",".png",".webp",".gif"}:
            return ".jpg" if ext==".jpeg" else ext
    guessed=mimetypes.guess_extension(ct) if ct else None
    return guessed or ".bin"

def prime_state_session(session: requests.Session, land: str) -> None:
    data = {
        "ger_name": "-- Alle Amtsgerichte --",
        "order_by": "2",
        "land_abk": land,
        "ger_id": "0",
        "az1": "", "az2": "", "az3": "", "az4": "",
        "art": "", "obj": "", "str": "", "hnr": "",
        "plz": "", "ort": "", "ortsteil": "", "vtermin": "", "btermin": "",
    }
    resp = session.post(
        SEARCH_URL,
        data=data,
        headers={"Referer": BASE + "index.php?button=Termine+suchen"},
        timeout=45,
    )
    resp.raise_for_status()
    if "showZvg" not in resp.text:
        raise RuntimeError(f"ZVG-Suche fuer {land} lieferte keine Detailverweise")

def detail_url(record: dict) -> str:
    return f"{BASE}index.php?button=showZvg&zvg_id={record.get('zvg_id')}&land_abk={record.get('state_code')}"

def prime_record_context(session: requests.Session, record: dict, primed_states: set[str]) -> str:
    land = str(record.get("state_code") or "")
    if land and land not in primed_states:
        prime_state_session(session, land)
        primed_states.add(land)
    durl = detail_url(record)
    resp = session.get(durl, headers={"Referer": PORTAL_REFERER}, timeout=45)
    resp.raise_for_status()
    if "showAnhang" not in resp.text and not record.get("attachments"):
        raise RuntimeError("Detailseite ohne Anhangskontext")
    return durl

def _clear_preview_fields(a: dict) -> None:
    for key in ("preview_path", "preview_url", "preview_content_type", "preview_bytes"):
        a.pop(key, None)


def ensure_photo_preview(a: dict, folder: Path, target: Path, fid: str) -> bool:
    if a.get("type") != "foto":
        return False
    is_pdf = a.get("content_type") == "application/pdf" or str(target).lower().endswith(".pdf")
    if not is_pdf or not target.exists():
        return False

    preview = folder / f"{fid}-preview.jpg"
    try:
        doc = fitz.open(target)
        best = None
        seen = set()

        # First prefer actual embedded raster images. This prevents a text-heavy
        # PDF page from being rendered and mistaken for an object photo.
        for page_no in range(min(doc.page_count, 120)):
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
                        ((float(rect.width) * float(rect.height)) / page_area for rect in rects),
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
                    adjusted = score + min(coverage, 0.8) * 70 - page_no * 0.2
                    if best is None or adjusted > best[0]:
                        best = (adjusted, page_no, xref, metrics, "embedded-image")
                except Exception:
                    continue

        # Some official "Foto" PDFs contain a flattened single-page photo.
        # Only use a rendered page when it itself looks photographic and contains
        # very little extractable text.
        if best is None:
            for page_no in range(min(doc.page_count, 40)):
                page = doc.load_page(page_no)
                page_text_chars = len((page.get_text("text") or "").strip())
                if page_text_chars > 120:
                    continue
                pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
                metrics = analyze_pixmap(pix)
                score = photo_score(metrics)
                if score is None:
                    continue
                if looks_like_document_scan(
                    metrics,
                    coverage=1.0,
                    page_text_chars=page_text_chars,
                    page_ratio_match=True,
                ):
                    continue
                adjusted = score - page_no * 0.2
                if best is None or adjusted > best[0]:
                    best = (adjusted, page_no, None, metrics, "rendered-photo-page")

        if best is None:
            doc.close()
            preview.unlink(missing_ok=True)
            _clear_preview_fields(a)
            a["photo_validation_version"] = VALIDATION_VERSION
            a["photo_quality_pass"] = False
            a["photo_quality_mode"] = "rejected-document-like-pdf"
            a["preview_error"] = "Kein belastbares Objektfoto erkannt"
            return False

        score, page_no, xref, metrics, mode = best
        if xref is not None:
            pix = fitz.Pixmap(doc, xref)
            if pix.n != 3 or pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)
        else:
            page = doc.load_page(page_no)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)

        while max(pix.width, pix.height) > 1600:
            pix.shrink(1)
        data = pix.tobytes("jpeg", jpg_quality=80)
        preview.write_bytes(data)
        doc.close()

        preview_rel = preview.as_posix()
        a["preview_path"] = preview_rel
        a["preview_url"] = RAW_BASE + preview_rel
        a["preview_content_type"] = "image/jpeg"
        a["preview_bytes"] = len(data)
        a.update(quality_payload(metrics, score, mode=mode, page=page_no + 1))
        a.pop("preview_error", None)
        return True
    except Exception as exc:
        preview.unlink(missing_ok=True)
        _clear_preview_fields(a)
        a["photo_validation_version"] = VALIDATION_VERSION
        a["photo_quality_pass"] = False
        a["photo_quality_mode"] = "validation-error"
        a["preview_error"] = str(exc)
        return False

def active_record(r, horizon_days: int) -> bool:
    if r.get("cancelled"): return False
    d=parse_date(r.get("auction_date"))
    if not d: return True
    now=datetime.now(d.tzinfo or timezone.utc)
    return d >= now - timedelta(days=7) and d <= now + timedelta(days=horizon_days)

QUICK_TYPES = {"bekanntmachung", "expose", "hinweis", "dokument"}
TYPE_PRIORITY = {"bekanntmachung": 0, "expose": 1, "hinweis": 2, "dokument": 3, "gutachten": 4, "foto": 5}

def attachment_priority(a: dict):
    t = str(a.get("type") or "")
    size = float(a.get("size_kb") or 10**12)
    return (0 if t in QUICK_TYPES else 1, size, TYPE_PRIORITY.get(t, 9), str(a.get("file_id") or ""))

def record_priority(r: dict):
    atts = r.get("attachments") or []
    stale_photo_preview = any(
        isinstance(a, dict)
        and a.get("type") == "foto"
        and a.get("cached_path")
        and str(a.get("cached_path") or "").lower().endswith(".pdf")
        and int(a.get("photo_validation_version") or 0) < VALIDATION_VERSION
        for a in atts
    )
    missing_quick = [a for a in atts if isinstance(a, dict) and a.get("type") in QUICK_TYPES and not a.get("cached_url")]
    missing_gutachten = any(isinstance(a, dict) and a.get("type") == "gutachten" and not a.get("cached_url") for a in atts)
    missing_foto = any(isinstance(a, dict) and a.get("type") == "foto" and (not a.get("cached_url") or not a.get("preview_url")) for a in atts)
    d = parse_date(r.get("auction_date"))
    ts = d.timestamp() if d else 9e18
    quick_size = min((float(a.get("size_kb") or 10**12) for a in missing_quick), default=10**12)
    return (0 if missing_quick else 1 if stale_photo_preview else 2 if missing_gutachten else 3 if missing_foto else 4, quick_size, ts, str(r.get("id") or ""))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default="data/auctions.json")
    ap.add_argument("--media-dir",default="media")
    ap.add_argument("--max-file-mb",type=float,default=float(os.getenv("ZVG_MEDIA_MAX_FILE_MB","18")))
    ap.add_argument("--max-run-mb",type=float,default=float(os.getenv("ZVG_MEDIA_MAX_RUN_MB","75")))
    ap.add_argument("--max-files",type=int,default=int(os.getenv("ZVG_MEDIA_MAX_FILES","120")))
    ap.add_argument("--horizon-days",type=int,default=int(os.getenv("ZVG_MEDIA_HORIZON_DAYS","180")))
    args=ap.parse_args()

    data_path=Path(args.data)
    media_root=Path(args.media_dir)
    media_root.mkdir(parents=True,exist_ok=True)
    payload=json.loads(data_path.read_text(encoding="utf-8"))
    records=payload.get("results",[])
    by_id={str(r.get("id")):r for r in records if r.get("id")}

    # Prune cached folders for procedures no longer relevant.
    active_ids={rid for rid,r in by_id.items() if active_record(r,args.horizon_days)}
    if media_root.exists():
        for child in media_root.iterdir():
            if child.is_dir() and child.name not in {safe_part(x) for x in active_ids}:
                shutil.rmtree(child,ignore_errors=True)

    session=requests.Session()
    session.headers.update({
        "User-Agent":"ZVGRadarMediaCache/1.1 (+public court-auction documents; bounded cache)",
        "Referer":PORTAL_REFERER,
        "Accept":"application/pdf,image/*,*/*;q=0.5",
    })

    max_file=int(args.max_file_mb*1024*1024)
    budget=int(args.max_run_mb*1024*1024)
    used=0
    downloaded=0
    failed=0
    cached_total=0
    primed_states: set[str] = set()
    context_ready: set[str] = set()
    error_samples=[]
    budget_exhausted=False

    for r in sorted(records, key=record_priority):
        if not active_record(r,args.horizon_days):
            continue
        rid=safe_part(r.get("id"))
        atts=r.get("attachments") or []
        if not isinstance(atts,list):
            continue
        atts=sorted(atts,key=attachment_priority)
        record_referer = PORTAL_REFERER
        if atts and r.get("zvg_id") and r.get("state_code"):
            try:
                if rid not in context_ready:
                    record_referer = prime_record_context(session, r, primed_states)
                    context_ready.add(rid)
                else:
                    record_referer = detail_url(r)
            except Exception as exc:
                failed += 1
                if len(error_samples) < 12:
                    error_samples.append({"id": str(r.get("id")), "stage": "context", "error": str(exc)})
                print(f"{r.get('id')}: Kontextfehler: {exc}", file=sys.stderr)
                continue
        for a in atts:
            if budget_exhausted:
                break
            if not isinstance(a,dict) or a.get("type") not in ALLOWED_TYPES:
                continue
            if a.get("cached_url"):
                cached_total+=1
                cached_path = a.get("cached_path")
                if a.get("type") == "foto" and cached_path:
                    target = Path(cached_path)
                    needs_validation = (
                        not a.get("preview_url")
                        or int(a.get("photo_validation_version") or 0) < VALIDATION_VERSION
                    )
                    if needs_validation and str(target).lower().endswith(".pdf"):
                        folder = target.parent
                        fid = safe_part(a.get("file_id") or target.stem)
                        if ensure_photo_preview(a, folder, target, fid):
                            print(f"{r.get('id')}: Foto-Vorschau validiert -> {a.get('preview_path')}")
                        elif a.get("preview_error"):
                            print(f"{r.get('id')}: Foto verworfen: {a.get('preview_error')}", file=sys.stderr)
                continue
            if downloaded>=args.max_files or used>=budget:
                budget_exhausted = used>=budget
                break
            url=a.get("url")
            if not url:
                continue
            declared_kb = float(a.get("size_kb") or 0)
            if declared_kb > 0 and declared_kb * 1024 > max_file:
                a["cache_status"]="oversize"
                a["cache_error"]=f"Datei groesser als GitHub-Cache-Limit ({declared_kb/1024:.1f} MB > {args.max_file_mb:.1f} MB); wird ueber ZVGPro direkt bereitgestellt"
                continue
            try:
                resp=session.get(url,headers={"Referer":record_referer},timeout=60,stream=True)
                resp.raise_for_status()
                ctype=(resp.headers.get("content-type") or "").lower()
                if "text/html" in ctype:
                    raise RuntimeError("Portal lieferte HTML statt Datei")
                declared=resp.headers.get("content-length")
                if declared and int(declared)>max_file:
                    raise RuntimeError("Datei groesser als Cache-Limit")
                if declared and used + int(declared) > budget:
                    budget_exhausted = True
                    break
                ext=ext_from(ctype,url,a.get("name") or "")
                fid=safe_part(a.get("file_id") or hashlib.sha1(url.encode()).hexdigest()[:12])
                name=f"{fid}{ext}"
                folder=media_root/rid
                folder.mkdir(parents=True,exist_ok=True)
                target=folder/name
                size=0
                with target.open("wb") as fh:
                    for chunk in resp.iter_content(256*1024):
                        if not chunk: continue
                        size+=len(chunk)
                        if size>max_file:
                            raise RuntimeError("Datei groesser als Cache-Limit")
                        if used+size>budget:
                            budget_exhausted = True
                            break
                        fh.write(chunk)
                if budget_exhausted:
                    target.unlink(missing_ok=True)
                    break
                if size<=0:
                    target.unlink(missing_ok=True)
                    raise RuntimeError("Leere Datei")
                used+=size
                downloaded+=1
                cached_total+=1
                rel=target.as_posix()
                a["cached_path"]=rel
                a["cached_url"]=RAW_BASE+rel
                a["cached_bytes"]=size
                a["content_type"]=ctype.split(";",1)[0] or None
                a["cached_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
                a["cache_status"]="ok"

                # ZVG kennzeichnet Fotos teilweise als PDF-Anhang. Fuer die
                # Web-Galerie erzeugen wir deshalb zusaetzlich eine echte
                # Bildvorschau aus der ersten PDF-Seite.
                if a.get("type") == "foto" and (a.get("content_type") == "application/pdf" or ext == ".pdf"):
                    if not ensure_photo_preview(a, folder, target, fid) and a.get("preview_error"):
                        print(f"{r.get('id')}: Foto-Vorschau fehlgeschlagen: {a.get('preview_error')}", file=sys.stderr)

                print(f"{r.get('id')}: {a.get('type')} -> {rel} ({size} bytes)")
            except Exception as exc:
                failed+=1
                a["cache_status"]="error"
                a["cache_error"]=str(exc)
                if len(error_samples) < 12:
                    error_samples.append({"id": str(r.get("id")), "file_id": str(a.get("file_id") or ""), "stage": "download", "error": str(exc)})
                print(f"{r.get('id')}: Medienfehler: {exc}",file=sys.stderr)
        if downloaded>=args.max_files or used>=budget or budget_exhausted:
            break

    # Recount media availability from actual cached objects.
    with_photos=0
    with_pdfs=0
    with_gutachten=0
    for r in records:
        atts=r.get("attachments") or []
        if any(
            (a.get("preview_url")) or
            (a.get("cached_url") and str(a.get("content_type") or "").startswith("image/"))
            for a in atts if isinstance(a,dict) and a.get("type") == "foto"
        ):
            with_photos+=1
        if any(a.get("cached_url") and (a.get("content_type")=="application/pdf" or str(a.get("cached_path","")).endswith(".pdf")) for a in atts if isinstance(a,dict)):
            with_pdfs+=1
        if any(a.get("type")=="gutachten" and a.get("cached_url") for a in atts if isinstance(a,dict)):
            with_gutachten+=1

    meta=payload.setdefault("meta",{})
    meta["media_cache"]={
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "downloaded_this_run":downloaded,
        "failed_this_run":failed,
        "bytes_this_run":used,
        "with_cached_photos":with_photos,
        "with_cached_pdfs":with_pdfs,
        "with_cached_gutachten":with_gutachten,
        "max_file_mb":args.max_file_mb,
        "max_run_mb":args.max_run_mb,
        "horizon_days":args.horizon_days,
        "primed_states":sorted(primed_states),
        "error_samples":error_samples,
    }
    tmp=data_path.with_suffix(data_path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(data_path)
    print(json.dumps(meta["media_cache"],ensure_ascii=False))

if __name__=="__main__":
    main()
