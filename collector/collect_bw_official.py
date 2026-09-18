#!/usr/bin/env python3
from __future__ import annotations
import argparse,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from official_notice_common import clean,split_case_chunks,make_record,dump,CASE_RE

SEARCH='https://immobilienpool.de/'
COURT_REFERENCE='https://justizportal.justiz-bw.de/pb/j1162216,Lde_DE/Startseite/Das+Amtsgericht/Zwangsversteigerungsabteilung'

def fetch_page(session,page):
    params={
      '_submit':'1',
      'geo1':'DEU',
      'geo2':'8',
      'path':'/Immobiliensuche',
      'page':str(page),
    }
    r=session.get(SEARCH,params=params,timeout=45)
    r.raise_for_status()
    return r

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_bw.json')
    ap.add_argument('--max-pages',type=int,default=45)
    a=ap.parse_args()

    s=requests.Session()
    s.headers.update({
      'User-Agent':'ZVGProOfficialSourceCollector/1.3 (+court-linked public auction listings; low-rate fetch)',
      'Accept-Language':'de-DE,de;q=0.9'
    })

    recs=[]; errors=[]; pages=0; previous_keys=set(); stale=0
    for page in range(1,a.max_pages+1):
        try:
            r=fetch_page(s,page)
            soup=BeautifulSoup(r.text,'html.parser')
            for x in soup(['script','style','nav','header','footer']): x.decompose()
            text=clean(soup.get_text(' ',strip=True))
            chunks=split_case_chunks(text,pre=550,post=1800)
            page_new=0
            for case,chunk in chunks:
                if 'versteiger' not in chunk.lower(): continue
                rec=make_record(
                    'bw',
                    'bw-immobilienpool-court-linked',
                    r.url,
                    case,
                    chunk,
                    publication='Von Baden-Württembergischen Amtsgerichten verlinkte Internet-Veröffentlichung'
                )
                if not rec: continue
                # Only minimal factual fields are retained. Do not republish editorial descriptions.
                rec['description']=None
                k=(rec.get('court'),rec.get('file_number'),str(rec.get('auction_date'))[:10])
                if k in previous_keys: continue
                previous_keys.add(k); recs.append(rec); page_new+=1
            pages+=1
            if page_new==0:
                stale+=1
                if stale>=3: break
            else:
                stale=0
        except Exception as e:
            if len(errors)<20: errors.append({'page':page,'error':str(e)})
            stale+=1
            if stale>=3 and page>3: break

    dump(a.output,SEARCH,recs,{
      'pages_scanned':pages,
      'errors':errors,
      'court_reference':COURT_REFERENCE,
      'note':'Baden-Württembergische Gerichte verweisen für Online-Termine auf immobilienpool.de; gespeichert werden nur Termin, Gericht, Aktenzeichen, Ort, Objektart und Verkehrswert.'
    })

if __name__=='__main__':
    main()
