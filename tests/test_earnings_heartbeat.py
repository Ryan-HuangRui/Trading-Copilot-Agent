import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'script'))
from earnings_heartbeat import main, render, snapshot
from earnings_state import EarningsState
from earnings_continuation import ContinuationLedger
from earnings_common import sha256_file


class HeartbeatTests(unittest.TestCase):
    def data(self,state='paused_quota'):
        return {'round':{'round_id':'round-test','state':state},'window':{'start':'2026-09-01','end_exclusive':'2026-11-01'},
            'live_worker_pids':[],'expected_companies':20,'expected_industries':5,'company_reports':[],
            'industry_reports':[],'publications':[],'pending_publications':[],'without_company_report':['LEN'], 'source_failures':[]}

    def test_paused_quota_is_not_reported_as_running(self):
        body=render(self.data(),'2026-09-24 16:00')
        self.assertIn('模型额度暂停',body);self.assertIn('工作进程 0 个',body)
        self.assertIn('不反复触发研究',body)

    def test_snapshot_does_not_mutate_research_databases(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);state=EarningsState(root/'runtime/earnings/state.sqlite');state.close()
            ledger=ContinuationLedger(root);ledger.db.execute("INSERT INTO rounds(round_id,revision,batch_date,cutoff,state,created_at,updated_at) VALUES('r',1,'2026-09-24','2026-09-24T00:00:00Z','paused_quota','now','now')");ledger.db.commit();ledger.close()
            p=root/'universe.json';p.write_text(json.dumps({'industries':[{'industry_id':'homebuilding','issuers':[{'symbol':'LEN'}]}]}))
            config={'paths':{'state':'runtime/earnings/state.sqlite','universe':'universe.json'},'disclosure_window':self.data()['window']}
            paths=[root/'runtime/earnings/state.sqlite',root/'runtime/earnings/continuation.sqlite'];before=[sha256_file(p) for p in paths]
            data=snapshot(root,config,['homebuilding'])
            self.assertEqual(data['round']['state'],'paused_quota');self.assertEqual(data['without_company_report'],['LEN'])
            self.assertEqual([sha256_file(p) for p in paths],before)

    def test_same_hour_is_sent_once_and_final_report_stops_repeated_messages(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);runtime=root/'runtime/earnings';runtime.mkdir(parents=True)
            binary=runtime/'sender';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o700)
            deployment=runtime/'deployment.json';deployment.write_text(json.dumps({'schema_version':1,'verified_repo':str(root.resolve()),
                'project':'test','session':'session','cc_connect_bin':str(binary),'verified_at':'now','verified_from_cron_id':'test','delivery_enabled':True}))
            args=['heartbeat','--repo-root',str(root),'--config','config.json','--deployment',str(deployment),'--industry','homebuilding','--execute']
            with patch('sys.argv',args),patch('earnings_heartbeat.load_config',return_value=({},'hash')), \
                 patch('earnings_heartbeat.snapshot',return_value=self.data('waiting')) as read, \
                 patch('earnings_delivery.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'ok',b'')) as send, \
                 contextlib.redirect_stdout(io.StringIO()):
                main();main()
            self.assertEqual(read.call_count,1);self.assertEqual(send.call_count,1)
            self.assertTrue((runtime/'heartbeat/finished.json').exists())


if __name__=='__main__':unittest.main()
