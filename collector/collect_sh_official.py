#!/usr/bin/env python3
from __future__ import annotations
import argparse,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from official_notice_common import clean,pdf_text,split_case_chunks,make_record,dump

ROOT='https://www.schleswig-holstein.de/DE/justiz/themen/service/justizministerialblatt'
CURRENT='https://www.schleswig-holstein.de/DE/Justiz/justizministerialblatt/Teil_B/_documents/Aktuell.html'

def discover_current_pdfs(session,max_pdfs=4):
    r=session.get(CURRENT,timeout=25)
    r.raise_for_status()
    ct=(r.headers.get('content-type') or '').lower()
    if 'pdf' in ct:
        return [r.url]
    soup=BeautifulSoup(r.text,'html.parser')
    out=[]
    for a in soup.find_all('a',href=True):
        href=urljoin(r.url,a['href'])
        low=(clean(a.get_text(' ',strip=True))+' '+href).lower()
        if ('.pdf' in href.lower() or 'publicationfile' in low) and ('teil' in low or 'anzeig' in low or 'aktuell' in low):
            if href not in out: out.append(href)
            if len(out)>=max_pdfs: break
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_sh.json')
    ap.add_argument('--max-issues',type=int,default=4)
    a=ap.parse_args()

    s=requests.Session()
    s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.2 (+public court notices; low-rate fetch)'})
    links=discover_current_pdfs(s,a.max_issues)
    recs=[]; errs=[]
    for u in links:
        try:
            q=s.get(u,timeout=35); q.raise_for_status()
            text=pdf_text(q.content)
            if 'Zwangsversteiger' not in text: continue
            for case,chunk in split_case_chunks(text):
                if 'versteiger' not in chunk.lower(): continue
                rec=make_record('sh','sh-schlha-teil-b',u,case,chunk,publication='Schleswig-Holsteinische Anzeigen Teil B')
                if rec:
                    # Minimal factual extraction only; do not republish the protected publication text.
                    rec['description']=None
                    recs.append(rec)
        except Exception as e:
            if len(errs)<10: errs.append({'url':u,'error':str(e)})
    dump(a.output,ROOT,recs,{
      'issues_scanned':len(links),
      'errors':errs,
      'note':'Nur minimale Falldaten aus der amtlichen aktuellen Teil-B-Ausgabe; kein Veröffentlichungstext wird übernommen.'
    })

if __name__=='__main__':
    main()
