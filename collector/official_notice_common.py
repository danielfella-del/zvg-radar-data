from __future__ import annotations
import hashlib, io, json, re, unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin
import fitz
from bs4 import BeautifulSoup

MONTHS={
 'januar':1,'februar':2,'märz':3,'maerz':3,'april':4,'mai':5,'juni':6,'juli':7,'august':8,
 'september':9,'oktober':10,'november':11,'dezember':12
}
CASE_RE=re.compile(r'\b(?:\d{1,4}\s*[A-Za-z]?\s*)?K\s*\d{1,5}\s*/\s*\d{2,4}\b',re.I)
COURT_RE=re.compile(r'Amtsgericht\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-(). ]{2,50})',re.I)
PLZ_RE=re.compile(r'\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,3})')

STATE_CENTERS={'bw':(48.65,9.35),'hh':(53.55,9.99),'mv':(53.8,12.6),'sh':(54.2,9.85)}
STATE_NAMES={'bw':'Baden-Württemberg','hh':'Hamburg','mv':'Mecklenburg-Vorpommern','sh':'Schleswig-Holstein'}

def clean(s:str)->str:
    text=unicodedata.normalize('NFKC',s or '')
    text=''.join(ch for ch in text if ch in '\n\r\t' or not unicodedata.category(ch).startswith('C'))
    text=re.sub(r'(?<=[A-Za-zÄÖÜäöüß])-\s+(?=[a-zäöüß])','',text)
    text=re.sub(r'(?<=[A-Za-zÄÖÜäöüß])-\s+(?=[A-ZÄÖÜ])','-',text)
    return re.sub(r'\s+',' ',text).strip()

def normalize_case(s:str)->str:
    s=clean(s).upper().replace('–','-')
    return re.sub(r'\s*/\s*','/',s)

def parse_eur(raw:str):
    if not raw: return None
    s=raw.replace('\xa0',' ').strip().replace('EUR','').replace('€','').strip()
    s=re.sub(r'[^\d.,]','',s)
    if not s: return None
    if ',' in s:
        s=s.replace('.','').replace(',','.')
    else:
        parts=s.split('.')
        if len(parts)>2 or (len(parts)==2 and len(parts[-1])==3): s=''.join(parts)
    try: return int(round(float(s)))
    except Exception: return None

def find_market_values(text:str):
    vals=[]
    pats=[
      r'Verkehrswert(?:\s+gemäß[^:]{0,100})?\s*:?\s*(?:[a-z]\)\s*)?([\d.\s]+(?:,\d{2})?)\s*(?:EUR|Euro|€)',
      r'Verkehrswerte?\s*:?\s*([\d.\s]+(?:,\d{2})?)\s*(?:EUR|Euro|€)'
    ]
    for p in pats:
        for m in re.finditer(p,text,re.I):
            v=parse_eur(m.group(1))
            if v and v not in vals: vals.append(v)
    return vals[:8]

def _parse_first_date(text:str):
    time_part=r'(?:\s*,?\s*(?:um\s*)?(\d{1,2})(?:[.:](\d{2}))?\s*(?:Uhr)?)?'
    m=re.search(r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(\d{4})'+time_part,text,re.I)
    if m:
        d,mon,y,h,mi=m.groups(); mm=MONTHS.get(mon.lower())
        if mm: return f'{y}-{mm:02d}-{int(d):02d}T{int(h or 0):02d}:{int(mi or 0):02d}'
    m=re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})'+time_part,text,re.I)
    if m:
        d,mo,y,h,mi=m.groups(); return f'{y}-{int(mo):02d}-{int(d):02d}T{int(h or 0):02d}:{int(mi or 0):02d}'
    return None

def parse_german_date(text:str):
    anchors=[
      r'Versteigerungstermin(?:\s+wird)?(?:\s+bestimmt)?\s*(?:auf|für)?',
      r'(?:soll|sollen)\s+am',
      r'öffentlich\s+versteigert\s+werden\s+am',
    ]
    for a in anchors:
        m=re.search(a,text,re.I)
        if m:
            iso=_parse_first_date(text[m.start():m.start()+420])
            if iso: return iso
    return _parse_first_date(text)

def futureish(iso:str|None, grace_days=2):
    if not iso: return False
    try:
        dt=datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        return dt.timestamp() >= datetime.now(timezone.utc).timestamp() - grace_days*86400
    except Exception: return True

def infer_type(text:str):
    t=text.lower()
    rules=[
      ('Mehrfamilienhaus',['mehrfamilienhaus','mietshaus']),
      ('Einfamilienhaus',['einfamilienhaus','zweifamilienhaus','reihenhaus','doppelhaushälfte','doppelhaushaelfte','wohnhaus']),
      ('Eigentumswohnung',['eigentumswohnung','wohnungseigentum','wohnung']),
      ('Grundstück',['grundstück','grundstueck','acker','landwirtschaft','bauplatz','flurstück','flurstueck']),
      ('Gewerbe',['gewerbe','büro','buero','halle','laden','hotel','gaststätte','gaststaette']),
    ]
    for label,needles in rules:
        if any(x in t for x in needles): return label
    return 'Sonstige'

def infer_plz_city(text:str):
    m=PLZ_RE.search(text)
    if not m: return None,None
    return m.group(1), clean(m.group(2)).strip(' ,.;')[:80]

def pdf_text(content:bytes):
    doc=fitz.open(stream=content,filetype='pdf')
    parts=[]
    for p in doc:
        try: parts.append(p.get_text('text') or '')
        except Exception: pass
    doc.close()
    return clean('\n'.join(parts))

def pdf_links_from_html(html:str,base_url:str,include_text_re:str|None=None):
    soup=BeautifulSoup(html,'html.parser'); out=[]
    rx=re.compile(include_text_re,re.I) if include_text_re else None
    for a in soup.find_all('a',href=True):
        u=urljoin(base_url,a['href']); txt=clean(a.get_text(' ',strip=True))
        if '.pdf' not in u.lower() and 'publicationFile' not in u: continue
        if rx and not rx.search(txt+' '+u): continue
        if u not in out: out.append(u)
    return out

def split_case_chunks(text:str,pre=350,post=3000,case_at_end=False):
    flat=clean(text)
    ms=list(CASE_RE.finditer(flat)); out=[]
    for i,m in enumerate(ms):
        if case_at_end:
            start=0 if i==0 else ms[i-1].end()
            end=min(len(flat),m.end()+max(pre,500))
        else:
            start=max(0,m.start()-pre)
            end=min(len(flat), ms[i+1].start()+pre if i+1<len(ms) else m.end()+post)
        out.append((normalize_case(m.group()),flat[start:end]))
    return out

def infer_court(chunk:str, fallback=None):
    ms=list(COURT_RE.finditer(chunk))
    if ms:
        c=clean(ms[-1].group(1))
        c=re.split(r'\s+(?:Vom|vom|Von|von|Abteilung|ein|eine|einen|im|in|öffentlich|oeffentlich|–|-)\b',c,flags=re.I)[0]
        return c[:80]
    return fallback

def make_record(state, source_adapter, source_url, case, chunk, fallback_court=None, publication=None):
    iso=parse_german_date(chunk)
    if not futureish(iso): return None
    court=infer_court(chunk,fallback_court)
    vals=find_market_values(chunk)
    plz,city=infer_plz_city(chunk)
    market=max(vals) if vals else None
    desc=clean(chunk)
    key='|'.join([state,court or '',case,iso or ''])
    lat,lng=STATE_CENTERS[state]
    return {
      'id':f'{state}-official-'+hashlib.sha1(key.encode()).hexdigest()[:16],
      'source_adapter':source_adapter,'source_url':source_url,'source_publication':publication,
      'state_code':state,'state':STATE_NAMES[state],'court':court,'file_number':case,
      'auction_date':iso,'cancelled':bool(re.search(r'aufgehoben|Termin aufgehoben|fällt aus|faellt aus',chunk,re.I)),
      'postcode':plz,'city':city,'property_type':infer_type(chunk),'property_type_raw':infer_type(chunk),
      'market_value':market,'market_values':vals,'description':desc[:1200],
      'attachments':[],'has_report':False,'lat':lat,'lng':lng,'position_precision':'state_approximation'
    }

def dump(path,source,records,meta=None):
    from pathlib import Path
    arr=[]; seen=set()
    for r in records:
        if not r: continue
        k=(r.get('court'),r.get('file_number'),str(r.get('auction_date'))[:10])
        if k in seen: continue
        seen.add(k); arr.append(r)
    arr.sort(key=lambda r:(r.get('auction_date') or '9999',r.get('court') or '',r.get('file_number') or ''))
    payload={'meta':{'source':source,'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'count':len(arr),**(meta or {})},'results':arr}
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload['meta'],ensure_ascii=False))
