#!/usr/bin/env python3
from __future__ import annotations
import argparse,requests
from datetime import datetime,timezone
from official_notice_common import pdf_text,split_case_chunks,make_record,dump

ROOT='https://www.schleswig-holstein.de/DE/justiz/themen/service/justizministerialblatt/Teil_B'
PDF_TMPL='https://www.schleswig-holstein.de/DE/justiz/themen/service/justizministerialblatt/Teil_B/_documents/Aktuelle_Ausgabe/{ym}.pdf?__blob=publicationFile&v=2'

def month_candidates(n=8):
    now=datetime.now(timezone.utc)
    y,m=now.year,now.month
    out=[]
    for _ in range(n):
        out.append(f'{y:04d}{m:02d}')
        m-=1
        if m==0: y-=1; m=12
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_sh.json')
    ap.add_argument('--max-issues',type=int,default=8)
    a=ap.parse_args()

    s=requests.Session()
    s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.3 (+public court notices; low-rate fetch)'})
    recs=[]; errs=[]; scanned=0
    for ym in month_candidates(a.max_issues):
        u=PDF_TMPL.format(ym=ym)
        try:
            q=s.get(u,timeout=(8,25))
            if q.status_code==404: continue
            q.raise_for_status()
            if 'pdf' not in (q.headers.get('content-type') or '').lower() and q.content[:4]!=b'%PDF':
                continue
            scanned+=1
            text=pdf_text(q.content)
            if 'Zwangsversteiger' not in text: continue
            for case,chunk in split_case_chunks(text,case_at_end=True):
                if 'versteiger' not in chunk.lower(): continue
                rec=make_record('sh','sh-schlha-teil-b',u,case,chunk,publication='Schleswig-Holsteinische Anzeigen Teil B')
                if rec:
                    rec['description']=None
                    recs.append(rec)
        except Exception as e:
            if len(errs)<12: errs.append({'url':u,'error':str(e)})
    dump(a.output,ROOT,recs,{
      'issues_scanned':scanned,
      'errors':errs,
      'note':'Nur minimale Falldaten aus Teil B; Veröffentlichungstexte werden nicht übernommen.'
    })

if __name__=='__main__':
    main()
