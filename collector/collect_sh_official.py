#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse
from official_notice_common import clean,pdf_text,split_case_chunks,make_record,dump
ROOT='https://www.schleswig-holstein.de/DE/justiz/themen/service/justizministerialblatt'
SEEDS=[
 ROOT,
 ROOT+'/Teil_B/_documents/Aktuell.html',
 'https://www.schleswig-holstein.de/DE/Justiz/justizministerialblatt/Teil_B/_documents/Aktuell.html'
]

def discover(s,max_pages=16,max_pdfs=8):
 queue=list(SEEDS); seen=set(); pdfs=[]
 while queue and len(seen)<max_pages and len(pdfs)<max_pdfs:
  u=queue.pop(0)
  if u in seen: continue
  seen.add(u)
  try:
   r=s.get(u,timeout=45); r.raise_for_status()
  except Exception: continue
  ct=(r.headers.get('content-type') or '').lower()
  if 'pdf' in ct:
   if u not in pdfs: pdfs.append(u)
   continue
  soup=BeautifulSoup(r.text,'html.parser')
  for a in soup.find_all('a',href=True):
   href=urljoin(r.url,a['href']); txt=clean(a.get_text(' ',strip=True)); low=(txt+' '+href).lower()
   host=(urlparse(href).hostname or '').lower()
   if not host.endswith('schleswig-holstein.de'): continue
   if ('.pdf' in href.lower() or 'publicationfile' in low) and ('teil_b' in low or 'teil b' in low or 'zwangsversteiger' in low):
    if href not in pdfs: pdfs.append(href)
   elif ('teil_b' in low or 'teil b' in low or 'justizministerialblatt' in low) and href not in seen and href not in queue:
    queue.append(href)
   if len(pdfs)>=max_pdfs: break
 return pdfs

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='data/supplemental_sh.json'); ap.add_argument('--max-issues',type=int,default=8); a=ap.parse_args()
 s=requests.Session(); s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.1 (+public court notices; low-rate fetch)'})
 links=discover(s,max_pdfs=a.max_issues); recs=[]; errs=[]
 for u in links:
  try:
   q=s.get(u,timeout=60); q.raise_for_status(); text=pdf_text(q.content)
   if 'Zwangsversteiger' not in text: continue
   for case,chunk in split_case_chunks(text):
    if 'versteiger' not in chunk.lower(): continue
    rec=make_record('sh','sh-schlha-teil-b',u,case,chunk,publication='Schleswig-Holsteinische Anzeigen Teil B')
    if rec: recs.append(rec)
  except Exception as e:
   if len(errs)<20: errs.append({'url':u,'error':str(e)})
 dump(a.output,ROOT,recs,{'issues_scanned':len(links),'errors':errs,'note':'Nur minimale faktische Eckdaten aus amtlichen Gerichtsbekanntmachungen; Quelle wird je Datensatz verlinkt.'})
if __name__=='__main__': main()
