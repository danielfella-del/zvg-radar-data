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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",default="data/auctions.json"); ap.add_argument("--supplement",action="append",default=[]); a=ap.parse_args()
    p=Path(a.data); data=json.loads(p.read_text(encoding="utf-8")); rows=[r for r in data.get("results",[]) if isinstance(r,dict)]
    idx={key(r):i for i,r in enumerate(rows)}
    added=updated=0; by_source={}
    for spath in a.supplement:
        q=Path(spath)
        if not q.exists(): continue
        s=json.loads(q.read_text(encoding="utf-8")); sr=[r for r in s.get("results",[]) if isinstance(r,dict)]
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
    meta["count"]=len(rows)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(meta["supplemental_merge"],ensure_ascii=False))

if __name__=="__main__": main()
