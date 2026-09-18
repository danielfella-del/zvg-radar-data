#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,time,unicodedata,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse
from official_notice_common import clean,split_case_chunks,make_record,dump,CASE_RE
INDEX='https://justizportal.justiz-bw.de/pb/%2CLde/Startseite/Wegweiser%2BJustiz/Amtsgerichte%2Balphabetisch'
KEY_RE=re.compile(r'zwangsversteiger|versteigerungsobj|versteigerungstermin|immobiliarvollstreck|vollstreckungsgericht',re.I)
GEN_RE=re.compile(r'aufgaben|aktuelles|service|verfahren',re.I)
COURTS='''Aalen|Achern|Adelsheim|Albstadt|Backnang|Bad Mergentheim|Bad Säckingen|Bad Saulgau|Bad Urach|Bad Waldsee|Baden-Baden|Balingen|Besigheim|Biberach an der Riß|Böblingen|Brackenheim|Breisach am Rhein|Bretten|Bruchsal|Buchen (Odenwald)|Bühl|Calw|Crailsheim|Donaueschingen|Ehingen (Donau)|Ellwangen (Jagst)|Emmendingen|Esslingen|Ettenheim|Ettlingen|Freiburg im Breisgau|Freudenstadt|Geislingen an der Steige|Gengenbach|Gernsbach|Göppingen|Hechingen|Heidelberg|Heidenheim|Heilbronn|Horb am Neckar|Karlsruhe|Karlsruhe-Durlach|Kehl|Kenzingen|Kirchheim unter Teck|Konstanz|Künzelsau|Lahr/Schwarzwald|Langenburg|Leonberg|Leutkirch im Allgäu|Lörrach|Ludwigsburg|Mannheim|Marbach am Neckar|Maulbronn|Mosbach|Müllheim|Münsingen|Nagold|Neresheim|Nürtingen|Oberkirch|Oberndorf|Offenburg|Öhringen|Pforzheim|Philippsburg|Radolfzell am Bodensee|Rastatt|Ravensburg|Reutlingen|Riedlingen|Rottenburg am Neckar|Rottweil|Sankt Blasien|Schönau im Schwarzwald|Schopfheim|Schorndorf|Schwäbisch Gmünd|Schwäbisch Hall|Schwetzingen|Sigmaringen|Singen (Hohentwiel)|Sinsheim|Spaichingen|Staufen im Breisgau|Stockach|Stuttgart|Stuttgart – Bad Cannstatt|Tauberbischofsheim|Tettnang|Titisee-Neustadt|Tübingen|Tuttlingen|Überlingen|Ulm|Vaihingen an der Enz|Villingen-Schwenningen|Waiblingen|Waldkirch|Waldshut-Tiengen|Wangen im Allgäu|Weinheim|Wertheim|Wiesloch|Wolfach'''.split('|')

def slug(s):
 s=s.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('Ä','ae').replace('Ö','oe').replace('Ü','ue').replace('ß','ss')
 s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
 s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
 s=s.replace('-an-der-riss','').replace('-im-breisgau','').replace('-am-neckar','').replace('-am-bodensee','').replace('-an-der-steige','').replace('-im-allgaeu','').replace('-im-schwarzwald','').replace('-am-rhein','')
 s=s.replace('-jagst','').replace('-donau','').replace('-odenwald','').replace('-hohentwiel','')
 return s

def official(u):
 h=(urlparse(u).hostname or '').lower(); return h.endswith('justiz-bw.de') and 'justizportal.' not in h

def discover_roots(s):
 roots={}
 try:
  r=s.get(INDEX,timeout=60); r.raise_for_status(); raw=r.text; soup=BeautifulSoup(raw,'html.parser')
  for a in soup.find_all('a',href=True):
   u=urljoin(r.url,a['href']); h=(urlparse(u).hostname or '').lower(); txt=clean(a.get_text(' ',strip=True))
   if official(u) and ('amtsgericht' in h or txt in COURTS): roots[h]=(u,txt if txt in COURTS else None)
  for m in re.finditer(r'https?://[^"\'<>\s]+justiz-bw\.de[^"\'<>\s]*',raw,re.I):
   u=m.group(0).replace('&amp;','&'); h=(urlparse(u).hostname or '').lower()
   if official(u): roots.setdefault(h,(f'{urlparse(u).scheme}://{h}/',None))
 except Exception:
  pass
 for name in COURTS:
  h=f'amtsgericht-{slug(name)}.justiz-bw.de'; roots.setdefault(h,(f'https://{h}/',name))
 return list(roots.values())

def crawl_court(s,root,known_name,max_pages=10):
 q=[(root,0)]; seen=set(); recs=[]; page_count=0; errs=[]
 host=(urlparse(root).hostname or '').lower(); fallback=known_name
 while q and page_count<max_pages:
  u,depth=q.pop(0)
  if u in seen: continue
  seen.add(u)
  try:
   r=s.get(u,timeout=25); r.raise_for_status()
  except Exception as e:
   if len(errs)<2: errs.append(str(e))
   continue
  if not official(r.url) or (urlparse(r.url).hostname or '').lower()!=host: continue
  page_count+=1; soup=BeautifulSoup(r.text,'html.parser')
  for x in soup(['script','style','nav','header','footer']): x.decompose()
  text=clean(soup.get_text(' ',strip=True))
  if CASE_RE.search(text) and ('versteiger' in text.lower() or 'verkehrswert' in text.lower()):
   for case,chunk in split_case_chunks(text,pre=500,post=2600):
    rec=make_record('bw','justiz-bw-official',r.url,case,chunk,fallback_court=fallback,publication='Amtliche Gerichtsseite Baden-Württemberg')
    if rec: recs.append(rec)
  if depth>=2: continue
  candidates=[]
  for a in soup.find_all('a',href=True):
   href=urljoin(r.url,a['href']); label=clean(a.get_text(' ',strip=True)); low=label+' '+href
   if not official(href) or (urlparse(href).hostname or '').lower()!=host: continue
   score=2 if KEY_RE.search(low) else (1 if depth==0 and GEN_RE.search(low) else 0)
   if score and href not in seen: candidates.append((score,href))
  for _,href in sorted(set(candidates),reverse=True)[:8]: q.append((href,depth+1))
 return recs,page_count,errs

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='data/supplemental_bw.json'); ap.add_argument('--max-courts',type=int,default=0); ap.add_argument('--pause',type=float,default=0.03); a=ap.parse_args()
 s=requests.Session(); s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.2 (+public court notices; low-rate fetch)','Accept-Language':'de-DE,de;q=0.9'})
 roots=discover_roots(s)
 if a.max_courts>0: roots=roots[:a.max_courts]
 recs=[]; pages=0; ok=0; errors=[]
 for root,name in roots:
  rr,pc,ee=crawl_court(s,root,name); pages+=pc
  if rr: ok+=1; recs.extend(rr)
  if ee and len(errors)<30: errors.append({'root':root,'errors':ee})
  time.sleep(max(0,a.pause))
 dump(a.output,INDEX,recs,{'courts_discovered':len(roots),'courts_with_records':ok,'pages_scanned':pages,'errors':errors,'note':'Nur amtliche justiz-bw.de-Gerichtsseiten; keine Übernahme aus gewerblichen ZVG-Portalen.'})
 return 0 if roots else 2
if __name__=='__main__': raise SystemExit(main())
