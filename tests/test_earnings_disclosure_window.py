import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
from earnings_state import EarningsState
from earnings_daily import DailyLedger, industry_work, round_progress, run_publication_work
from earnings_common import sha256_file
from earnings_period_review import _available_mapped_quarters, _period_member_audit

WINDOW = {'start': '2026-09-01', 'end_exclusive': '2026-11-01'}
CUTOFF = '2026-09-24T03:00:00Z'

class DisclosureWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name)
        self.state = EarningsState(self.root / 'runtime/earnings/state.sqlite', disclosure_window=WINDOW)
        self.state.upsert_issuer(issuer_id='issuer', cik='123', symbol='TEST', name='Test', identity_status='verified_sec')

    def tearDown(self):
        self.state.close(); self.temp.cleanup()

    def event(self, name, published, end='2026-06-30', start='2026-04-01', form='10-Q'):
        self.doc(name, name, published, end, start, form)
        self.state.refresh_event(name, 'issuer', 'earnings', start, end)
        return self.state.enqueue_task(task_type='company', subject_id=name, period_start=start, period_end=end,
            input_hash=name, method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')[0]

    def doc(self, name, event, published, end='2026-06-30', start='2026-04-01', form='10-Q'):
        path=self.root / ('raw_data/earnings/'+name+'.html');path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original')
        self.state.register_document(dict(document_id=name, issuer_id='issuer', event_id=event, form=form,
            source_type='sec_filing', source_url='https://example.invalid/filing', provider='sec', backend='test',
            reporting_start=start, reporting_end=end, published_at=published, accepted_at=published,
            fetched_at='2026-09-24T02:00:00Z', public_time_precision='second', original_path=str(path.relative_to(self.root)),
            content_sha256=sha256_file(path),source_mode='live',metadata_json='{}'))

    def artifact(self, event, task, kind='company', window=None):
        path=self.root / ('report/earnings/'+event+'.json');path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'report_type':kind,'cutoff':CUTOFF,'scope':{'disclosure_window':window}}))
        self.state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (event,task,kind,event,'2026-04-01','2026-06-30',str(path.relative_to(self.root)),sha256_file(path),'hash','live','partial',CUTOFF))
        return str(path.relative_to(self.root))

    def test_old_latest_is_not_claimed_but_september_release_of_june_period_is(self):
        self.event('old','2026-06-20T10:00:00Z',end='2026-05-31')
        new=self.event('new','2026-09-05T10:00:00Z')
        self.event('future','2026-10-05T10:00:00Z',end='2026-09-30',start='2026-07-01')
        claimed=self.state.claim_tasks(owner='test',limit=10,lease_seconds=60,task_type='company',company_tier='current',cutoff=CUTOFF)
        self.assertEqual([r['task_id'] for r in claimed],[new])

    def test_amendment_and_refetch_do_not_refresh_publication_date(self):
        self.event('old','2026-06-20T10:00:00Z')
        self.doc('amended','old','2026-09-05T10:00:00Z',form='10-Q/A')
        self.assertFalse(self.state.event_in_disclosure_window('old',CUTOFF))
        self.event('amendment-only','2026-09-10T10:00:00Z',form='10-Q/A')
        self.assertFalse(self.state.event_in_disclosure_window('amendment-only',CUTOFF))

    def test_unresolved_current_8k_is_not_a_quarterly_earnings_release(self):
        self.event('board-change','2026-09-20T10:00:00Z',end=None,start=None,form='8-K')
        self.state.db.execute("UPDATE earnings_events SET event_kind='unresolved_earnings' WHERE event_id='board-change'")
        self.assertFalse(self.state.event_in_disclosure_window('board-change',CUTOFF))

    def test_mixed_unit_table_uses_explicit_row_units_and_rejects_conflicts(self):
        from earnings_publication import _claims
        text = "| metric | value (USD million or %) |\n|---|---|\n| margin (%) | 34.78 |\n| revenue (USD million) | 55 |"
        claims = _claims(text)
        self.assertEqual([r['unit'] for r in claims if r.get('table')], ['%', 'USD million'])
        conflict = "| metric | value (USD million) |\n|---|---|\n| margin (%) | 34.78 |"
        self.assertIsNone([r for r in _claims(conflict) if r.get('table')][0]['unit'])

    def test_release_period_requires_results_and_completed_quarter(self):
        from earnings_collect import _release_period
        filing = {'form': '8-K', 'filingDate': '2026-09-16'}
        release = b'Lennar Reports Third Quarter 2026 Results. For the quarter ended August 31, 2026.'
        self.assertEqual(_release_period(release, filing, b'Item 2.02 Results of Operations')[0], '2026-08-31')
        self.assertEqual(_release_period(release, filing, b'Item 5.02 Board changes'), (None, None))
        self.assertEqual(_release_period(release.replace(b'Reports', b'Will Release'), filing, b'Item 2.02'), (None, None))
        self.assertEqual(_release_period(release.replace(b'August 31', b'September 30'), filing, b'Item 2.02'), (None, None))

    def test_collection_checkpoint_requires_complete_same_window_and_cutoff(self):
        from earnings_daily import window_collection_complete
        coverage={'policy':'calendar-disclosure-window-v1','complete':True,'window':WINDOW,'checked_through':CUTOFF}
        config={'disclosure_window':WINDOW}
        self.state.set_watermark('sec_disclosure_window','TEST',CUTOFF,'success',json.dumps(coverage))
        self.assertTrue(window_collection_complete(self.state,config,'TEST',CUTOFF))
        self.assertFalse(window_collection_complete(self.state,config,'TEST','2026-09-25T00:00:00Z'))
        self.assertFalse(window_collection_complete(self.state,{'disclosure_window':None},'TEST',CUTOFF))
        self.assertFalse(window_collection_complete(self.state,{'disclosure_window':{'start':'2026-08-01','end_exclusive':'2026-11-01'}},'TEST',CUTOFF))
        self.state.set_watermark('sec_disclosure_window','TEST',CUTOFF,'partial',json.dumps(coverage))
        self.assertFalse(window_collection_complete(self.state,config,'TEST',CUTOFF))

    def test_explicit_three_month_period_and_hash_bound_original_resolution(self):
        from earnings_common import explicit_three_month_period
        from earnings_period_review import resolve_report_period
        content=b'Results for the three months ended August 31, 2026.'
        self.assertEqual(explicit_three_month_period(content,'2026-08-31')[0],'2026-06-01')
        self.assertEqual(explicit_three_month_period(b'three months ended January 31, 2026','2026-01-31')[0],'2025-11-01')
        self.assertIsNone(explicit_three_month_period(b'quarter ended August 31, 2026','2026-08-31'))
        self.assertIsNone(explicit_three_month_period(content+b' Our 13-week quarter','2026-08-31'))
        self.assertIsNone(explicit_three_month_period(content,'2026-08-30'))
        self.event('release','2026-09-16T20:00:00Z',end='2026-08-31',start=None,form='8-K')
        path=self.root/'raw_data/earnings/release.html';path.write_bytes(content)
        digest=sha256_file(path)
        self.state.db.execute("UPDATE documents SET content_sha256=? WHERE document_id='release'",(digest,))
        report={'cutoff':CUTOFF,'scope':{'issuer_id':'issuer','event_id':'release','reporting_start':None,'reporting_end':'2026-08-31'},
                'evidence':[{'document_id':'release','document_version':1,'document_hash':digest}]}
        resolved=resolve_report_period(report,root=self.root)
        self.assertEqual(resolved['actual_period'],{'start':'2026-06-01','end':'2026-08-31'})
        self.assertEqual(resolved['research_quarter'],'2026-Q3')
        self.assertEqual(resolved['period_proofs'][0]['document_hash'],digest)
        report['cutoff']='2026-09-15T00:00:00Z'
        with self.assertRaises(ValueError):resolve_report_period(report,root=self.root)
        report['cutoff']=CUTOFF
        path.write_bytes(content+b' mutated')
        with self.assertRaises(ValueError):resolve_report_period(report,root=self.root)

    def test_collection_scans_full_window_and_separates_history_depth_from_backlog(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from earnings_collect import collect_live
        class FakeSec:
            FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
            user_agent = 'test@example.com'; timeout = 1; max_attempts = 1
            def resolve_symbols(self, symbols):
                return {'TEST': {'issuer_id':'issuer', 'cik':'123', 'name':'Test', 'symbol':'TEST'}}
            def submissions(self, cik):
                rows = {k: [] for k in ('accessionNumber','filingDate','reportDate','acceptanceDateTime','form','primaryDocument','primaryDocDescription')}
                for accession, filed, form in [('old','2026-08-01','10-Q'), ('release','2026-09-16','8-K'), ('other','2026-09-17','10-Q'), ('future','2026-10-20','10-Q')]:
                    for key, value in zip(rows, (accession,filed,'2026-08-31',filed+'T12:00:00Z',form,accession+'.htm','')):
                        rows[key].append(value)
                return {'filings':{'recent':rows,'files':[]}}, {'fetched_at':CUTOFF}
            def filing_index(self, **kwargs):
                return {'directory':{'item':[{'name':'ex991.htm','documentType':'EX-99.1'}]}}, {}
            def filing(self, **kwargs):
                name=kwargs['primary_document']
                content = b'Test Reports Third Quarter Results. Three months ended August 31, 2026.' if name=='ex991.htm' else b'Item 2.02 Results of Operations'
                return content, {'fetched_at':CUTOFF}, 'https://www.sec.gov/'+name
            def company_facts(self, cik):
                return {'entityName':'Test','facts':{}}, {'fetched_at':CUTOFF}
        config={'disclosure_window':WINDOW,'profiles':{'daily':{'model':'gpt-5.6-sol','reasoning_effort':'medium'}},
                'budgets':{'max_task_attempts':2,'initialization_lookback_quarters':8}, 'sources':{'sec':{'max_attempts':3}}}
        with patch('earnings_collect.SecClient.from_config',return_value=FakeSec()):
            first=collect_live(self.root,self.state,config,'cfg',['TEST'],datetime(2026,9,24,tzinfo=timezone.utc),1,None,'initialization')
            self.assertFalse(first['initialization_coverage']['TEST']['complete'])
            last=collect_live(self.root,self.state,config,'cfg',['TEST'],datetime(2026,9,24,tzinfo=timezone.utc),1,None,'incremental')
        coverage=last['initialization_coverage']['TEST']
        self.assertTrue(coverage['complete']);self.assertEqual(coverage['discovered_filings'],2)
        self.assertFalse(coverage['historical_depth_required'])
        self.assertEqual({r[0] for r in self.state.db.execute('SELECT item_key FROM source_items')},{'release','other'})
        event=self.state.db.execute("SELECT event_id FROM earnings_events WHERE event_kind='earnings'").fetchone()[0]
        self.assertTrue(self.state.event_in_disclosure_window(event,CUTOFF))
        self.assertEqual(self.state.db.execute("SELECT reporting_start FROM documents WHERE source_type='sec_earnings_exhibit'").fetchone()[0], '2026-06-01')

    def test_window_boundaries_and_actual_acceptance_cutoff(self):
        for name,pub,want in [('before','2026-08-31T23:59:59Z',False),('start','2026-09-01T00:00:00Z',True),
                              ('end','2026-11-01T00:00:00Z',False)]:
            self.event(name,pub);self.assertEqual(self.state.event_in_disclosure_window(name,'2026-12-01T00:00:00Z'),want)
        self.event('late-today','2026-09-24T10:00:00Z')
        self.state.db.execute("UPDATE documents SET published_at='2026-09-24' WHERE event_id='late-today'")
        self.assertFalse(self.state.event_in_disclosure_window('late-today',CUTOFF))

    def test_daily_and_quarterly_inputs_do_not_use_old_company_as_fallback(self):
        task=self.event('old','2026-06-20T10:00:00Z');path=self.artifact('old',task)
        ledger=DailyLedger(self.root)
        universe={'industries':[{'industry_id':'test','issuers':[{'symbol':'TEST'}]}]}
        try:self.assertEqual(industry_work(self.root,self.state,universe,ledger,cutoff=CUTOFF),[])
        finally:ledger.db.close()
        self.assertEqual(_available_mapped_quarters(self.state,CUTOFF),set())
        self.assertEqual(_period_member_audit(self.state,['issuer'],'2026-Q2',cutoff=CUTOFF)[:3],(set(),set(),set()))
        self.assertFalse(self.state.report_in_disclosure_window(self.root,path,CUTOFF))

    def test_historical_industry_report_needs_matching_window(self):
        task=self.event('industry','2026-09-05T10:00:00Z');path=self.artifact('industry',task,kind='synthesis')
        self.assertFalse(self.state.report_in_disclosure_window(self.root,path,CUTOFF))
        payload=json.loads((self.root/path).read_text());payload['scope']['disclosure_window']=WINDOW
        (self.root/path).write_text(json.dumps(payload))
        self.assertTrue(self.state.report_in_disclosure_window(self.root,path,CUTOFF))

    def test_progress_waits_for_disclosure_instead_of_counting_old_latest(self):
        self.event('old','2026-06-20T10:00:00Z')
        p=self.root/'config/universe.json';p.parent.mkdir();p.write_text(json.dumps({'industries':[{'industry_id':'test','issuers':[{'symbol':'TEST'}]}]}))
        config={'disclosure_window':WINDOW,'paths':{'universe':'config/universe.json'},'budgets':{},'quarterly':{},'delivery':{}}
        progress=round_progress(self.root,self.state,config,cutoff=CUTOFF)
        self.assertEqual(progress['pending']['current_company'],0)
        self.assertEqual(progress['pending']['awaiting_window_disclosure'],['TEST'])
        self.assertEqual(progress['actionable_count'],0)
        self.assertEqual(progress['waiting_count'],1)

if __name__=='__main__':unittest.main()
