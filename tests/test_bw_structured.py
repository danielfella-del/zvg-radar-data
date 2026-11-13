#!/usr/bin/env python3
"""Synthetic, network-free regressions for BW object and document provenance."""
import copy
import html
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'collector'))
import collect_bw_official as bw
import merge_supplemental as merge

URL='https://immobilienpool.de/amtsgerichte/heidenheim.999/zwangsversteigerungen'
NOW=datetime(2026,11,13,12,tzinfo=timezone.utc)

def item(eid='100',case='2 K 24/25',date='20.11.2030, 08:30 Uhr',value='169.000,00 €',title='Einfamilienhaus'):
    return {'id':int(eid),'title':title,'link':'/zwangsversteigerungen/objekt.'+eid,
      'address':'Teststraße 1, 89537 Beispielort','city':'89537 Beispielort',
      'price':value,'priceType':'Verkehrswert','priceFrom':False,'otherPrices':False,
      'auctionDate':date,'cancelled':False,'facts':[{'key':'Amtsgericht Heidenheim:','value':case}]}

def document(items,*,current=1,last=1,total=None,nxt=None,court_id=999,court='Amtsgericht Heidenheim'):
    payload={'props':{'court':{'id':court_id,'title':court},'auctions':{'data':items,
      'meta':{'current_page':current,'last_page':last,'total':len(items) if total is None else total},'links':{'next':nxt}}}}
    return '<p>31.12.2099 9.999.999,00 € Nachbarobjekt</p><div id="app" data-page="'+html.escape(json.dumps(payload),quote=True)+'"></div>'

def record(eid='100',**overrides):
    r=bw.parse_listing_html(document([item(eid)]),URL)[0][0]
    r['source_data_status']='verified_listing';r.update(overrides);return r

def legacy(**overrides):
    r=record();r.update({'id':'bw-official-legacy','source_url':URL,'auction_date':'2030-01-01T00:00','market_value':999999})
    for key in ('source_external_id','source_listing_url','source_parser_version','source_data_status'):r.pop(key,None)
    r.update(overrides);return r

def detail(r,*,exists=True,paid=False,guests='yes',link=True,eid=None,court_id=999,case=None):
    data={'props':{'auction':{'id':int(eid or r['source_external_id']),'court':{'id':court_id},
      'fileNumber':case or r['file_number'],'appraisal':{'exists':exists,'paid':paid,'guests':guests}}}}
    source='<div id="app" data-page="'+html.escape(json.dumps(data),quote=True)+'"></div>'
    return source+('<a href="'+r['source_url']+'/gutachten">Gutachten</a>' if link else '')

class Response:
    def __init__(self,url,text,error=None):self.url=url;self.text=text;self.error=error
    def raise_for_status(self):
        if self.error:raise self.error
class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append(url)
        if not self.responses:raise AssertionError('Unexpected network request')
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value

class ListingTests(unittest.TestCase):
    def test_adjacent_objects_never_mix(self):
        rows,_=bw.parse_listing_html(document([item(),item('200','3 K 11/26','05.12.2030, 11:00 Uhr','26.000,00 €','Eigentumswohnung')]),URL)
        self.assertEqual([(r['file_number'],r['auction_date'],r['market_value']) for r in rows],
          [('2 K 24/25','2030-11-20T08:30',169000),('3 K 11/26','2030-12-05T11:00',26000)])
        self.assertEqual(rows[1]['property_type'],'Eigentumswohnung')
    def test_wrong_court_id(self):
        with self.assertRaises(ValueError):bw.parse_listing_html(document([item()],court_id=888),URL)
    def test_wrong_fact_court(self):
        x=item();x['facts'][0]['key']='Amtsgericht Ulm:'
        with self.assertRaises(ValueError):bw.parse_listing_html(document([x]),URL)
    def test_case_tokens_and_year(self):
        self.assertEqual(bw.case_identity('0002 K 0024/2025'),bw.case_identity('2 K 24/25'))
        self.assertNotEqual(bw.case_identity('3 2 K 28/25'),bw.case_identity('2 K 28/25'))
        self.assertNotEqual(bw.case_identity('3 2 K 28/25'),bw.case_identity('32 K 28/25'))
    def test_missing_structured_data(self):
        with self.assertRaises(ValueError):bw.parse_listing_html('<p>2 K 4/25 169.000 €</p>',URL)
    def test_invalid_calendar_date(self):
        with self.assertRaises(ValueError):bw.parse_listing_html(document([item(date='31.02.2030, 08:30 Uhr')]),URL)
    def test_invalid_case(self):
        with self.assertRaises(ValueError):bw.parse_listing_html(document([item(case='Aktenzeichen unbekannt')]),URL)
    def test_ambiguous_price_is_not_guessed(self):
        for key in ('otherPrices','priceFrom'):
            x=item();x[key]=True
            self.assertIsNone(bw.parse_listing_html(document([x]),URL)[0][0]['market_value'])
    def test_missing_address_is_not_court_address(self):
        x=item();x['city']='';x['address']='';r=bw.parse_listing_html(document([x]),URL)[0][0]
        self.assertIsNone(r['city']);self.assertIsNone(r['postcode']);self.assertIsNone(r['address'])
    def test_foreign_object_url(self):
        for bad in ('https://evil.test/zwangsversteigerungen/o.100','/zwangsversteigerungen/o.999','https://user@immobilienpool.de/zwangsversteigerungen/o.100'):
            x=item();x['link']=bad
            with self.assertRaises(ValueError):bw.parse_listing_html(document([x]),URL)
    def test_empty_verified_list(self):
        self.assertEqual(bw.parse_court_page(Session([Response(URL,document([]))]),URL),[])
    def test_full_pagination(self):
        next_url=URL+'?page=2'
        s=Session([Response(URL,document([item()],last=2,total=2,nxt=next_url)),Response(next_url,document([item('200')],current=2,last=2,total=2))])
        self.assertEqual(len(bw.parse_court_page(s,URL)),2)
    def test_missing_next_preserves_source_as_failed(self):
        with self.assertRaises(ValueError):bw.parse_court_page(Session([Response(URL,document([item()],last=2,total=2))]),URL)
    def test_missing_total_item(self):
        with self.assertRaises(ValueError):bw.parse_court_page(Session([Response(URL,document([item()],total=2))]),URL)
    def test_duplicate_ids_do_not_satisfy_total(self):
        next_url=URL+'?page=2';s=Session([Response(URL,document([item()],last=2,total=2,nxt=next_url)),Response(next_url,document([item()],current=2,last=2,total=2))])
        with self.assertRaises(ValueError):bw.parse_court_page(s,URL)
    def test_partial_failure_discards_partial_refresh(self):
        s=Session([Response(URL,document([item()],last=2,total=2,nxt=URL+'?page=2')),RuntimeError('timeout')])
        with self.assertRaises(RuntimeError):bw.parse_court_page(s,URL)
    def test_pagination_never_changes_court(self):
        with self.assertRaises(ValueError):bw.parse_listing_html(document([item()],last=2,total=2,nxt='/amtsgerichte/ulm.888/zwangsversteigerungen?page=2'),URL)
    def test_raw_counts_before_date_filter(self):
        with patch.object(bw,'futureish',return_value=False):
            self.assertEqual(bw.parse_court_page(Session([Response(URL,document([item()]))]),URL),[])

class IdentityTests(unittest.TestCase):
    def test_discovery_failure_still_writes_previous_bw_for_merge(self):
        old=legacy();other={**old,'state_code':'he','id':'he-123','source_adapter':None}
        with tempfile.TemporaryDirectory() as td:
            td=pathlib.Path(td);before=td/'before.json';output=td/'supplement.json'
            before.write_text(json.dumps({'results':[old,other]}))
            with patch.object(sys,'argv',['collect_bw_official.py','--previous',str(before),'--output',str(output)]),patch.object(bw,'discover_court_pages',side_effect=RuntimeError('directory unavailable')):
                with self.assertRaises(RuntimeError):bw.main()
            payload=json.loads(output.read_text())
            self.assertEqual(payload['results'],[old]);self.assertEqual(payload['meta']['source_scan_status'],'incomplete')
    def test_standalone_failure_reuses_existing_supplement(self):
        old=legacy()
        with tempfile.TemporaryDirectory() as td:
            output=pathlib.Path(td)/'supplement.json';output.write_text(json.dumps({'results':[old]}))
            with patch.object(sys,'argv',['collect_bw_official.py','--output',str(output)]),patch.object(bw,'discover_court_pages',return_value=[]):
                with self.assertRaises(ValueError):bw.main()
            self.assertEqual(json.loads(output.read_text())['results'],[old])
    def test_changed_date_and_value_keep_id(self):
        result,diag=bw.preserve_record_ids([record()],[legacy()], [URL])
        self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],'bw-official-legacy');self.assertEqual(result[0]['market_value'],169000)
        self.assertEqual(result[0]['auction_date'],'2030-11-20T08:30');self.assertEqual(diag,[])
    def test_truncated_court_label_uses_verified_listing_id(self):
        result,_=bw.preserve_record_ids([record()],[legacy(court='Heid')],[URL]);self.assertEqual(result[0]['id'],'bw-official-legacy');self.assertEqual(result[0]['court'],'Heidenheim')
    def test_same_case_other_listing_does_not_reuse(self):
        other='https://immobilienpool.de/amtsgerichte/ulm.888/zwangsversteigerungen'
        result,_=bw.preserve_record_ids([record()],[legacy(source_url=other)],[URL,other])
        self.assertNotEqual(result[0]['id'],'bw-official-legacy');self.assertEqual(result[1]['source_data_status'],'unverified')
    def test_page_two_same_court_identity(self):
        result,_=bw.preserve_record_ids([record()],[legacy(source_url=URL+'?page=2')],[URL]);self.assertEqual(result[0]['id'],'bw-official-legacy')
    def test_case_parts_must_not_be_truncated(self):
        result,_=bw.preserve_record_ids([record(file_number='3 2 K 24/25')],[legacy()],[URL]);self.assertEqual(len(result),2);self.assertEqual(result[1]['source_data_status'],'unverified')
    def test_changed_identity_with_same_external_id_is_isolated(self):
        old=record(attachments=[{'url':'protected://old'}],has_report=True)
        unrelated=record('200',file_number='3 K 8/26',market_value=200000)
        changed=record(file_number='9 K 99/26')
        result,diag=bw.preserve_record_ids([changed,unrelated],[old],[URL])
        self.assertEqual(len(result),2);self.assertEqual(len({r['id'] for r in result}),2)
        q=next(r for r in result if r['id']==old['id'])
        self.assertEqual(q['source_data_status'],'unverified');self.assertEqual(q['source_data_error'],'source_external_identity_conflict')
        self.assertIsNone(q['auction_date']);self.assertEqual(q['attachments'],[]);self.assertFalse(q['has_report'])
        self.assertEqual(next(r for r in result if r['id']==unrelated['id'])['market_value'],200000)
        self.assertTrue(any(d['reason']=='source_external_identity_conflict' for d in diag))
        with tempfile.TemporaryDirectory() as td:
            bw.write_payload(pathlib.Path(td)/'out.json',result,{},announce=False)
    def test_changed_court_for_external_id_quarantines_even_old_unread_source(self):
        old=record();other='https://immobilienpool.de/amtsgerichte/ulm.888/zwangsversteigerungen'
        changed=record(source_listing_url=other,court='Ulm')
        result,_=bw.preserve_record_ids([changed],[old],[other])
        self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],old['id']);self.assertEqual(result[0]['source_data_status'],'unverified')
    def test_ambiguous_prior_external_identity_keeps_review_ids_separate(self):
        first=record();second=record(id='bw-official-other-history')
        result,_=bw.preserve_record_ids([record()],[first,second],[URL])
        self.assertEqual({r['id'] for r in result},{first['id'],second['id']})
        self.assertTrue(all(r['source_data_status']=='unverified' for r in result))
        self.assertEqual(len({merge.key(r) for r in result}),2)
    def test_two_lots_quarantine_legacy(self):
        result,_=bw.preserve_record_ids([record(),record('200')],[legacy()],[URL])
        self.assertEqual(len(result),3);q=result[-1]
        self.assertEqual(q['id'],'bw-official-legacy');self.assertIsNone(q['market_value']);self.assertIsNone(q['auction_date']);self.assertFalse(q['cancelled'])
        self.assertEqual(q['source_review_previous']['market_value'],999999)
    def test_failed_source_retained_unchanged(self):
        old=legacy();result,_=bw.preserve_record_ids([], [old],[]);self.assertEqual(result,[old])
    def test_missing_object_is_unknown_not_cancelled(self):
        result,_=bw.preserve_record_ids([], [legacy()], [URL]);self.assertFalse(result[0]['cancelled']);self.assertIsNone(result[0]['auction_date'])
    def test_no_protected_data_in_review_backup(self):
        old=legacy(attachments=[{'url':'protected://doc'}],detail_description='secret detail',creditor_info='secret creditor',smart_analysis={'secret':True})
        result,_=bw.preserve_record_ids([], [old],[URL]);q=result[0]
        self.assertNotIn('protected',json.dumps(q));self.assertNotIn('secret',json.dumps(q))
        self.assertNotIn('attachments',q['source_review_previous']);self.assertEqual(q['attachments'],[])
    def test_recovered_legacy_clears_review_fields(self):
        pending,_=bw.preserve_record_ids([], [legacy()],[URL]);result,_=bw.preserve_record_ids([record()],pending,[URL]);self.assertEqual(result[0]['id'],'bw-official-legacy');self.assertNotIn('source_review_previous',result[0])
    def test_write_keeps_distinct_lots(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/'out.json';bw.write_payload(path,[record(),record('200')],{},announce=False)
            self.assertEqual(len(json.loads(path.read_text())['results']),2)
    def test_merge_keeps_pool_lots_and_portal_separate(self):
        a,b=record(),record('200');portal={**a,'id':'bw-1','source_adapter':None};self.assertEqual(len({merge.key(r) for r in [a,b,portal]}),3)
    def test_merge_updates_same_opaque_id_without_overwriting_other_state(self):
        old=legacy();fresh,_=bw.preserve_record_ids([record()],[old],[URL]);other={**old,'id':'he-123','state_code':'he','source_adapter':None}
        with tempfile.TemporaryDirectory() as td:
            td=pathlib.Path(td);base=td/'base.json';supp=td/'supp.json'
            base.write_text(json.dumps({'results':[old,other],'meta':{}}));supp.write_text(json.dumps({'meta':{'state_code':'bw','source_parser_version':2},'results':fresh}))
            subprocess.run([sys.executable,str(ROOT/'collector/merge_supplemental.py'),'--data',str(base),'--supplement',str(supp)],check=True,capture_output=True)
            rows=json.loads(base.read_text())['results'];self.assertEqual(len(rows),2)
            self.assertEqual(next(r for r in rows if r['id']==old['id'])['market_value'],169000);self.assertEqual(next(r for r in rows if r['id']==other['id']),other)

class DocumentTests(unittest.TestCase):
    def test_source_report_is_not_imported_report(self):
        r=record();meta=bw.parse_document_metadata(detail(r),r);self.assertTrue(meta['source_report_available']);self.assertEqual(meta['external_report_access'],'free');self.assertFalse(r['has_report']);self.assertEqual(r['attachments'],[])
    def test_missing_anchor_does_not_invent_url(self):
        r=record();self.assertIsNone(bw.parse_document_metadata(detail(r,link=False),r)['external_report_url'])
    def test_paid_and_login_are_distinct(self):
        r=record();self.assertEqual(bw.parse_document_metadata(detail(r,paid=True),r)['external_report_access'],'paid');self.assertEqual(bw.parse_document_metadata(detail(r,guests='no'),r)['external_report_access'],'login')
    def test_explicit_absent_and_unknown(self):
        r=record();self.assertFalse(bw.parse_document_metadata(detail(r,exists=False),r)['source_report_available']);self.assertIsNone(bw.parse_document_metadata(detail(r,exists=None),r)['source_report_available'])
    def test_wrong_id_court_or_case_rejected(self):
        r=record()
        for opts in ({'eid':'999'},{'court_id':888},{'case':'3 K 24/25'}):
            with self.assertRaises(ValueError):bw.parse_document_metadata(detail(r,**opts),r)
    def test_fair_queue_and_backoff(self):
        fresh=record('100');old=record('200',source_document_attempted_at=(NOW-timedelta(days=8)).isoformat(),source_document_checked_at=(NOW-timedelta(days=8)).isoformat(),source_document_check_status='checked')
        error=record('300',source_document_attempted_at=(NOW-timedelta(hours=1)).isoformat(),source_document_check_status='error')
        self.assertEqual([r['source_external_id'] for r in bw.select_document_checks([old,error,fresh],2,NOW)],['100','200'])
    def test_recent_success_not_rechecked(self):
        r=record(source_document_checked_at=(NOW-timedelta(days=1)).isoformat(),source_document_check_status='checked');self.assertEqual(bw.select_document_checks([r],20,NOW),[])
    def test_source_error_preserves_prior_positive_observation(self):
        r=record(source_report_available=True,external_report_url='https://immobilienpool.de/zwangsversteigerungen/objekt.100/gutachten');s=Session([RuntimeError('503')]);saved=[]
        with patch.object(bw,'select_document_checks',return_value=[r]):bw.refresh_document_metadata(s,[r],pause=0,on_progress=lambda x:saved.append(x))
        self.assertTrue(r['source_report_available']);self.assertEqual(r['source_document_check_status'],'error');self.assertEqual(len(saved),1)
    def test_success_is_checkpointed(self):
        r=record();s=Session([Response(r['source_url'],detail(r))]);saved=[]
        with patch.object(bw,'select_document_checks',return_value=[r]):bw.refresh_document_metadata(s,[r],pause=0,on_progress=lambda x:saved.append(x))
        self.assertEqual(r['source_document_check_status'],'checked');self.assertEqual(saved[0]['checked'],1);self.assertFalse(r['has_report'])
    def test_time_budget_keeps_later_work_pending(self):
        r=record();s=Session([])
        with patch.object(bw,'select_document_checks',return_value=[r]):meta=bw.refresh_document_metadata(s,[r],max_seconds=0)
        self.assertEqual(meta['attempted'],0);self.assertNotIn('source_document_attempted_at',r)

if __name__=='__main__':
    # Tests cannot accidentally perform production I/O.
    with patch('requests.sessions.Session.request',side_effect=AssertionError('Network disabled')):
        unittest.main()
