#!/usr/bin/env python3
from __future__ import annotations
import argparse,requests,re
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse
from official_notice_common import clean,pdf_text,split_case_chunks,make_record,dump

INDEX='https://www.luewu.de/amtlicher_anzeiger_hamburg/'

def discover_issue_pages(session,max_pages=6,max_issues=40):
    out=[]
    for page in range(1,max_pages+1):
        u=INDEX if page==1 else INDEX+f'?paged1={page}'
        r=session.get(u,timeout=30); r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        found=0
        for a in soup.find_all('a',href=True):
            label=clean(a.get_text(' ',strip=True))
            href=urljoin(r.url,a['href'])
            if re.search(r'Ausgabe\s+Nr\.\s*\d+',label,re.I) and '/aanz/' in href:
                if href not in out: out.append(href); found+=1
                if len(out)>=max_issues: return out
        if found==0 and page>2: break
    return out

def issue_pdf(session,url):
    r=session.get(url,timeout=30); r.raise_for_status()
    ct=(r.headers.get('content-type') or '').lower()
    if 'pdf' in ct: return r.url,r.content
    soup=BeautifulSoup(r.text,'html.parser')
    candidates=[]
    for a in soup.find_all('a',href=True):
        href=urljoin(r.url,a['href'])
        label=clean(a.get_text(' ',strip=True))
        low=(href+' '+label).lower()
        if '.pdf' in href.lower() and ('aanz' in low or 'anzeiger' in low or 'download' in low or 'pdf' in low):
            candidates.append(href)
    for href in candidates:
        q=session.get(href,timeout=40); q.raise_for_status()
        if 'pdf' in (q.headers.get('content-type') or '').lower() or q.content[:4]==b'%PDF':
            return q.url,q.content
    return None,None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_hh.json')
    ap.add_argument('--max-issues',type=int,default=40)
    ap.add_argument('--max-pages',type=int,default=6)
    a=ap.parse_args()

    s=requests.Session()
    s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.2 (+officially linked public notices; low-rate fetch)'})
    issues=discover_issue_pages(s,a.max_pages,a.max_issues)
    recs=[]; errs=[]; pdfs=0
    for page in issues:
        try:
            pdf_url,content=issue_pdf(s,page)
            if not content: continue
            pdfs+=1
            text=pdf_text(content)
            if 'Zwangsversteiger' not in text: continue
            for case,chunk in split_case_chunks(text):
                if 'versteiger' not in chunk.lower(): continue
                rec=make_record('hh','hh-amtlicher-anzeiger',pdf_url or page,case,chunk,publication='Amtlicher Anzeiger Hamburg')
                if rec:
                    rec['description']=None
                    recs.append(rec)
        except Exception as e:
            if len(errs)<20: errs.append({'url':page,'error':str(e)})
    dump(a.output,INDEX,recs,{
      'issues_discovered':len(issues),
      'pdfs_scanned':pdfs,
      'errors':errs,
      'note':'Es werden nur minimale Falldaten aus den online bereitgestellten Ausgaben des Amtlichen Anzeigers extrahiert.'
    })

if __name__=='__main__':
    main()
