#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path

TRACK = {
    'auction_date': 'Termin', 'market_value': 'Verkehrswert', 'cancelled': 'Status',
    'last_updated': 'Portal-Aktualisierung', 'court': 'Gericht', 'file_number': 'Aktenzeichen',
    'address': 'Adresse', 'postcode': 'PLZ', 'city': 'Ort', 'property_type': 'Objektart',
    'has_report': 'Gutachten', 'detail_source_last_updated': 'Detailstand'
}

def load(path: Path):
    try:
        d=json.loads(path.read_text(encoding='utf-8'))
        return d if isinstance(d,dict) else {'results':d}
    except Exception:
        return {'results':[]}

def compact_record(r):
    return {k:r.get(k) for k in ('id','file_number','court','city','postcode','property_type','auction_date','market_value','cancelled')}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--before',required=True); ap.add_argument('--data',default='data/auctions.json'); ap.add_argument('--max-events',type=int,default=30); a=ap.parse_args()
    before=load(Path(a.before)); current=load(Path(a.data)); now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    old={str(r.get('id')):r for r in before.get('results',[]) if isinstance(r,dict) and r.get('id')}
    cur={str(r.get('id')):r for r in current.get('results',[]) if isinstance(r,dict) and r.get('id')}
    changed=0; created=0
    for rid,r in cur.items():
        prev=old.get(rid)
        hist=[]
        if prev and isinstance(prev.get('history'),list): hist=[x for x in prev['history'] if isinstance(x,dict)]
        if not prev:
            hist.append({'at':now,'type':'first_seen','label':'Erstmals erfasst','snapshot':compact_record(r)})
            created+=1
        else:
            diffs=[]
            for key,label in TRACK.items():
                if prev.get(key)!=r.get(key): diffs.append({'field':key,'label':label,'from':prev.get(key),'to':r.get(key)})
            if diffs:
                hist.append({'at':now,'type':'changed','label':'Objektdaten geändert','changes':diffs})
                changed+=1
        r['history']=hist[-a.max_events:]
        r['first_seen_at']=(hist[0].get('at') if hist else (prev or {}).get('first_seen_at')) or now
    removed=[]
    for rid,r in old.items():
        if rid not in cur: removed.append(compact_record(r))
    meta=current.setdefault('meta',{})
    meta['history_tracking']={'generated_at':now,'new_this_run':created,'changed_this_run':changed,'removed_this_run':len(removed),'removed_sample':removed[:50]}
    Path(a.data).write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(meta['history_tracking'],ensure_ascii=False))
if __name__=='__main__': main()
