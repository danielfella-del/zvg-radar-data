#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from official_notice_common import clean,pdf_text,split_case_chunks,make_record,dump
INDEX='https://www.regierung-mv.de/Landesregierung/jm/service_justizministerium/verkuendungsblaetter/amtsblaetter'

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='data/supplemental_mv.json'); ap.add_argument('--max-issues',type=int,default=32); a=ap.parse_args()
 s=requests.Session(); s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.1 (+public official notices; low-rate fetch)'})
 r=s.get(INDEX,timeout=60); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
 links=[]
 for x in soup.find_all('a',href=True):
  txt=clean(x.get_text(' ',strip=True)); u=urljoin(r.url,x['href'])
  if 'amtlicher anzeiger' in txt.lower() and ('.pdf' in u.lower() or 'publicationFile' in u):
   if u not in links: links.append(u)
 links=links[:a.max_issues]
 recs=[]; errs=[]
 for u in links:
  try:
   q=s.get(u,timeout=60); q.raise_for_status(); text=pdf_text(q.content)
   if 'Zwangsversteiger' not in text: continue
   for case,chunk in split_case_chunks(text):
    if 'versteiger' not in chunk.lower(): continue
    rec=make_record('mv','mv-amtlicher-anzeiger',u,case,chunk,publication='Amtlicher Anzeiger Mecklenburg-Vorpommern')
    if rec: recs.append(rec)
  except Exception as e:
   if len(errs)<20: errs.append({'url':u,'error':str(e)})
 dump(a.output,INDEX,recs,{'issues_scanned':len(links),'errors':errs,'note':'Faktische Eckdaten aus amtlichen Bekanntmachungen.'})
if __name__=='__main__': main()
