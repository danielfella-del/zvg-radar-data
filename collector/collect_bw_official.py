#!/usr/bin/env python3
from __future__ import annotations
import argparse,re,requests,unicodedata,time,json,hashlib
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse,urlunsplit
from official_notice_common import clean,normalize_case,parse_eur,infer_type,futureish,STATE_CENTERS

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

ADAPTER='bw-immobilienpool-court-linked'
PARSER_VERSION=2
POOL_HOSTS={'immobilienpool.de','www.immobilienpool.de','versteigerungspool.de','www.versteigerungspool.de'}

def listing_identity(url):
    p=urlparse(str(url or ''))
    m=re.fullmatch(r'/amtsgerichte/[^/]+\.(\d+)/zwangsversteigerungen/?',p.path)
    return m.group(1) if p.scheme in {'http','https'} and p.hostname in POOL_HOSTS and m else None

def canonical_listing(url):
    p=urlparse(url)
    if not listing_identity(url): raise ValueError('Nicht erlaubte BW-Gerichtsübersicht')
    return urlunsplit(('https','immobilienpool.de',p.path.rstrip('/'),'',''))

def canonical_court(title):
    name=norm(re.sub(r'^Amtsgericht\s+','',clean(title),flags=re.I))
    court=NAMES.get(name) or ALIASES.get(name)
    if not court: raise ValueError('Gericht nicht eindeutig Baden-Württemberg zugeordnet')
    return court

def case_identity(value):
    # Keep every department token: e.g. "3 2 K 28/25" is not "2 K 28/25".
    value=normalize_case(value)
    match=re.fullmatch(r'((?:\d{1,4}\s*[A-Z]?\s*)*)K\s*(\d{1,5})/(\d{2}|\d{4})',value)
    if not match:
        raise ValueError('Vollständiges Aktenzeichen fehlt')
    prefix=re.sub(r'(\d)\s+([A-Z])',r'\1\2',match[1])
    tokens=[re.sub(r'\d+',lambda m:str(int(m.group())),t) for t in re.findall(r'\d+[A-Z]?',prefix)]
    year=int(match[3]);year=year+2000 if year<100 else year
    return (' '.join(tokens)+' K '+str(int(match[2]))+'/'+str(year)).strip()

def parse_listing_html(html,source_url,with_meta=False):
    """Read only fields inside one source object, never nearby page text."""
    listing=canonical_listing(source_url)
    node=BeautifulSoup(html,'html.parser').find(id='app')
    if node is None or not node.get('data-page'): raise ValueError('Strukturierte BW-Falldaten fehlen')
    page=json.loads(node['data-page']); props=page.get('props') or {}
    court=props.get('court') or {}; payload=props.get('auctions')
    if str(court.get('id'))!=listing_identity(listing): raise ValueError('Gerichts-ID der Quelle passt nicht')
    name=canonical_court(court.get('title') or '')
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):
        raise ValueError('Unvollständige BW-Objektliste')
    links=payload.get('links');meta=payload.get('meta')
    if not isinstance(links,dict) or 'next' not in links or not isinstance(meta,dict):
        raise ValueError('BW-Pagination nicht belegt')
    if any(type(meta.get(k)) is not int for k in ('current_page','last_page','total')) or not (1<=meta['current_page']<=meta['last_page']) or meta['total']<len(payload['data']):
        raise ValueError('BW-Pagination unvollständig')
    if bool(links['next'])!=(meta['current_page']<meta['last_page']):
        raise ValueError('BW-Folgeseite fehlt oder ist widersprüchlich')
    recs=[]; seen=set()
    for item in payload['data']:
        if not isinstance(item,dict): raise ValueError('Ungültiger BW-Objekteintrag')
        external_id=str(item.get('id') or '')
        link=urljoin(listing,str(item.get('link') or '')); lp=urlparse(link)
        if not external_id.isdigit() or lp.hostname not in POOL_HOSTS or lp.scheme!='https' or lp.username or lp.password or lp.port not in (None,443) or not re.fullmatch(r'/zwangsversteigerungen/[^/]+\.'+re.escape(external_id),lp.path):
            raise ValueError('BW-Objekt-ID und Objektlink passen nicht')
        link=urlunsplit(('https','immobilienpool.de',lp.path,'',''))
        if external_id in seen: raise ValueError('Doppelte BW-Objekt-ID innerhalb einer Seite')
        seen.add(external_id)
        facts=item.get('facts') or []
        case_facts=[f for f in facts if isinstance(f,dict) and str(f.get('key','')).startswith('Amtsgericht ')]
        if len(case_facts)!=1 or canonical_court(str(case_facts[0]['key']).rstrip(':'))!=name:
            raise ValueError('BW-Objekt und Gericht passen nicht')
        case=normalize_case(case_facts[0].get('value') or ''); case_identity(case)
        date_text=clean(item.get('auctionDate') or '')
        try: iso=datetime.strptime(date_text,'%d.%m.%Y, %H:%M Uhr').isoformat(timespec='minutes')
        except ValueError as exc: raise ValueError('Eindeutiger BW-Versteigerungstermin fehlt') from exc
        if not futureish(iso): continue
        title=clean(item.get('title') or '')
        if not title: raise ValueError('BW-Objektbezeichnung fehlt')
        place=clean(item.get('city') or ''); place_match=re.fullmatch(r'(\d{5})\s+(.+)',place)
        postcode,city=(place_match.groups() if place_match else (None,None))
        value=None
        if item.get('priceType')=='Verkehrswert' and not item.get('priceFrom') and not item.get('otherPrices'):
            raw=clean(item.get('price') or '')
            if re.fullmatch(r'\d{1,3}(?:\.\d{3})*(?:,\d{2})?\s*€|\d+(?:,\d{2})?\s*€',raw): value=parse_eur(raw)
        lat,lng=STATE_CENTERS['bw']
        # New source objects use their immutable external ID. Existing IDs are
        # reused separately, only after an unambiguous identity comparison.
        oid='bw-official-'+hashlib.sha1(('bw|pool|'+external_id).encode()).hexdigest()[:16]
        recs.append({'id':oid,'source_adapter':ADAPTER,'source_url':link,
          'source_listing_url':listing,'source_external_id':external_id,'source_parser_version':PARSER_VERSION,
          'source_publication':'Von Baden-Württembergischen Amtsgerichten verlinkte Internet-Veröffentlichung',
          'state_code':'bw','state':'Baden-Württemberg','court':name,'file_number':case,
          'auction_date':iso,'cancelled':item.get('cancelled') is True,'postcode':postcode,'city':city,
          'address':clean(item.get('address') or '') or None,
          'property_type':infer_type(title),'property_type_raw':title,'market_value':value,
          'market_values':[value] if value is not None else [],'description':None,
          # This listing does not check appraisal availability. False here is
          # only the legacy "no imported report" flag, never proof of absence.
          'attachments':[],'has_report':False,'lat':lat,'lng':lng,'position_precision':'state_approximation'})
    nxt=links['next']
    if nxt:
        nxt=urljoin(listing,nxt)
        if listing_identity(nxt)!=listing_identity(listing): raise ValueError('Pagination verweist auf anderes Gericht')
    return (recs,nxt,{**meta,'raw_ids':seen}) if with_meta else (recs,nxt)

def parse_court_page(session,url,max_pages=6):
    recs=[]; seen={}; visited=set(); raw_ids=set();total=None;last=None;page_url=canonical_listing(url)
    for page_number in range(1,max_pages+1):
        if page_url in visited: raise ValueError('Schleife in BW-Pagination')
        visited.add(page_url)
        response=session.get(page_url,timeout=35); response.raise_for_status()
        if listing_identity(response.url)!=listing_identity(url): raise ValueError('Weiterleitung auf fremdes Gericht')
        page,nxt,meta=parse_listing_html(response.text,response.url,with_meta=True)
        if meta['current_page']!=page_number or (total is not None and (total!=meta['total'] or last!=meta['last_page'])):
            raise ValueError('BW-Pagination änderte sich während des Abrufs')
        total=meta['total'];last=meta['last_page'];raw_ids.update(meta['raw_ids'])
        for record in page:
            key=record['source_external_id']
            if key in seen:
                if seen[key]!=record: raise ValueError('Widersprüchliches BW-Objekt auf Folgeseite')
                continue
            seen[key]=record; recs.append(record)
        if not nxt:
            if len(raw_ids)!=total:raise ValueError('BW-Objektliste nicht vollständig gelesen')
            return recs
        page_url=nxt
    raise ValueError('BW-Pagination nicht vollständig gelesen')

def preserve_record_ids(records,previous,checked_sources=None):
    """Never match a legacy record by its possibly incorrect date/value."""
    def identity(record):
        try:
            case=case_identity(record.get('file_number') or '')
            listing=listing_identity(record.get('source_listing_url') or record.get('source_url'))
            # The canonical listing ID is the court identity, verified against
            # props.court.id above. Old whole-page extraction also truncated
            # some court labels; do not infer identity from those labels.
            return (listing,case) if listing else None
        except ValueError:return None
    prior=[r for r in previous if r.get('source_adapter')==ADAPTER and r.get('state_code')=='bw']
    old_keys=defaultdict(list); new_keys=defaultdict(list); by_external=defaultdict(list)
    for r in prior:
        if identity(r):old_keys[identity(r)].append(r)
        if r.get('source_external_id'):by_external[str(r['source_external_id'])].append(r)
    for r in records:new_keys[identity(r)].append(r)
    result=[]; diagnostics=[]; reused=set(); identity_conflicts=set()
    for r in records:
        key=identity(r); same=by_external.get(r['source_external_id'],[]); old=None
        if same and (len(same)!=1 or identity(same[0])!=key):
            # Reused source IDs are not permission to attach a different
            # proceeding to an existing local ID. Isolate this conflict while
            # allowing unrelated objects to refresh normally.
            identity_conflicts.update(x['id'] for x in same)
            diagnostics.append({'source_external_id':r['source_external_id'],'reason':'source_external_identity_conflict'})
            continue
        if len(same)==1 and identity(same[0])==key:old=same[0]
        elif not same and len(old_keys.get(key,[]))==1 and len(new_keys[key])==1:old=old_keys[key][0]
        elif same or old_keys.get(key):diagnostics.append({'source_external_id':r['source_external_id'],'reason':'legacy_identity_ambiguous'})
        if old:
            # Preserve the old opaque ID, history and already collected data;
            # the verified object fields take precedence over stale values.
            merged={**old,**r}; merged['id']=old['id']
            if old.get('attachments'):merged['attachments']=old['attachments'];merged['has_report']=bool(old.get('has_report'))
            for obsolete in ('source_review_previous','source_data_error'):
                merged.pop(obsolete,None)
            r=merged;reused.add(old['id'])
        r['source_data_status']='verified_listing'
        result.append(r)
    checked={listing_identity(u) for u in checked_sources or []}
    for old in prior:
        if old['id'] in reused:continue
        listing=listing_identity(old.get('source_listing_url') or old.get('source_url'))
        if listing not in checked and old['id'] not in identity_conflicts:
            # collect.py may already have replaced the BW state before this
            # step. Carry failed/unread court sources from --previous intact.
            result.append(dict(old));continue
        current=identity(old)
        reason=('source_external_identity_conflict' if old['id'] in identity_conflicts else
          'legacy_identity_ambiguous' if new_keys.get(current) else 'legacy_identity_not_confirmed')
        # Preserve history and the opaque ID, but do not publish formerly
        # mixed values as current facts. No cancellation is invented.
        clear={key:None for key in ('market_value','auction_date','address','postcode','city','district',
          'property_type','property_type_raw','description','gutachten_url','expose_url','detail_description',
          'creditor_info','smart_analysis','dossier_completeness','lat','lng')}
        clear.update({'market_values':[],'attachments':[],'detail_attachments':[],'has_report':False,'position_precision':'unknown',
          'source_report_available':None,'external_report_url':None,'external_report_access':'unknown',
          'source_document_check_status':'unknown','source_document_checked_at':None})
        public_fields=('market_value','market_values','auction_date','address','postcode','city','district','property_type','property_type_raw','description')
        previous_fields={key:(old.get('source_review_previous') or old).get(key) for key in public_fields}
        pending={**old,**clear,'source_data_status':'unverified','source_data_error':reason,
          'source_review_previous':previous_fields}
        result.append(pending);diagnostics.append({'id':old['id'],'reason':reason})
    return result,diagnostics

def write_payload(path,records,meta,announce=True):
    # Distinct pool objects may share a case and date (separate lots). Do not
    # run the generic court/case/date deduplicator over these object records.
    ids=[r['id'] for r in records]
    if len(ids)!=len(set(ids)):raise ValueError('Doppelte BW-Objekt-ID nach Identitätsabgleich')
    records.sort(key=lambda r:(r.get('auction_date') or '9999',r.get('court') or '',r.get('file_number') or '',r.get('source_external_id') or ''))
    payload={'meta':{'source':DIRECTORY,'state_code':'bw','source_parser_version':PARSER_VERSION,
      'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'count':len(records),**meta},'results':records}
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
    if announce:print(json.dumps(payload['meta'],ensure_ascii=False))

DOCUMENT_FIELDS=('source_report_available','external_report_url','external_report_access',
  'source_document_check_status','source_document_checked_at','source_document_attempted_at','source_document_error')

def parse_document_metadata(html,record):
    """Read source availability only; this does not import/unlock a document."""
    soup=BeautifulSoup(html,'html.parser');node=soup.find(id='app')
    if node is None or not node.get('data-page'):raise ValueError('BW-Dokumentmetadaten fehlen')
    item=(json.loads(node['data-page']).get('props') or {}).get('auction') or {}
    if str(item.get('id'))!=str(record.get('source_external_id')):
        raise ValueError('BW-Dokumentseite gehört zu anderem Objekt')
    if str((item.get('court') or {}).get('id'))!=listing_identity(record.get('source_listing_url')):
        raise ValueError('BW-Dokumentseite gehört zu anderem Gericht')
    if case_identity(item.get('fileNumber') or '')!=case_identity(record.get('file_number') or ''):
        raise ValueError('BW-Dokumentseite gehört zu anderem Verfahren')
    appraisal=item.get('appraisal') or {}
    available=appraisal.get('exists') if type(appraisal.get('exists')) is bool else None
    result={'source_report_available':available,'external_report_url':None,'external_report_access':'unknown'}
    if available is True:
        expected=record['source_url'].rstrip('/')+'/gutachten'
        links={urljoin(record['source_url'],a['href']) for a in soup.find_all('a',href=True)}
        # Never construct a download route that was not present in the source.
        if expected in links:result['external_report_url']=expected
        if appraisal.get('paid') is True:result['external_report_access']='paid'
        elif appraisal.get('paid') is False and appraisal.get('guests')=='yes':result['external_report_access']='free'
        elif appraisal.get('paid') is False and appraisal.get('guests')=='no':result['external_report_access']='login'
    return result

def parsed_timestamp(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError,TypeError):return None

def select_document_checks(records,limit=20,now=None):
    now=now or datetime.now(timezone.utc);due=[]
    for record in records:
        if record.get('source_data_status')!='verified_listing' or record.get('cancelled'):continue
        if str(record.get('auction_date') or '')[:10]<now.date().isoformat():continue
        attempt=parsed_timestamp(record.get('source_document_attempted_at'))
        success=parsed_timestamp(record.get('source_document_checked_at'))
        if record.get('source_document_check_status')=='error' and attempt and attempt>now-timedelta(hours=6):continue
        if record.get('source_document_check_status')=='checked' and success and success>now-timedelta(days=7):continue
        due.append(record)
    epoch=datetime(1970,1,1,tzinfo=timezone.utc)
    due.sort(key=lambda r:(parsed_timestamp(r.get('source_document_attempted_at')) or epoch,str(r.get('source_external_id') or '')))
    return due[:max(0,limit)]

def refresh_document_metadata(session,records,limit=20,pause=.35,on_progress=None,max_seconds=60):
    selected=select_document_checks(records,limit);errors=[];ok=0
    started=time.monotonic();attempted=0
    for i,record in enumerate(selected):
        if time.monotonic()-started>=max_seconds:break
        attempted+=1
        record['source_document_attempted_at']=datetime.now(timezone.utc).isoformat(timespec='seconds')
        try:
            response=session.get(record['source_url'],timeout=(6,12));response.raise_for_status()
            p=urlparse(response.url);expected=urlparse(record['source_url'])
            if p.scheme!='https' or p.hostname not in POOL_HOSTS or p.path!=expected.path:
                raise ValueError('BW-Dokumentseite wurde unerwartet weitergeleitet')
            record.update(parse_document_metadata(response.text,record))
            record['source_document_checked_at']=record['source_document_attempted_at']
            record['source_document_check_status']='checked';record['source_document_error']=None;ok+=1
        except Exception as exc:
            # Keep a previous positive observation rather than invent absence.
            record['source_document_check_status']='error';record['source_document_error']=str(exc)
            errors.append({'id':record['id'],'error':str(exc)})
        if on_progress:on_progress({'selected':len(selected),'attempted':attempted,'checked':ok,'errors':errors[:]})
        if i+1<len(selected):time.sleep(max(0,pause))
    return {'selected':len(selected),'attempted':attempted,'checked':ok,'errors':errors}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='data/supplemental_bw.json')
    ap.add_argument('--max-courts',type=int,default=0)
    ap.add_argument('--pause',type=float,default=.04)
    ap.add_argument('--previous',help='Vorheriger Snapshot zum eindeutigen Erhalt bestehender Objekt-IDs')
    ap.add_argument('--detail-limit',type=int,default=20,help='Maximale Zahl reiner Gutachten-Metadatenprüfungen; keine Downloads')
    a=ap.parse_args()
    previous_path=Path(a.previous) if a.previous else Path(a.output) if Path(a.output).exists() else None
    previous=json.loads(previous_path.read_text(encoding='utf-8')).get('results',[]) if previous_path else []
    prior_bw=[dict(r) for r in previous if r.get('state_code')=='bw' and r.get('source_adapter')==ADAPTER]
    # collect.py can already have replaced the BW state. A discovery failure
    # or outer timeout must still leave the old supplement available to merge.
    write_payload(a.output,prior_bw,{'source_scan_status':'incomplete','checked_sources':[],
      'errors':[{'error':'Gerichtsübersichten noch nicht vollständig geprüft'}]},announce=False)
    s=requests.Session()
    s.headers.update({'User-Agent':'ZVGProOfficialSourceCollector/1.4 (+court-linked public auction listings; low-rate fetch)','Accept-Language':'de-DE,de;q=0.9'})
    pages=discover_court_pages(s)
    if not pages:raise ValueError('Keine BW-Gerichtsübersichten eindeutig erkannt')
    if a.max_courts>0: pages=pages[:a.max_courts]
    recs=[]; errors=[]; courts_ok=0;checked=[]
    for u in pages:
        try:
            rr=parse_court_page(s,u)
            courts_ok+=1; recs.extend(rr);checked.append(canonical_listing(u))
        except Exception as e:
            if len(errors)<30: errors.append({'url':u,'error':str(e)})
        time.sleep(max(0,a.pause))
    recs,identity_errors=preserve_record_ids(recs,previous,checked)
    metadata={
      'source_scan_status':'complete' if not errors else 'partial',
      'court_pages_discovered':len(pages),
      'courts_with_records':courts_ok,
      'errors':errors,
      'checked_sources':checked,
      'identity_errors':identity_errors,
      'court_reference':COURT_REFERENCE,
      'note':'Baden-Württembergische Gerichte verweisen für Online-Termine auf immobilienpool.de; gespeichert werden nur minimale Falldaten.'
    }
    # Save verified listing corrections before optional, bounded metadata I/O.
    # Every completed document check is checkpointed, so the existing outer
    # workflow timeout cannot discard progress or starve later objects.
    write_payload(a.output,recs,metadata,announce=False)
    def checkpoint(progress):
        metadata['document_metadata']=progress
        write_payload(a.output,recs,metadata,announce=False)
    metadata['document_metadata']=refresh_document_metadata(s,recs,a.detail_limit,on_progress=checkpoint)
    write_payload(a.output,recs,metadata)

if __name__=='__main__':
    main()

