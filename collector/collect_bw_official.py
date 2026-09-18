#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

INDEX="https://justizportal.justiz-bw.de/pb/%2CLde/Startseite/Wegweiser%2BJustiz/Amtsgerichte%2Balphabetisch"
CASE_RE=re.compile(r"\b(?:\d{1,3}\s*)?K\s*\d{1,5}\s*[/\-]\s*\d{2,4}\b",re.I)
DATE_RE=re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*(?:,|um)?\s*(\d{1,2}):(\d{2}))?",re.I)
PLZ_RE=re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß().\- /]{2,60})")
VALUE_RE=re.compile(r"(?:Verkehrswert|Wert)\s*:?\s*(?:EUR\s*)?([\d.]+(?:,\d{2})?)\s*(?:€|EUR)",re.I)
CAND_RE=re.compile(r"zwangsversteiger|versteigerungsobj|versteigerungstermin",re.I)

def clean(s):
    return re.sub(r"\s+"," ",s or "").strip()

def norm_case(s):
    s=clean(s).upper().replace("-","/"); s=re.sub(r"\s+"," ",s)
    s=re.sub(r"\s*/\s*","/",s)
    return s

def parse_value(s):
    if not s: return None
    try: return int(round(float(s.replace(".","").replace(",","."))))
    except Exception: return None

def parse_date(text):
    m=DATE_RE.search(text)
    if not m: return None
    d,mo,y,h,mi=m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}T{int(h or 0):02d}:{int(mi or 0):02d}"

def court_from_host(host):
    name=(host.split(".")[0] if host else "").replace("amtsgericht-","").replace("-"," ")
    return " ".join(x.capitalize() for x in name.split()) or None

def official(url):
    h=(urlparse(url).hostname or "").lower()
    return h.endswith("justiz-bw.de")

def discover_courts(session):
    r=session.get(INDEX,timeout=60); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    roots={}
    for a in soup.find_all("a",href=True):
        u=urljoin(r.url,a["href"])
        h=(urlparse(u).hostname or "").lower()
        if not h.endswith("justiz-bw.de") or "justizportal." in h:
            continue
        txt=clean(a.get_text(" ",strip=True))
        if "amtsgericht" in h or txt.lower().startswith("amtsgericht"):
            roots.setdefault(h, f"{urlparse(u).scheme}://{h}/")
    return sorted(roots.values())

def candidate_pages(session,root):
    out=[]
    try:
        r=session.get(root,timeout=30); r.raise_for_status()
    except Exception:
        return out
    soup=BeautifulSoup(r.text,"html.parser")
    for a in soup.find_all("a",href=True):
        txt=clean(a.get_text(" ",strip=True))
        u=urljoin(r.url,a["href"])
        if official(u) and (CAND_RE.search(txt) or CAND_RE.search(u)):
            if u not in out: out.append(u)
    return out[:8]

def chunks_from_page(html):
    soup=BeautifulSoup(html,"html.parser")
    for x in soup(["script","style","nav","header","footer"]): x.decompose()
    text=clean(soup.get_text(" ",strip=True))
    matches=list(CASE_RE.finditer(text))
    chunks=[]
    for i,m in enumerate(matches):
        start=max(0,m.start()-500)
        end=min(len(text), matches[i+1].start()+120 if i+1<len(matches) else m.end()+2200)
        chunks.append((norm_case(m.group()),text[start:end]))
    return chunks

def make_record(url,court,case,chunk):
    date=parse_date(chunk)
    if not date: return None
    plz=None; city=None
    pm=PLZ_RE.search(chunk)
    if pm:
        plz=pm.group(1); city=clean(pm.group(2)).split(" Verkehrswert")[0][:80]
    vm=VALUE_RE.search(chunk)
    value=parse_value(vm.group(1)) if vm else None
    cancelled=bool(re.search(r"aufgehoben|Termin aufgehoben|fällt aus|faellt aus",chunk,re.I))
    obj=None
    for token in ("Einfamilienhaus","Zweifamilienhaus","Mehrfamilienhaus","Eigentumswohnung","Wohnung","Grundstück","Gewerbe","Landwirtschaftsfläche","Garage"):
        if re.search(re.escape(token),chunk,re.I):
            obj=token; break
    key=f"bw|{court}|{case}|{date}"
    return {
      "id":"bw-official-"+hashlib.sha1(key.encode()).hexdigest()[:16],
      "source_adapter":"justiz-bw-official",
      "source_url":url,
      "state_code":"bw","state":"Baden-Württemberg",
      "court":court,"file_number":case,"auction_date":date,
      "cancelled":cancelled,"postcode":plz,"city":city,
      "property_type_raw":obj,"property_type":obj or "Sonstige",
      "market_value":value,"market_values":[value] if value is not None else [],
      "description":None,"attachments":[],"has_report":False,
      "position_precision":"state_approximation"
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="data/supplemental_bw.json")
    ap.add_argument("--pause",type=float,default=0.15)
    ap.add_argument("--max-courts",type=int,default=0)
    a=ap.parse_args()
    s=requests.Session(); s.headers.update({"User-Agent":"ZVGProOfficialSourceCollector/1.0 (+public court notices; low-rate fetch)","Accept-Language":"de-DE,de;q=0.9"})
    roots=discover_courts(s)
    if a.max_courts>0: roots=roots[:a.max_courts]
    records={}; stats={"courts_discovered":len(roots),"courts_with_zvg_pages":0,"pages":0,"errors":[]}
    for idx,root in enumerate(roots):
        pages=candidate_pages(s,root)
        if pages: stats["courts_with_zvg_pages"]+=1
        court=court_from_host(urlparse(root).hostname or "")
        for u in pages:
            try:
                r=s.get(u,timeout=35); r.raise_for_status(); stats["pages"]+=1
                for case,chunk in chunks_from_page(r.text):
                    rec=make_record(r.url,court,case,chunk)
                    if rec:
                        k=(rec["court"],rec["file_number"],rec["auction_date"])
                        records[k]=rec
            except Exception as e:
                if len(stats["errors"])<30: stats["errors"].append({"url":u,"error":str(e)})
            time.sleep(max(0,a.pause))
        if idx+1<len(roots): time.sleep(max(0,a.pause))
    arr=sorted(records.values(),key=lambda r:(r.get("auction_date") or "9999",r.get("court") or "",r.get("file_number") or ""))
    payload={"meta":{"source":INDEX,"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"count":len(arr),**stats,"note":"Nur amtliche justiz-bw.de-Seiten; keine Übernahme aus gewerblichen ZVG-Portalen."},"results":arr}
    p=Path(a.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload["meta"],ensure_ascii=False))
    return 0 if roots else 2

if __name__=="__main__": raise SystemExit(main())
