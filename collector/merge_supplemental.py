#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from datetime import datetime,timezone
from pathlib import Path

def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip().casefold()
def ncase(x): return re.sub(r"[^0-9a-z]+","",clean(x))
def date_day(x): return str(x or "")[:10]

def key(r):
    state=clean(r.get("state_code")); court=clean(r.get("court")); case=ncase(r.get("file_number")); day=date_day(r.get("auction_date"))
    if case and day: return ("case",state,court,case,day)
    return ("fallback",state,clean(r.get("postcode")),clean(r.get("city")),day,str(r.get("market_value") or ""))

def richness(r):
    fields=("postcode","city","market_value","property_type_raw","source_url","court","file_number","auction_date")
    return sum(1 for f in fields if r.get(f))

def merge_one(base,supp):
    out=dict(base)
    sources=list(out.get("sources") or [])
    for r in (base,supp):
        u=r.get("source_url")
        if u and u not in sources: sources.append(u)
    out["sources"]=sources
    out["source_adapters"]=sorted(set((out.get("source_adapters") or [])+[str(supp.get("source_adapter") or "supplemental")]))
    for f in ("postcode","city","market_value","property_type_raw","property_type","court","file_number","auction_date"):
        if not out.get(f) and supp.get(f) is not None: out[f]=supp.get(f)
    if supp.get("cancelled"): out["cancelled"]=True
    return out

def reconcile_checked_notices(rows, supplement):
    """Replace only HH/MV official notices from successfully reread issues.

    A missing/failed issue keeps its previous records. Main-portal records and
    every other state's data are outside this reconciliation.
    """
    meta=supplement.get("meta") or {}
    state=meta.get("state_code")
    incoming=supplement.get("results") or []
    if state not in {"hh","mv"} or meta.get("notice_parser_version")!=2:
        return rows,incoming,0
    from hh_mv_notices import issue_key
    checked=set(meta.get("checked_issues") or [])
    adapter=f"{state}-amtlicher-anzeiger"
    previous={r.get("id"):r for r in rows if r.get("state_code")==state and r.get("source_adapter")==adapter}
    kept=[]
    for r in rows:
        if (r.get("state_code")==state and r.get("source_adapter")==adapter
                and issue_key(state,r.get("source_url") or "") in checked):
            continue
        kept.append(r)
    refreshed=[]
    for r in incoming:
        if r.get("state_code")!=state or r.get("source_adapter")!=adapter:
            raise ValueError("HH/MV-Zusatzquelle enthält fremde Datensätze")
        prev=previous.get(r.get("id"))
        if prev:
            # Keep enrichment/history, but corrected facts take precedence.
            new={**prev,**r}
            for field in ("attachments","has_report"):
                if prev.get(field): new[field]=prev[field]
            if prev.get("notice_parser_version")==2 and prev.get("cancelled"):
                new["cancelled"]=True
            if prev.get("postcode")==r.get("postcode") and prev.get("position_precision")!="state_approximation":
                for field in ("lat","lng","position_precision"):
                    if field in prev: new[field]=prev[field]
            r=new
        refreshed.append(r)
    return kept,refreshed,len(rows)-len(kept)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",default="data/auctions.json"); ap.add_argument("--supplement",action="append",default=[]); a=ap.parse_args()
    p=Path(a.data); data=json.loads(p.read_text(encoding="utf-8")); rows=[r for r in data.get("results",[]) if isinstance(r,dict)]
    idx={key(r):i for i,r in enumerate(rows)}
    added=updated=replaced=0; by_source={}; official_status={}
    for spath in a.supplement:
        q=Path(spath)
        if not q.exists(): continue
        s=json.loads(q.read_text(encoding="utf-8"))
        rows,sr,removed=reconcile_checked_notices(rows,s)
        sr=[r for r in sr if isinstance(r,dict)]
        replaced+=removed
        idx={key(r):i for i,r in enumerate(rows)}
        smeta=s.get("meta") or {}
        if smeta.get("state_code") in {"hh","mv"} and smeta.get("notice_parser_version")==2:
            official_status[smeta["state_code"]]=smeta
        by_source[str(q)]=len(sr)
        for r in sr:
            k=key(r)
            if k in idx:
                i=idx[k]; rows[i]=merge_one(rows[i],r); updated+=1
            else:
                rows.append(r); idx[k]=len(rows)-1; added+=1
    rows.sort(key=lambda r:(r.get("auction_date") or "9999",r.get("state_code") or "",r.get("id") or ""))
    data["results"]=rows
    meta=data.setdefault("meta",{}); meta["supplemental_merge"]={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"added":added,"updated":updated,"inputs":by_source,"count_after":len(rows)}
    if official_status:
        meta["supplemental_merge"]["reconciled_official_records"]=replaced
        meta["official_notice_sources"]=official_status
    meta["count"]=len(rows)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(meta["supplemental_merge"],ensure_ascii=False))

if __name__=="__main__": main()

