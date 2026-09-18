#!/usr/bin/env python3
from __future__ import annotations
import argparse,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from official_notice_common import clean,pdf_text,split_case_chunks,make_record,dump
INDEX='https://www.luewu.de/amtlicher_anzeiger_hamburg/'

def discover(s,max_pages,max_issues):
 out=[]
 for page in range(1,max_pages+1):
  u=INDEX if page==1 else INDEX+f'?paged1={page}'
  r=s.get(u,timeout=45); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
  before=len(out)
  for a in soup.find_all('a',href=True):
   href=urljoin(r.url,a['href']); txt=clean(a.get_text(' ',strip=True))
   if '.pdf' in href.lower() and ('ausgabe' in txt.lower() or 'anzeiger' in txt.lower() or 'amt' in href.lower()):
    if href not in out: out.append(href)
    if len(out)>=max_issues: return out
  if len(out)==before and page>2: break
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='data/supplemental_hh.json'); ap.add_argument('--max-issues',type=int,default=36); ap.add_argument('--max-pages',type=int,default=6); a=ap.parse_args()
 s=requests.Session(); s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.1 (+officially linked public notices; low-rate fetch)'})
 links=discover(s,a.max_pages,a.max_issues); recs=[]; errs=[]
 for u in links:
  try:
   q=s.get(u,timeout=60); q.raise_for_status(); text=pdf_text(q.content)
   if 'Zwangsversteiger' not in text: continue
   for case,chunk in split_case_chunks(text):
    if 'versteiger' not in chunk.lower(): continue
    rec=make_record('hh','hh-amtlicher-anzeiger',u,case,chunk,publication='Amtlicher Anzeiger Hamburg')
    if rec: recs.append(rec)
  except Exception as e:
   if len(errs)<20: errs.append({'url':u,'error':str(e)})
 dump(a.output,INDEX,recs,{'issues_scanned':len(links),'errors':errs,'note':'Hamburg verweist für amtliche Bekanntmachungen auf den Amtlichen Anzeiger; extrahiert werden nur faktische Eckdaten.'})
if __name__=='__main__': main()
