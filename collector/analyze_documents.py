#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path
import fitz

SIGNALS=[
 ('moisture','Feuchtigkeit / Schimmel',r'feucht|schimmel|nässe|naesse|durchfeucht|kondensat'),
 ('renovation','Sanierungs- / Modernisierungsbedarf',r'sanierungsbed|modernisierungsbed|renovierungsbed|instandhaltungsstau|erneuerungsbed'),
 ('rights','Rechte / Belastungen erwähnt',r'grunddienstbarkeit|dienstbarkeit|wohnrecht|nießbrauch|niessbrauch|baulast|altenteil|reallast'),
 ('occupancy','Nutzung / Vermietung erwähnt',r'vermietet|mietverhältnis|mietverhaeltnis|eigengenutzt|bewohnt|leerstand|leer stehend'),
 ('energy','Energie / Heizung erwähnt',r'energieausweis|heizung|wärmepumpe|waermepumpe|gasheizung|ölheizung|oelheizung|fernwärme|fernwaerme'),
 ('damage','Schäden / Mängel erwähnt',r'mangel|mängel|maengel|schaden|schäden|schaeden|rissbildung|undicht'),
 ('heritage','Denkmalschutz erwähnt',r'denkmal|denkmalschutz'),
 ('contamination','Altlasten erwähnt',r'altlast|kontamin|bodenschad'),
]

def read_text(pdf:Path,max_pages=80):
    doc=fitz.open(str(pdf)); parts=[]
    for page in doc[:max_pages]:
        try: parts.append(page.get_text('text') or '')
        except Exception: pass
    pages=len(doc); doc.close(); return '\n'.join(parts),pages

def first_num(text,patterns):
    for p in patterns:
        m=re.search(p,text,re.I)
        if m: return m.group(1).replace('.','').replace(',','.')
    return None

def summary(text):
    clean=re.sub(r'\s+',' ',text)
    sents=re.split(r'(?<=[.!?])\s+',clean)
    good=[]
    for s in sents:
        if 45<=len(s)<=260 and not re.search(r'inhaltsverzeichnis|urheberrecht|copyright',s,re.I): good.append(s.strip())
        if len(good)>=3: break
    return ' '.join(good)[:700]

def resolve_pdf(root:Path,a:dict):
    for key in ('cached_path','local_path'):
        v=a.get(key)
        if v:
            p=(root/v).resolve() if not Path(v).is_absolute() else Path(v)
            if p.is_file(): return p
    u=str(a.get('cached_url') or '')
    if '/media/' in u:
        rel='media/'+u.split('/media/',1)[1].split('?',1)[0]
        p=root/rel
        if p.is_file(): return p
    return None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data',default='data/auctions.json'); ap.add_argument('--repo-root',default='.'); ap.add_argument('--limit',type=int,default=20); a=ap.parse_args()
    path=Path(a.data); root=Path(a.repo_root); payload=json.loads(path.read_text(encoding='utf-8')); now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    done=0; errors=0
    for r in payload.get('results',[]):
        if done>=a.limit: break
        atts=[x for x in (r.get('attachments') or []) if isinstance(x,dict) and x.get('type')=='gutachten']
        if not atts: continue
        pdf=next((resolve_pdf(root,x) for x in atts if resolve_pdf(root,x)),None)
        if not pdf: continue
        prev=r.get('smart_analysis') or {}
        sig=f"{pdf.stat().st_size}:{int(pdf.stat().st_mtime)}"
        if prev.get('source_signature')==sig: continue
        try:
            text,pages=read_text(pdf)
            if len(text.strip())<200: raise RuntimeError('PDF enthält zu wenig extrahierbaren Text')
            signals=[{'code':c,'label':label,'mentions':len(re.findall(rx,text,re.I))} for c,label,rx in SIGNALS if re.search(rx,text,re.I)]
            year=first_num(text,[r'Baujahr\D{0,25}(18\d{2}|19\d{2}|20\d{2})',r'errichtet\D{0,20}(18\d{2}|19\d{2}|20\d{2})'])
            living=first_num(text,[r'Wohnfläche\D{0,30}([\d.,]+)\s*m²',r'Wohnfläche\D{0,30}([\d.,]+)\s*qm'])
            land=first_num(text,[r'Grundstücksfläche\D{0,30}([\d.,]+)\s*m²',r'Grundstück\D{0,30}([\d.,]+)\s*qm'])
            r['smart_analysis']={'generated_at':now,'source':'lokal extrahierter Gutachtentext','source_file':pdf.name,'source_signature':sig,'pages':pages,'text_chars':len(text),'summary':summary(text),'facts':{'baujahr':year,'wohnflaeche_m2':living,'grundstueck_m2':land},'signals':signals,'disclaimer':'Automatisierte Textauswertung. Maßgeblich sind Gutachten, Bekanntmachung, Grundbuch und Terminbedingungen.'}
            done+=1
        except Exception as e:
            r['smart_analysis']={'generated_at':now,'source_file':pdf.name,'error':str(e)}; errors+=1
    payload.setdefault('meta',{})['smart_analysis']={'generated_at':now,'analyzed_this_run':done,'errors_this_run':errors}
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload['meta']['smart_analysis'],ensure_ascii=False))
if __name__=='__main__': main()
