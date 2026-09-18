#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXPECTED = {
    "bw": ("Baden-Württemberg", 150),
    "by": ("Bayern", 250),
    "be": ("Berlin", 40),
    "br": ("Brandenburg", 60),
    "hb": ("Bremen", 5),
    "hh": ("Hamburg", 8),
    "he": ("Hessen", 180),
    "mv": ("Mecklenburg-Vorpommern", 35),
    "ni": ("Niedersachsen", 180),
    "nw": ("Nordrhein-Westfalen", 600),
    "rp": ("Rheinland-Pfalz", 150),
    "sl": ("Saarland", 30),
    "sn": ("Sachsen", 100),
    "st": ("Sachsen-Anhalt", 80),
    "sh": ("Schleswig-Holstein", 35),
    "th": ("Thüringen", 90),
}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data", default="data/auctions.json")
    ap.add_argument("--fail-on-missing", action="store_true")
    a=ap.parse_args()
    p=Path(a.data)
    obj=json.loads(p.read_text(encoding="utf-8"))
    rows=obj.get("results", [])
    counts=Counter(str(r.get("state_code") or "") for r in rows if isinstance(r,dict))
    states={}
    warnings=[]
    for code,(name,floor) in EXPECTED.items():
        n=counts.get(code,0)
        status="ok" if n>=floor else ("missing" if n==0 else "low")
        states[code]={"name":name,"count":n,"minimum_expected":floor,"status":status}
        if status!="ok":
            warnings.append(f"{code}: {name} nur {n} Verfahren (Warnschwelle {floor})")
    report={
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total":sum(counts.values()),
        "states":states,
        "warnings":warnings,
        "all_16_present":all(counts.get(c,0)>0 for c in EXPECTED),
    }
    obj.setdefault("meta",{})["coverage"]=report
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    if a.fail_on_missing and not report["all_16_present"]:
        return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
