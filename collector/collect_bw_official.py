#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,requests,unicodedata,time
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse
from official_notice_common import clean,split_case_chunks,make_record,dump

DIRECTORY='https://immobilienpool.de/amtsgerichte'
COURT_REFERENCE='https://justizportal.justiz-bw.de/pb/j1162216,Lde_DE/Startseite/Das+Amtsgericht/Zwangsversteigerungsabteilung'
BW_COURTS='''Aalen|Achern|Adelsheim|Albstadt|Backnang|Bad Mergentheim|Bad Säckingen|Bad Saulgau|Bad Urach|Bad Waldsee|Baden-Baden|Balingen|Besigheim|Biberach|Böblingen|Brackenheim|Breisach|Bretten|Bruchsal|Buchen|Bühl|Calw|Crailsheim|Donaueschingen|Ehingen|Ellwangen|Emmendingen|Esslingen|Ettenheim|Ettlingen|Freiburg|Freudenstadt|Geislingen|Gengenbach|Gernsbach|Göppingen|Hechingen|Heidelberg|Heidenheim|Heilbronn|Horb|Karlsruhe|Karlsruhe-Durlach|Kehl|Kenzingen|Kirchheim unter Teck|Konstanz|Künzelsau|Lahr|Langenburg|Leonberg|Leutkirch|Lörrach|Ludwigsburg|Mannheim|Marbach|Maulbronn|Mosbach|Müllheim|Münsingen|Nagold|Neresheim|Nürtingen|Oberkirch|Oberndorf|Offenburg|Öhringen|Pforzheim|Philippsburg|Radolfzell|Rastatt|Ravensburg|Reutlingen|Riedlingen|Rottenburg|Rottweil|Sankt Blasien|Schönau|Schopfheim|Schorndorf|Schwäbisch Gmünd|Schwäbisch Hall|Schwetzingen|Sigmaringen|Singen|Sinsheim|Spaichingen|Staufen|Stockach|Stuttgart|Stuttgart-Bad Cannstatt|Tauberbischofsheim|Tettnang|Titisee-Neustadt|Tübingen|Tuttlingen|Überlingen|Ulm|Vaihingen|Villingen-Schwenningen|Waiblingen|Waldkirch|Waldshut-Tiengen|Wangen|Weinheim|Wertheim|Wiesloch|Wolfach'''.split('|')

def norm(s):
    s=s.lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode()
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

NAMES={norm(x):x for x in BW_COURTS}
ALIASES={
  'biberach an der riss':'Biberach',
  'freiburg im breisgau':'Freiburg',
  'geislingen an der steige':'Geislingen',
  'horb am neckar':'Horb',
  'lahr schwarzwald':'Lahr',
  'leutkirch im allgaeu':'Leutkirch',
  'marbach am neckar':'Marbach',
  'radolfzell am bodensee':'Radolfzell',
  'rottenburg am neckar':'Rottenburg',
  'singen hohentwiel':'Singen',
  'staufen im breisgau':'Staufen',
  'vaihingen an der enz':'Vaihingen',
  'wangen im allgaeu':'Wangen',
  'bruchsal vollstreckungsgericht':'Bruchsal',
}

def is_bw_name(label):
    x=norm(re.sub(r'^Amtsgericht\s+','',label,flags=re.I))
    if x in NAMES or x in ALIASES: return True
    for n in NAMES:
        if len(n)>=5 and (x==n or x.startswith(n+' ') or n.startswith(x+' ')):
            return True
    return False

def discover_court_pages(session):
    r=session.get(DIRECTORY,timeout=45); r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    out=[]
    for a in soup.find_all('a',href=True):
        label=clean(a.get_text(' ',strip=True))
        if not label.lower().startswith('amtsgericht '): continue
        if not is_bw_name(label): continue
        href=urljoin(r.url,a['href'])
        host=(urlparse(href).hostname or '').lower()
        if not host.endswith('immobilienpool.de'): continue
        base=href.rstrip('/')
        if '/zwangsversteigerungen' not in base:
            base+='/zwangsversteigerungen'
        if base not in out: out.append(base)
    return out

def parse_court_page(session,url):
    r=session.get(url,timeout=35); r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    for x in soup(['script','style','nav','header','footer']): x.decompose()
    text=clean(soup.get_text(' ',strip=True))
    recs=[]
    for case,chunk in split_case_chunks(text,pre=450,post=1500):
        if 'versteigerungstermin' not in chunk.lower() and 'zwangsversteigerung' not in chunk.lower(): continue
        rec=make_record('bw','bw-immobilienpool-court-linked',r.url,case,chunk,publication='Von Baden-Württembergischen Amtsgerichten verlinkte Internet-Veröffentlichung')
        if rec:
            rec['description']=None
            recs.append(rec)
    return recs

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_bw.json')
    ap.add_argument('--max-courts',type=int,default=0)
    ap.add_argument('--pause',type=float,default=.04)
    a=ap.parse_args()
    s=requests.Session()
    s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.4 (+court-linked public auction listings; low-rate fetch)','Accept-Language':'de-DE,de;q=0.9'})
    pages=discover_court_pages(s)
    if a.max_courts>0: pages=pages[:a.max_courts]
    recs=[]; errors=[]; courts_ok=0
    for u in pages:
        try:
            rr=parse_court_page(s,u)
            if rr: courts_ok+=1; recs.extend(rr)
        except Exception as e:
            if len(errors)<30: errors.append({'url':u,'error':str(e)})
        time.sleep(max(0,a.pause))
    dump(a.output,DIRECTORY,recs,{
      'court_pages_discovered':len(pages),
      'courts_with_records':courts_ok,
      'errors':errors,
      'court_reference':COURT_REFERENCE,
      'note':'Baden-Württembergische Gerichte verweisen für Online-Termine auf immobilienpool.de; gespeichert werden nur minimale Falldaten.'
    })

if __name__=='__main__':
    main()
