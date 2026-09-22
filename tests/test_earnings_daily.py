import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'script'))
from earnings_common import atomic_write_json, sha256_file
from earnings_daily import (DailyLedger, _latest_company_publication_heads, _publication_matches_current_head,
    _quarterly_market_readiness, fail_owned_attempt, finalize, notification_material, render_publication_entries, run, run_gap_review_step,
    reconcile_quarterly_publication, run_publication_work, round_progress, season_limit, unresolved_terminal_count)
from earnings_period_review import QuarterlyReviewLedger
from earnings_lark import publication_delivery_route_key
from earnings_state import EarningsState
from earnings_role_runner import run_role


class EarningsDailyTests(unittest.TestCase):
    def test_completed_industry_publication_reconciles_exact_quarterly_revision_without_attempts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            scope = qledger.freeze({'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'},
                {'industry_id': 'managed-care', 'issuers': [], 'key_symbols': []},
                '2026-08-31T00:00:00Z', edition='stage')
            source = root / 'report/earnings/managed-care.json'
            atomic_write_json(source, {'report_id': 'synthesis', 'report_type': 'synthesis'})
            source_sha = sha256_file(source)
            qledger.set_stage(scope['scope_id'], 'synthesis', 'completed',
                              artifact_path=str(source.relative_to(root)), artifact_sha256=source_sha)
            qledger.db.execute("UPDATE quarterly_scopes SET finalization_state='ready_stage_with_gaps' WHERE scope_id=?",
                               (scope['scope_id'],)); qledger.db.commit()
            base = root / 'report/earnings/publications/industry/managed-care/2026-Q2/v1'
            reader = base / 'reader-report.md'; atomic_write_json(base / 'reader-report.html', {'html': True})
            reader.parent.mkdir(parents=True, exist_ok=True); reader.write_text('# reader\n')
            reader_sha = sha256_file(reader); html_sha = sha256_file(base / 'reader-report.html')
            manifest = base / 'publication-manifest.json'
            atomic_write_json(manifest, {'publication_id': 'publication', 'series_id': 'series',
                'publication_type': 'industry', 'scope_id': 'managed-care', 'quarter_id': '2026-Q2',
                'edition': 'stage', 'version': 1, 'publishable': True,
                'checker': {'status': 'passed', 'errors': []},
                'sources': [{'path': str(source.relative_to(root)), 'sha256': source_sha}],
                'artifacts': {'markdown': {'path': str(reader.relative_to(root)), 'sha256': reader_sha},
                              'html': {'path': str((base / 'reader-report.html').relative_to(root)), 'sha256': html_sha}}})
            manifest_path = str(manifest.relative_to(root)); now = '2026-09-21T00:00:00Z'
            lark_documents = {'enabled': True, 'profile': 'test-profile', 'user_route': 'test-user',
                              'as': 'user', 'parent_token': 'test-folder'}
            route_key = publication_delivery_route_key(lark_documents)
            state.db.execute('INSERT INTO publication_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('publication', 'series', 'industry', 'managed-care', '2026-Q2', 'stage', 1,
                 manifest_path, sha256_file(manifest), reader_sha, 'passed', now))
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
              scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'job-series', str(source.relative_to(root)), source_sha,
              'industry', 'managed-care', '2026-Q2', 'stage', 1, 'complete', 'frozen.json', manifest_path, None, 2, now, now))
            state.db.execute('INSERT INTO publication_delivery_routes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (route_key, 'series', 'publication', 'verified', 'doc', 'https://example.test/doc', reader_sha,
                 'remote-sha', 1, None, now)); state.db.commit()
            preview = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertEqual(preview['planned_stages'], ['publication', 'checker', 'cloud'])
            state.db.execute("DELETE FROM publication_delivery_routes")
            state.db.execute("UPDATE publication_jobs SET state='archived' WHERE job_id='job'"); state.db.commit()
            no_route = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertEqual((no_route['cloud_evidence'], no_route['planned_stages']),
                             (None, ['publication', 'checker']))
            archive_preview = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=True, expected_route_key=None)
            self.assertEqual((archive_preview['cloud_evidence'], archive_preview['planned_stages']),
                             ('local_archive', ['publication', 'checker', 'cloud']))
            state.db.execute("UPDATE publication_jobs SET state='complete' WHERE job_id='job'")
            qledger.set_stage(scope['scope_id'], 'cloud', 'completed', artifact_path='old-manifest.json',
                              artifact_sha256='old-manifest-sha')
            qledger.db.execute("UPDATE quarterly_scopes SET finalization_state='finalized_stage_with_gaps',finalized_revision=1 WHERE scope_id=?",
                               (scope['scope_id'],)); qledger.db.commit()
            state.db.execute('INSERT INTO publication_delivery_routes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('wrong-route', 'series', 'publication', 'verified', 'doc', 'https://example.test/wrong', reader_sha,
                 'remote-sha', 1, None, now))
            state.db.execute('INSERT INTO publication_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('series', 'publication', 'verified', 'legacy-doc', 'https://example.test/legacy', reader_sha,
                 'legacy-remote', 1, None, now)); state.db.commit()
            stale_cloud = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertEqual(stale_cloud['planned_invalidations'], ['cloud'])
            self.assertIsNone(stale_cloud['cloud_evidence'])
            stale_applied = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=True,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertFalse(stale_applied['finalized'])
            self.assertEqual(qledger.db.execute("SELECT state FROM quarterly_stages WHERE scope_id=? AND stage='cloud'",
                                                (scope['scope_id'],)).fetchone()[0], 'blocked')
            self.assertEqual(qledger.db.execute("SELECT finalization_state FROM quarterly_scopes WHERE scope_id=?",
                                                (scope['scope_id'],)).fetchone()[0], 'ready_stage_with_gaps')
            state.db.execute("DELETE FROM publication_delivery_routes")
            state.db.execute("DELETE FROM publication_delivery")
            state.db.execute('INSERT INTO publication_delivery_routes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (route_key, 'series', 'publication', 'verified', 'doc', 'https://example.test/doc', reader_sha,
                 'remote-sha', 1, None, now)); state.db.commit()
            self.assertEqual(qledger.db.execute("SELECT state FROM quarterly_stages WHERE scope_id=? AND stage='publication'",
                                                (scope['scope_id'],)).fetchone()[0], 'completed')
            import time
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['delivery']['lark_documents_enabled'] = True
            daily = DailyLedger(root)
            outcomes = run_publication_work(root, config, state, daily, {'lark_documents': lark_documents}, '2026-09-22',
                                            time.monotonic() + 5, discover=False)
            applied = next(row for row in outcomes if row.get('status') == 'reconciled')
            self.assertTrue(applied['finalized'])
            self.assertEqual({row[0] for row in qledger.db.execute(
                "SELECT state FROM quarterly_stages WHERE scope_id=? AND stage IN ('publication','checker','cloud')",
                (scope['scope_id'],))}, {'completed'})
            self.assertEqual(state.db.execute("SELECT attempts FROM publication_jobs WHERE job_id='job'").fetchone()[0], 2)
            again = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=True,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertEqual(again['planned_stages'], [])
            original_reader = reader.read_text(); reader.write_text(original_reader + 'tampered')
            tampered_reader = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertFalse(tampered_reader['eligible']); self.assertIn('reader file/hash', tampered_reader['reason'])
            reader.write_text(original_reader)
            original_manifest = manifest.read_text(); manifest.write_text(original_manifest + '\n')
            tampered_manifest = reconcile_quarterly_publication(root, state, dict(state.db.execute(
                "SELECT * FROM publication_jobs WHERE job_id='job'").fetchone()), apply=False,
                local_archive_allowed=False, expected_route_key=route_key)
            self.assertFalse(tampered_manifest['eligible']); self.assertIn('not registered', tampered_manifest['reason'])
            manifest.write_text(original_manifest)
            daily.db.close(); qledger.close(); state.close()

    def test_publication_reconciliation_rejects_stale_synthesis_binding(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            scope = qledger.freeze({'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'},
                {'industry_id': 'managed-care', 'issuers': [], 'key_symbols': []}, '2026-08-31T00:00:00Z', edition='stage')
            qledger.set_stage(scope['scope_id'], 'synthesis', 'completed', artifact_path='new.json', artifact_sha256='new')
            result = reconcile_quarterly_publication(root, state, {'job_id': 'old', 'publication_type': 'industry',
                'scope_id': 'managed-care', 'quarter_id': '2026-Q2', 'edition': 'stage', 'revision': 1,
                'source_path': 'old.json', 'source_sha256': 'old', 'publication_manifest_path': 'missing.json',
                'state': 'complete'}, apply=False, local_archive_allowed=False, expected_route_key='route')
            self.assertFalse(result['eligible']); self.assertIn('current synthesis', result['reason'])
            qledger.close(); state.close()

    def test_round_progress_treats_below_trigger_quarterly_scope_as_waiting(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            ledger = DailyLedger(root); qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            scope = qledger.freeze({'quarter_id': '2026-Q3', 'period_start': '2026-07-01', 'period_end': '2026-09-30'},
                {'industry_id': 'cloud-software', 'issuers': [], 'key_symbols': []}, '2026-09-20T00:00:00Z', edition='stage')
            qledger.db.execute("UPDATE quarterly_scopes SET accepted_reports_json='[{}]' WHERE scope_id=?", (scope['scope_id'],))
            qledger.db.commit(); qledger.close()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            atomic_write_json(root / config['paths']['universe'], {'industries': []})
            below = {'maturity': {'counts': {'researched_issuers': 1}}, 'eligible_stage': False,
                     'deadline_stage_allowed': False}
            with patch('earnings_daily._registered_quarterly_scope_readiness', return_value=below):
                progress = round_progress(root, state, config, cutoff='2026-09-20T00:00:00Z', ledger=ledger)
            self.assertEqual(progress['pending']['quarterly_scopes'], 0)
            self.assertEqual(progress['pending']['quarterly_waiting'], 1)
            self.assertEqual(progress['actionable_count'], 0)
            state.close(); ledger.db.close()
    def test_cross_day_finalizer_uses_current_notification_day_and_frozen_evidence(self):
        from datetime import datetime, timezone
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1,
                'verified_repo': str(root), 'project': 'test', 'session': 'test', 'verified_at': 'test',
                'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false', 'codex_bin': '/bin/false',
                'delivery_enabled': False})
            cutoff = '2026-09-20T16:37:31+00:00'
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False,
                send=False, finalize_only=True, batch_date='2026-09-21', cutoff=cutoff, round_id='frozen')
            with patch('earnings_daily.datetime') as clock, patch('earnings_daily.finalize') as final:
                clock.now.return_value = datetime(2026, 9, 22, 2, tzinfo=timezone.utc)
                final.return_value = {'delivery': {'state': 'preview'}}
                result = run(args)
            self.assertEqual(final.call_args.args[3], '2026-09-22')
            self.assertEqual(final.call_args.kwargs['cutoff'], cutoff)
            self.assertEqual(result['date'], '2026-09-21')
            self.assertEqual(result['notification_date'], '2026-09-22')

    def test_short_daily_window_defers_company_and_industry_before_claiming(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['publication']['enabled'] = False
            config['quarterly']['automatic_trigger_enabled'] = False
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1,
                'verified_repo': str(root), 'project': 'test', 'session': 'test', 'verified_at': 'test',
                'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false', 'codex_bin': '/bin/false',
                'delivery_enabled': False, 'batch_timeout_seconds': 300})
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False,
                send=False, manual_quarter=None)
            with patch('earnings_daily.industry_work', return_value=[{'industry': 'consumer-retail'}]), \
                 patch('earnings_daily.command') as context, patch('earnings_daily.run_role') as role:
                result = run(args)
            context.assert_not_called(); role.assert_not_called()
            self.assertEqual(result['status'], 'success')
            ledger = DailyLedger(root)
            self.assertEqual(ledger.used(result['date'], 'company'), 0)
            self.assertEqual(ledger.used(result['date'], 'industry'), 0)
            ledger.db.close()

    def test_gap_review_with_158_seconds_remaining_does_not_reserve_attempt(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = DailyLedger(root)
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            input_path = root / 'runtime/earnings/gap-input.json'
            atomic_write_json(input_path, {'input_hash': 'unchanged'})
            scope = {'scope_id': 'scope', 'gap_review_input_path': str(input_path.relative_to(root))}
            with patch('earnings_daily.run_gap_review') as model:
                result = run_gap_review_step(root, config, ledger, qledger, {}, '2026-09-20',
                                             scope, time.monotonic() + 158)
            model.assert_not_called(); self.assertEqual(result['status'], 'queued')
            self.assertEqual(ledger.used('2026-09-20', 'review'), 0)
            self.assertEqual(qledger.db.execute('SELECT count(*) FROM quarterly_gap_attempts').fetchone()[0], 0)
            qledger.close(); ledger.db.close()

    def test_short_quarterly_window_does_not_claim_industry_or_market(self):
        import time
        from earnings_daily import run_quarterly_step
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            ledger = DailyLedger(root); config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            with patch('earnings_daily.inspect_due', return_value={'scopes': [{}], 'quarter': '2026-Q2'}), \
                 patch('earnings_daily.command') as context, patch('earnings_daily.run_gap_review_step') as gap:
                run_quarterly_step(root, config, {}, state, ledger, {}, '2026-09-20',
                    '2026-09-20T02:00:00Z', 'short-window', root / 'runtime/earnings/logs', time.monotonic() + 158)
            context.assert_not_called(); gap.assert_not_called()
            self.assertEqual(ledger.used('2026-09-20', 'quarterly'), 0)
            state.close(); ledger.db.close()

    def test_ineligible_rolling_scope_does_not_spend_gap_review_call(self):
        import time
        from earnings_daily import run_quarterly_step
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            config = json.loads((ROOT / 'config/earnings_research.json').read_text()); config['quarterly']['automatic_trigger_enabled'] = True
            scope = {'scope_id': 'q3-cloud', 'industry_id': 'cloud-software', 'period_start': '2026-07-01',
                'period_end': '2026-09-30', 'quarter_id': '2026-Q3', 'revision': 1,
                'cutoff': '2026-09-20T00:00:00Z', 'frozen_scope_path': 'frozen.json',
                'frozen_industry': {'industry_id': 'cloud-software', 'issuers': [], 'key_symbols': []},
                'input_fingerprint': 'one-of-six', 'eligible_stage': False, 'deadline_stage_allowed': False,
                'maturity': {'counts': {'researched_issuers': 1}, 'critical_gap_status': 'unresolved'}}
            with patch('earnings_daily.inspect_due', return_value={'scopes': [scope], 'quarter': '2026-Q3'}), \
                 patch('earnings_daily.run_gap_review_step') as gap, patch('earnings_daily.command') as command:
                outcomes = run_quarterly_step(root, config, {}, state, ledger, {}, '2026-09-20',
                    '2026-09-20T00:00:00Z', 'q3', root / 'runtime/earnings/logs', time.monotonic() + 1800)
            gap.assert_not_called(); command.assert_not_called()
            self.assertEqual(outcomes[0]['reason'], 'quarterly stage trigger has not been reached')
            self.assertEqual(ledger.used('2026-09-20', 'review'), 0)
            state.close(); ledger.db.close()

    def test_sealed_stage_with_gaps_selects_stage_market_not_full(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            ledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            scope = ledger.freeze({'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'},
                {'industry_id': 'limited', 'issuers': [], 'key_symbols': []}, '2026-08-31T00:00:00Z', edition='stage')
            task, _ = state.enqueue_task(task_type='synthesis', subject_id='limited', period_start='2026-04-01',
                period_end='2026-06-30', input_hash='s', method_version='v1', source_mode='live', profile='quarterly',
                model='m', effort='e')
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            source = root / 'report/earnings/industries/limited/synthesis.json'
            atomic_write_json(source, {'report_id': 's', 'report_type': 'synthesis', 'task_id': task, 'source_mode': 'live'})
            digest = sha256_file(source)
            state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('s', task, 'synthesis', 'limited', '2026-04-01', '2026-06-30', str(source.relative_to(root)),
                 digest, 'm', 'live', 'partial', '2026-08-31T00:00:00Z'))
            manifest = root / 'report/earnings/publications/industry/limited/2026-Q2/v1/publication-manifest.json'
            atomic_write_json(manifest, {'publication_id': 'p', 'publishable': True, 'edition': 'stage',
                'checker': {'status': 'passed', 'errors': []}, 'sources': [{'sha256': digest}]})
            state.db.execute('INSERT INTO publication_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('p', 'series', 'industry', 'limited', '2026-Q2', 'stage', 1, str(manifest.relative_to(root)),
                 sha256_file(manifest), 'body', 'passed', '2026-08-31T00:00:00Z'))
            ledger.set_stage(scope['scope_id'], 'synthesis', 'completed', artifact_path=str(source.relative_to(root)), artifact_sha256=digest)
            ledger.db.execute("UPDATE quarterly_scopes SET finalization_state='finalized_stage_with_gaps' WHERE scope_id=?", (scope['scope_id'],)); ledger.db.commit()
            readiness = _quarterly_market_readiness(root, state, ledger.db)
            self.assertEqual(readiness['actionable'][0]['edition'], 'stage')
            self.assertEqual(readiness['actionable'][0]['dependencies'][0]['finalization_state'], 'finalized_stage_with_gaps')
            state.close(); ledger.close()

    def test_quarterly_runner_passes_stage_edition_to_market_context(self):
        import time
        from earnings_daily import run_quarterly_step
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); daily = DailyLedger(root)
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            scope = qledger.freeze({'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'},
                {'industry_id': 'limited', 'issuers': [], 'key_symbols': []}, '2026-08-31T00:00:00Z', edition='stage')
            report = root / 'report/earnings/limited.json'; atomic_write_json(report, {'report_id': 'limited'})
            digest = sha256_file(report)
            qledger.set_stage(scope['scope_id'], 'synthesis', 'completed', artifact_path=str(report.relative_to(root)), artifact_sha256=digest)
            qledger.close()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text()); config['quarterly']['automatic_trigger_enabled'] = True
            ready = {'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30',
                     'edition': 'stage', 'reasons': [], 'dependencies': []}
            artifact = {'path': str(report.relative_to(root)), 'sha256': digest, 'report': {}}
            with patch('earnings_daily.inspect_due', return_value={'scopes': [], 'quarter': '2026-Q2'}), \
                 patch('earnings_daily._quarterly_market_readiness', return_value={'actionable': [ready], 'waiting': []}), \
                 patch('earnings_daily._latest_role_artifact', return_value=artifact), \
                 patch('earnings_daily.command', return_value={'status': 'skipped', 'model_execution_required': False}) as context:
                run_quarterly_step(root, config, {}, state, daily, {}, '2026-09-20', '2026-08-31T00:00:00Z',
                    'stage-market', root / 'runtime/earnings/logs', time.monotonic() + 1800)
            args = context.call_args.args[2]
            self.assertEqual(args[args.index('--edition') + 1], 'stage')
            state.close(); daily.db.close()

    def test_gap_quota_stops_later_quarterly_scopes_and_market_work(self):
        import time
        from earnings_daily import run_quarterly_step
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            scopes = []
            for index in range(2):
                scopes.append({'scope_id': f'scope-{index}', 'industry_id': f'industry-{index}',
                    'period_start': '2026-04-01', 'period_end': '2026-06-30', 'quarter_id': '2026-Q2',
                    'revision': 1, 'cutoff': '2026-08-31T00:00:00Z', 'frozen_scope_path': f'frozen-{index}.json',
                    'frozen_industry': {'industry_id': f'industry-{index}', 'issuers': [], 'key_symbols': []},
                    'input_fingerprint': f'fingerprint-{index}', 'eligible_stage': True, 'deadline_stage_allowed': False,
                    'maturity': {'counts': {'researched_issuers': 1}, 'critical_gap_status': 'unresolved'}})
            review = {'quarter': '2026-Q2', 'scopes': scopes}
            quota = {'status': 'queued', 'scope_id': 'scope-0', 'stage': 'gap_review',
                     'reason': 'model_quota_exhausted: usage limit'}
            with patch('earnings_daily.inspect_due', return_value=review), \
                 patch('earnings_daily.run_gap_review_step', return_value=quota) as gap, \
                 patch('earnings_daily.command') as command, patch('earnings_daily.run_role') as role:
                outcomes = run_quarterly_step(root, config, {'industries': []}, state, ledger,
                    {'codex_bin': '/bin/false'}, '2026-09-16', '2026-09-15T00:00:00Z', 'quota-run',
                    root / 'runtime/earnings/logs', time.monotonic() + 1800)
            self.assertEqual(outcomes, [quota]); gap.assert_called_once(); command.assert_not_called(); role.assert_not_called()
            self.assertEqual(ledger.used('2026-09-16', 'quarterly'), 0)
            state.close(); ledger.db.close()

    def test_daily_gap_quota_skips_final_publication_pass(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['publication']['enabled'] = True
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1,
                'verified_repo': str(root), 'project': 'test', 'session': 'test', 'verified_at': 'test',
                'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false', 'codex_bin': '/bin/false',
                'delivery_enabled': False, 'batch_timeout_seconds': 120})
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False,
                send=False, manual_quarter=None)
            quota = [{'status': 'queued', 'scope_id': 'scope', 'stage': 'gap_review',
                      'reason': 'model_quota_exhausted: usage limit'}]
            with patch('earnings_daily.command', return_value={'status': 'skipped', 'manifests': []}), \
                 patch('earnings_daily.run_publication_work', side_effect=[[], []]) as publication, \
                 patch('earnings_daily.run_quarterly_step', return_value=quota):
                result = run(args)
            self.assertEqual(publication.call_count, 2)  # resume + current; no post-quarterly pass
            self.assertTrue(any('quota exhausted in quarterly work' in error for error in result['errors']))

    def test_market_completion_flows_through_final_verified_publication_pass(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['publication']['enabled'] = True; config['quarterly']['automatic_trigger_enabled'] = True
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1,
                'verified_repo': str(root), 'project': 'test', 'session': 'test', 'verified_at': 'test',
                'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false', 'codex_bin': '/bin/false',
                'delivery_enabled': False, 'batch_timeout_seconds': 1800})
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False,
                send=False, manual_quarter=None, execution_focus='downstream')
            market = [{'status': 'completed', 'task_id': 'market-task',
                       'report_path': 'report/earnings/market.json', 'report_sha256': 'market-sha'}]
            verified = {'status': 'success', 'job_id': 'market-publication', 'cloud': {'state': 'verified'}}
            with patch('earnings_daily.industry_work', return_value=[]), \
                 patch('earnings_daily.run_quarterly_step', return_value=market), \
                 patch('earnings_daily.run_publication_work', side_effect=[[], [], [verified]]) as publication:
                result = run(args)
            self.assertEqual(publication.call_count, 3)
            self.assertIn(verified, result['publications'])

    def test_quarterly_empty_cohorts_do_not_consume_model_budget(self):
        from earnings_daily import run_quarterly_step
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            atomic_write_json(root / 'config/earnings_research.json', config)
            universe = {'industries': [{'industry_id': 'empty', 'label': 'Empty',
                'issuers': [{'symbol': 'MISSING'}], 'key_symbols': ['MISSING']}]}
            atomic_write_json(root / 'config/earnings_universe.json', universe)
            state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            with patch('earnings_daily.run_gap_review') as gap, patch('earnings_daily.command') as command:
                result = run_quarterly_step(root, config, universe, state, ledger, {'codex_bin': '/bin/false'},
                    '2026-09-16', '2026-09-15T19:00:00Z', 'empty-cohort', root / 'runtime/earnings/logs',
                    time.monotonic() + 1800)
            gap.assert_not_called(); command.assert_not_called()
            self.assertIn('waiting for accepted company evidence', result[0]['reason'])
            self.assertEqual(ledger.used('2026-09-16', 'review'), 0)
            self.assertEqual(ledger.used('2026-09-16', 'quarterly'), 0)
            state.close(); ledger.db.close()

    def test_completed_industry_registry_still_runs_market_when_due_list_is_empty(self):
        from earnings_daily import run_quarterly_step
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            config['budgets']['quarterly_tasks_per_day'] = 5
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            state.db.execute('PRAGMA foreign_keys=OFF')
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            quarter = {'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'}
            for industry in ('a', 'b'):
                scope = qledger.freeze(quarter, {'industry_id': industry, 'issuers': [], 'key_symbols': []},
                                       '2026-08-31T00:00:00Z', edition='full')
                report = root / f'report/earnings/{industry}-synthesis.json'
                atomic_write_json(report, {'report_id': f'{industry}-synthesis', 'report_type': 'synthesis',
                    'task_id': f'{industry}-task', 'research_mode': 'quarterly', 'source_mode': 'live',
                    'scope': {'industry_id': industry, 'reporting_start': quarter['period_start'],
                              'reporting_end': quarter['period_end']}})
                digest = sha256_file(report)
                state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f'{industry}-synthesis', f'{industry}-task', 'synthesis', industry, quarter['period_start'],
                     quarter['period_end'], str(report.relative_to(root)), digest, 'manifest', 'live', 'full',
                     '2026-08-31T00:00:00Z'))
                for stage in ('coverage', 'gap_review', 'industry', 'challenge'):
                    qledger.set_stage(scope['scope_id'], stage, 'completed')
                qledger.set_stage(scope['scope_id'], 'synthesis', 'completed', artifact_path=str(report.relative_to(root)),
                                  artifact_sha256=digest)
                publication = root / f'report/earnings/publications/industry/{industry}/2026-Q2/v1/publication-manifest.json'
                atomic_write_json(publication, {'publication_id': f'pub-{industry}', 'publication_type': 'industry',
                    'scope_id': industry, 'quarter_id': '2026-Q2', 'edition': 'full', 'version': 1,
                    'publishable': True, 'checker': {'status': 'passed', 'errors': []},
                    'sources': [{'path': str(report.relative_to(root)), 'sha256': digest}]})
                state.db.execute('INSERT INTO publication_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f'pub-{industry}', f'series-{industry}', 'industry', industry, '2026-Q2', 'full', 1,
                     str(publication.relative_to(root)), sha256_file(publication), f'content-{industry}', 'passed',
                     '2026-08-31T00:00:00Z'))
            state.db.commit(); qledger.close()
            progress = round_progress(root, state, config, cutoff='2026-09-21T02:00:00Z', ledger=ledger)
            self.assertEqual(progress['pending']['quarterly_market'], 1)
            review = {'quarter': {'quarter_id': '2026-Q2'}, 'scopes': []}
            market_context = {'status': 'success', 'model_execution_required': True,
                              'artifacts': ['runtime/earnings/market-input.json']}
            atomic_write_json(root / market_context['artifacts'][0], {'task_id': 'market-task',
                'lease': {'attempt': 1, 'owner': 'test'}})
            market_result = {'status': 'completed', 'task_id': 'market-task',
                             'report_path': 'report/earnings/market.json', 'report_sha256': 'market-sha'}
            with patch('earnings_daily.inspect_due', return_value=review), \
                 patch('earnings_daily.command', return_value=market_context) as command, \
                 patch('earnings_daily.run_role', return_value=market_result) as role:
                result = run_quarterly_step(root, config, {'industries': []}, state, ledger,
                    {'codex_bin': '/bin/false'}, '2026-09-21', '2026-09-21T02:00:00Z', 'market-close',
                    root / 'runtime/earnings/logs', time.monotonic() + 1800, round_id='round-2')
            self.assertEqual(result[-1], market_result)
            self.assertIn('earnings_market_context.py', command.call_args.args[1])
            role.assert_called_once()
            qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            self.assertEqual({row[0] for row in qledger.db.execute("SELECT state FROM quarterly_stages WHERE stage='market'")},
                             {'completed'})
            qledger.close(); state.close(); ledger.db.close()

    def test_gap_review_failure_recovers_next_day_then_stops_at_attempt_limit(self):
        import hashlib, time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['review_tasks_per_day'] = 1; config['budgets']['max_task_attempts'] = 2
            ledger = DailyLedger(root); qledger = QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite')
            frozen = qledger.freeze({'quarter_id': '2026-Q2', 'period_start': '2026-04-01', 'period_end': '2026-06-30'},
                {'industry_id': 'test', 'issuers': [], 'key_symbols': []}, '2026-08-31T00:00:00Z', edition='full')
            gap_input = {'schema_version': 1, 'scope_id': frozen['scope_id'], 'quarter_id': '2026-Q2',
                'cutoff': frozen['cutoff'], 'frozen_universe_hash': frozen['frozen_universe_hash'], 'reports': [],
                'member_reasons': {}, 'critical_limitations': [], 'required_output': {}}
            gap_input['input_hash'] = hashlib.sha256(json.dumps({k: gap_input[k] for k in
                ('scope_id', 'quarter_id', 'cutoff', 'frozen_universe_hash', 'reports', 'member_reasons', 'critical_limitations')},
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            input_path = root / 'runtime/earnings/quarterly-scopes' / frozen['scope_id'] / 'gap-review-input.json'
            atomic_write_json(input_path, gap_input)
            failing = root / 'runtime/earnings/failing-codex'; failing.write_text('#!/bin/sh\nexit 7\n'); failing.chmod(0o700)
            scope = {'scope_id': frozen['scope_id'], 'gap_review_input_path': str(input_path.relative_to(root))}
            first = run_gap_review_step(root, config, ledger, qledger, {'codex_bin': str(failing)}, '2026-09-16',
                                        scope, time.monotonic() + 1800)
            second = run_gap_review_step(root, config, ledger, qledger, {'codex_bin': str(failing)}, '2026-09-17',
                                         scope, time.monotonic() + 1800)
            third = run_gap_review_step(root, config, ledger, qledger, {'codex_bin': str(failing)}, '2026-09-18',
                                        scope, time.monotonic() + 1800)
            self.assertEqual((first['status'], first['attempts']), ('retryable_failed', 1))
            self.assertEqual((second['status'], second['attempts']), ('terminal_failed', 2))
            self.assertEqual((third['status'], third['attempts']), ('terminal_failed', 2))
            self.assertEqual(qledger.db.execute('SELECT state,attempts FROM quarterly_gap_attempts').fetchone()[:],
                             ('terminal_failed', 2))
            qledger.close(); ledger.db.close()

    def test_daily_entry_executes_gap_review_and_continues_quarterly_dag(self):
        from datetime import datetime as real_datetime, timezone
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir(); (root / 'runtime/earnings').mkdir(parents=True)
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['quarterly']['automatic_trigger_enabled'] = True
            config['budgets']['quarterly_tasks_per_day'] = 1; config['budgets']['review_tasks_per_day'] = 1
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': [{'industry_id': 'test',
                'metric_template': 'test', 'issuers': [{'symbol': 'TEST'}], 'key_symbols': ['TEST']}]})
            fake = root / 'runtime/earnings/fake-gap-codex'
            fake.write_text("""#!/usr/bin/env python3
import json,pathlib,sys
a=sys.argv; out=pathlib.Path(a[a.index('--output-last-message')+1]); prompt=sys.stdin.read()
if '独立季度财报证据缺口审查者' not in prompt: sys.exit(42)
out.write_text(json.dumps({'status':'resolved','limitation_dispositions':[],
'omitted_and_negative_sample_review':[{'sample':'frozen cohort','finding':'no omitted negative sample','evidence_ids':[]}]}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
"""); fake.chmod(0o700)
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1, 'verified_repo': str(root),
                'project': 'test', 'session': 'test', 'verified_at': 'test', 'verified_from_cron_id': 'test',
                'cc_connect_bin': '/bin/false', 'codex_bin': str(fake), 'delivery_enabled': False, 'batch_timeout_seconds': 1800})
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            state.upsert_issuer(issuer_id='issuer', cik='1', symbol='TEST', name='Test', identity_status='resolved')
            state.refresh_event('event', 'issuer', 'earnings', None, '2026-07-26')
            original = root / 'raw_data/earnings/doc.txt'; original.parent.mkdir(parents=True); original.write_text('accepted filing')
            state.register_document({'document_id': 'doc', 'issuer_id': 'issuer', 'event_id': 'event', 'form': '10-Q',
                'source_type': 'sec_filing', 'source_url': 'https://example.com/doc', 'provider': 'sec', 'backend': 'offline',
                'reporting_start': None, 'reporting_end': None, 'published_at': '2026-08-01T00:00:00Z',
                'accepted_at': '2026-08-01T00:00:00Z', 'fetched_at': '2026-08-01T00:00:00Z',
                'public_time_precision': 'second', 'original_path': str(original.relative_to(root)),
                'content_sha256': sha256_file(original), 'source_mode': 'live', 'metadata_json': '{}'})
            task, _ = state.enqueue_task(task_type='company', subject_id='event', period_start=None, period_end='2026-07-26',
                input_hash='input', method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            report = root / 'report/earnings/company.json'
            atomic_write_json(report, {'report_id': 'company', 'report_type': 'company', 'task_id': task, 'source_mode': 'live',
                'cutoff': '2026-08-02T00:00:00Z', 'scope': {'issuer_id': 'issuer', 'reporting_start': None,
                'reporting_end': '2026-07-26'}, 'claims': [], 'limitations': [], 'evidence': [{'evidence_id': 'e1',
                'document_id': 'doc', 'document_version': 1, 'document_hash': sha256_file(original), 'numeric_facts': [
                {'metric': 'revenue', 'value': '100.25', 'unit': 'USD', 'period': {'kind': 'duration',
                 'start': '2026-04-27', 'end': '2026-07-26'}}]}]})
            state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('company', task, 'company', 'event', None, '2026-07-26', str(report.relative_to(root)), sha256_file(report),
                 'manifest', 'live', 'partial', '2026-08-02T00:00:00Z')); state.close()
            called = []
            def fake_command(_root, script, _args, _logs, _timeout):
                called.append(script)
                return {'status': 'skipped', 'manifests': [], 'model_execution_required': False, 'task_state': 'queued'}
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False, send=False,
                manual_quarter=None)
            with patch('earnings_daily.command', side_effect=fake_command), patch('earnings_daily.datetime', wraps=real_datetime) as clock:
                clock.now.return_value = real_datetime(2026, 9, 16, 2, tzinfo=timezone.utc)
                result = run(args)
            self.assertEqual(result['status'], 'success', result)
            self.assertIn('earnings_industry_context.py', called)
            gap = next(row for row in result['quarterly'] if row.get('stage') == 'gap_review')
            self.assertEqual((gap['status'], gap['gap_status'], gap['model'], gap['effort']),
                             ('success', 'resolved', 'gpt-5.6-sol', 'high'))
            self.assertEqual(json.loads((root / gap['artifact']).read_text())['input_hash'],
                             json.loads((root / 'runtime/earnings/quarterly-scopes' /
                             gap['scope_id'] / 'gap-review-input.json').read_text())['input_hash'])

    def test_enabled_daily_offline_publication_to_real_export_shaped_lark_readback_is_idempotent(self):
        from tests.test_earnings_publication import SOURCE, markdown
        from datetime import datetime as real_datetime, timezone
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir(); (root / 'runtime/earnings').mkdir(parents=True)
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['publication']['enabled'] = True; config['delivery']['lark_documents_enabled'] = True
            config['quarterly']['automatic_trigger_enabled'] = False
            config['budgets']['publication_writers_per_day'] = 2; config['budgets']['publication_checkers_per_day'] = 2
            config['budgets']['publication_full_start_threshold_seconds'] = 1
            config['budgets']['publication_checker_start_threshold_seconds'] = 1
            atomic_write_json(root / 'config/earnings_research.json', config)
            atomic_write_json(root / 'config/earnings_universe.json', {'schema_version': 1, 'industries': [
                {'industry_id': 'test', 'label': 'Test', 'metric_template': 'test', 'key_symbols': ['TEST'],
                 'issuers': [{'symbol': 'TEST'}]}]})
            draft = root / 'runtime/earnings/fixture-draft.md'
            draft.write_text(markdown() + '\n| 指标 | 数值（百万美元） |\n| --- | ---: |\n| 收入 | 100 |\n')
            from earnings_publication import validate_reader_markdown
            checked = validate_reader_markdown(draft.read_text(), [SOURCE])
            checker_fixture = root / 'runtime/earnings/checker-fixture.json'
            atomic_write_json(checker_fixture, {'status': 'passed', 'errors': [], 'warnings': [],
                'source_mapping': [{'section': '全文', 'claim_ids': ['c1'], 'evidence_ids': ['e1']}],
                'fact_bindings': checked['fact_mappings']})
            codex = root / 'runtime/earnings/fake-codex'
            codex.write_text("""#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
a=sys.argv; out=pathlib.Path(a[a.index('--output-last-message')+1]); prompt=sys.stdin.read()
counter=pathlib.Path(os.environ['TCA_TEST_COUNTER']); counter.write_text(str(int(counter.read_text() or '0')+1))
if '独立财报发布核对者' in prompt:
 if '程序提取的 occurrence inventory' not in prompt or 'python-string-codepoint-offsets-v1' not in prompt: sys.exit(43)
 shutil.copyfile(os.environ['TCA_TEST_CHECKER'],out)
else: shutil.copyfile(os.environ['TCA_TEST_DRAFT'],out)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
"""); codex.chmod(0o700)
            counter = root / 'runtime/earnings/codex-count'; counter.write_text('0')
            lark = root / 'runtime/earnings/fake-lark'
            lark.write_text("""#!/usr/bin/env python3
import json, pathlib, re, sys
a=sys.argv; home=pathlib.Path(__file__).parent; log=home/'lark-count'; log.write_text(str(int(log.read_text() or '0')+1))
if '--content' in a:
 p=a[a.index('--content')+1]
 if not p.startswith('@./'): sys.exit(41)
 content=pathlib.Path(p[1:]).read_text()
 content=re.sub(r'^\\|(?:\\s*:?-+:?\\s*\\|)+$',lambda m:'|'+'|'.join('-' for _ in m.group(0).strip('|').split('|'))+'|',content,flags=re.M)
 (home/'remote.md').write_text('<title>TEST</title>\\n\\n'+content)
if '+fetch' in a: data={'document':{'content':(home/'remote.md').read_text()}}
elif '+create' in a: data={'document':{'document_id':'doc-offline','url':'https://lark.invalid/doc-offline'}}
else: data={'updated':True}
print(json.dumps({'ok':True,'identity':'user','data':data}))
"""); lark.chmod(0o700); (root / 'runtime/earnings/lark-count').write_text('0')
            deployment = {'schema_version': 1, 'verified_repo': str(root), 'project': 'test', 'session': 'test',
                'verified_at': 'test', 'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false',
                'codex_bin': str(codex), 'delivery_enabled': False, 'batch_timeout_seconds': 60,
                'lark_documents': {'enabled': True, 'lark_cli_bin': str(lark), 'profile': 'offline-user',
                    'user_route': 'offline-route', 'as': 'user', 'parent_position': 'my_space'}}
            atomic_write_json(root / 'runtime/earnings/deployment.json', deployment)
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            state.upsert_issuer(issuer_id='i', cik=None, symbol='TEST', name='Offline fixture', identity_status='resolved')
            state.refresh_event('event', 'i', 'earnings', '2026-04-01', '2026-06-30')
            task, _ = state.enqueue_task(task_type='company', subject_id='event', period_start='2026-04-01', period_end='2026-06-30',
                input_hash='offline', method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            source = root / 'report/earnings/source.json'; atomic_write_json(source, {**SOURCE, 'task_id': task, 'source_mode': 'live'})
            state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('report-company', task, 'company', 'event', '2026-04-01', '2026-06-30', str(source.relative_to(root)),
                 sha256_file(source), 'input', 'live', 'partial', '2026-08-01T00:00:00Z')); state.close()
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False, send=False)
            env = {'TCA_TEST_COUNTER': str(counter), 'TCA_TEST_DRAFT': str(draft), 'TCA_TEST_CHECKER': str(checker_fixture)}
            with patch.dict(os.environ, env), patch('earnings_daily.command', return_value={'manifests': [], 'status': 'skipped'}), \
                 patch('earnings_daily.datetime', wraps=real_datetime) as clock:
                clock.now.return_value = real_datetime(2026, 9, 16, 2, tzinfo=timezone.utc); first = run(args)
                clock.now.return_value = real_datetime(2026, 9, 17, 2, tzinfo=timezone.utc); second = run(args)
            self.assertEqual(first['status'], 'success', first); self.assertEqual(second['status'], 'success', second)
            self.assertEqual(counter.read_text(), '2', (first, second))
            delivery = EarningsState(root / 'runtime/earnings/state.sqlite')
            self.assertEqual(delivery.db.execute("SELECT state FROM publication_jobs").fetchone()[0], 'complete')
            self.assertEqual(delivery.db.execute("SELECT state FROM publication_delivery_routes").fetchone()[0], 'verified')
            delivery.close()

    def test_publication_discovery_queues_only_newest_series_source(self):
        from tests.test_earnings_publication import SOURCE
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            (root / 'config/earnings_research.json').write_bytes((ROOT / 'config/earnings_research.json').read_bytes())
            state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            state.upsert_issuer(issuer_id='i', cik=None, symbol='TEST', name='Test', identity_status='resolved')
            state.refresh_event('event', 'i', 'earnings', '2026-04-01', '2026-06-30')
            for revision in (1, 2):
                task, _ = state.enqueue_task(task_type='company', subject_id='event', period_start='2026-04-01',
                    period_end='2026-06-30', input_hash=f'input-{revision}', method_version='v1', source_mode='live',
                    profile='daily', model='gpt-5.6-sol', effort='medium')
                state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
                source = root / f'report/earnings/source-{revision}.json'
                atomic_write_json(source, {**SOURCE, 'report_id': f'report-{revision}', 'task_id': task,
                    'source_mode': 'live', 'change_summary': f'revision-{revision}'})
                state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f'report-{revision}', task, 'company', 'event', '2026-04-01', '2026-06-30',
                     str(source.relative_to(root)), sha256_file(source), 'manifest', 'live', 'partial', f'2026-08-0{revision}T00:00:00Z'))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_writers_per_day'] = 0; config['budgets']['publication_checkers_per_day'] = 0
            run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'}, '2026-09-16', time.monotonic() + 5)
            jobs = state.db.execute('SELECT source_path,revision,state FROM publication_jobs').fetchall()
            self.assertEqual([(row['source_path'], row['revision'], row['state']) for row in jobs],
                             [('report/earnings/source-2.json', 1, 'local_pending')])
            state.close(); ledger.db.close()

    def test_publication_preparation_uses_daily_runtime_config_path(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series', 'report/earnings/source.json', 'sha', 'company',
                'TEST', '2026-Q2', 'full', 1, 'local_pending', None, None, None, 0, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_writers_per_day'] = 0; config['budgets']['publication_checkers_per_day'] = 0
            manifest = root / 'runtime/earnings/publications/runs/test/input-manifest.json'
            with patch('earnings_daily.prepare_publication_input', return_value=manifest) as prepare:
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'}, '2026-09-16',
                                     time.monotonic() + 5, discover=False, config_path='runtime/earnings/runtime-config.json')
            self.assertEqual(prepare.call_args.kwargs['config_path'], 'runtime/earnings/runtime-config.json')
            state.close(); ledger.db.close()

    def test_latest_company_publication_precedes_older_checker_recovery(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            state.upsert_issuer(issuer_id='issuer', cik=None, symbol='TEST', name='Test', identity_status='resolved')
            now = '2026-09-16T00:00:00Z'
            jobs = []
            for label, period, job_state in [('old', '2025-03-31', 'checker_pending'), ('new', '2026-06-30', 'local_pending')]:
                event = f'event-{label}'; state.refresh_event(event, 'issuer', 'earnings', None, period)
                task, _ = state.enqueue_task(task_type='company', subject_id=event, period_start=None, period_end=period,
                    input_hash=label, method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')
                state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
                source = root / f'report/earnings/{label}.json'
                atomic_write_json(source, {'report_id': label, 'report_type': 'company', 'task_id': task,
                    'source_mode': 'live', 'scope': {'symbol': 'TEST', 'reporting_end': period}})
                digest = sha256_file(source)
                state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (label, task, 'company', event, None, period, str(source.relative_to(root)), digest,
                     'manifest', 'live', 'partial', now))
                manifest = root / f'runtime/earnings/publications/runs/{label}/input-manifest.json'
                atomic_write_json(manifest, {'name': label, 'permitted_outputs': {
                    'runner_result': str((manifest.parent / 'runner-result.json').relative_to(root))}})
                if label == 'old': atomic_write_json(manifest.with_name('writer-stage.json'), {'status': 'complete'})
                job_id = f'job-{label}'
                state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                    scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (job_id, f'series-{label}', str(source.relative_to(root)), digest,
                    'company', 'TEST', period[:4] + '-Q1', 'stage', 1, job_state, str(manifest.relative_to(root)),
                    None, None, 0, now, now))
                jobs.append((label, manifest))
            state.db.commit()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_writers_per_day'] = 1; config['budgets']['publication_checkers_per_day'] = 1
            config['budgets']['publication_full_start_threshold_seconds'] = 1
            config['budgets']['publication_checker_start_threshold_seconds'] = 1
            with patch('earnings_daily.run_publication', return_value={'status': 'success', 'manifest_path': 'report/publication.json'}) as publish:
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'}, '2026-09-16',
                                     time.monotonic() + 5, discover=False)
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.args[1], jobs[1][1])
            self.assertEqual(state.db.execute("SELECT state FROM publication_jobs WHERE job_id='job-old'").fetchone()[0],
                             'checker_pending')
            state.close(); ledger.db.close()

    def test_historical_publication_budget_is_separate_and_bounded(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            state.upsert_issuer(issuer_id='issuer', cik=None, symbol='TEST', name='Test', identity_status='resolved')
            now = '2026-09-16T00:00:00Z'
            for label, period in [('old-1', '2025-03-31'), ('old-2', '2025-06-30'), ('head', '2026-06-30')]:
                event = f'event-{label}'; state.refresh_event(event, 'issuer', 'earnings', None, period)
                task, _ = state.enqueue_task(task_type='company', subject_id=event, period_start=None, period_end=period,
                    input_hash=label, method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')
                state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
                source = root / f'report/earnings/{label}.json'
                atomic_write_json(source, {'report_id': label, 'report_type': 'company', 'task_id': task,
                    'source_mode': 'live', 'scope': {'symbol': 'TEST', 'reporting_end': period}})
                digest = sha256_file(source)
                state.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (label, task, 'company', event, None, period, str(source.relative_to(root)), digest,
                     'manifest', 'live', 'partial', now))
                if label != 'head':
                    manifest = root / f'runtime/earnings/publications/runs/{label}/input-manifest.json'
                    atomic_write_json(manifest, {'name': label, 'permitted_outputs': {
                        'runner_result': str((manifest.parent / 'runner-result.json').relative_to(root))}})
                    state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                        scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (f'job-{label}', f'series-{label}',
                        str(source.relative_to(root)), digest, 'company', 'TEST', period[:4] + '-Q1', 'stage', 1,
                        'local_pending', str(manifest.relative_to(root)), None, None, 0, now, now))
            state.db.commit()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_writers_per_day'] = 3; config['budgets']['publication_checkers_per_day'] = 3
            config['budgets']['publication_backfill_limit'] = 1
            config['budgets']['publication_full_start_threshold_seconds'] = 1
            config['budgets']['publication_checker_start_threshold_seconds'] = 1
            with patch('earnings_daily.run_publication', return_value={'status': 'success', 'manifest_path': 'report/publication.json'}) as publish:
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'}, '2026-09-16',
                                     time.monotonic() + 5, discover=False)
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(ledger.used('2026-09-16', 'publication_backfill'), 1)
            heads = _latest_company_publication_heads(root, state)
            self.assertFalse(_publication_matches_current_head({'publication_type': 'company', 'scope_id': 'TEST',
                'sources': [{'sha256': 'not-the-head'}]}, heads))
            state.refresh_event('event-newest', 'issuer', 'earnings', None, '2026-09-30')
            newest, _ = state.enqueue_task(task_type='company', subject_id='event-newest', period_start=None,
                period_end='2026-09-30', input_hash='newest', method_version='v1', source_mode='live',
                profile='daily', model='gpt-5.6-sol', effort='medium')
            heads = _latest_company_publication_heads(root, state)
            self.assertEqual(heads[('company', 'TEST')]['task_id'], newest)
            self.assertIsNone(heads[('company', 'TEST')]['sha256'])
            self.assertFalse(_publication_matches_current_head({'publication_type': 'company', 'scope_id': 'TEST',
                'sources': [{'sha256': digest}]}, heads))
            state.db.execute("UPDATE research_tasks SET state='terminal_failed' WHERE task_id=?", (newest,)); state.db.commit()
            self.assertIsNone(_latest_company_publication_heads(root, state)[('company', 'TEST')]['sha256'])
            state.close(); ledger.db.close()

    def test_cached_failed_publication_schedules_repair_without_new_budget_or_cloud(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            manifest = root / 'runtime/earnings/publications/runs/original/input-manifest.json'
            result_path = manifest.parent / 'runner-result.json'
            atomic_write_json(manifest, {'permitted_outputs': {'runner_result': str(result_path.relative_to(root))}})
            atomic_write_json(result_path, {'status': 'failed', 'reason': 'semantic drift',
                'semantic_errors': ['nine-month fact used as one quarter'], 'deterministic_errors': []})
            repair = manifest.parent / 'repair-attempt-1/input-manifest.json'; atomic_write_json(repair, {'repair': {'attempt': 1}})
            now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series', 'report/source.json', 'hash', 'company',
                'TEST', '2026-Q2', 'stage', 1, 'retryable_failed', str(manifest.relative_to(root)), None, None, 1, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            with patch('earnings_daily.prepare_repair_input', return_value=repair), \
                 patch('earnings_daily.run_publication', side_effect=AssertionError('cached failure must not call model')):
                outcomes = run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'},
                                                '2026-09-16', time.monotonic() + 5, discover=False)
            row = state.db.execute("SELECT state,input_manifest_path,publication_manifest_path,error FROM publication_jobs").fetchone()
            self.assertEqual((row['state'], row['input_manifest_path'], row['publication_manifest_path']),
                             ('retryable_failed', str(repair.relative_to(root)), None))
            self.assertIn('nine-month fact', row['error'])
            self.assertEqual(ledger.used('2026-09-16', 'publication_checker'), 0)
            self.assertEqual(outcomes, [])
            state.close(); ledger.db.close()

    def test_publication_does_not_start_below_stage_threshold(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            manifest = root / 'runtime/earnings/publications/runs/job/input-manifest.json'
            atomic_write_json(manifest, {'permitted_outputs': {'runner_result': 'runtime/earnings/publications/runs/job/result.json'}})
            now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series', 'report/source.json', 'hash', 'company',
                'TEST', '2026-Q2', 'stage', 1, 'local_pending', str(manifest.relative_to(root)), None, None, 0, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_full_start_threshold_seconds'] = 60
            with patch('earnings_daily.run_publication', side_effect=AssertionError('insufficient clock must stay queued')):
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'},
                                     '2026-09-16', time.monotonic() + 5, discover=False)
            self.assertEqual(state.db.execute('SELECT state FROM publication_jobs').fetchone()[0], 'local_pending')
            self.assertEqual(ledger.used('2026-09-16', 'publication_writer'), 0)
            state.close(); ledger.db.close()

    def test_enabled_daily_calls_recovery_before_discovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            config = json.loads((ROOT / 'config/earnings_research.json').read_text()); config['publication']['enabled'] = True
            atomic_write_json(root / 'config/earnings_research.json', config)
            (root / 'config/earnings_universe.json').write_bytes((ROOT / 'config/earnings_universe.json').read_bytes())
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1, 'verified_repo': str(root),
                'project': 'test', 'session': 'test', 'verified_at': 'test', 'verified_from_cron_id': 'test',
                'cc_connect_bin': '/bin/false', 'codex_bin': '/bin/false', 'delivery_enabled': False})
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False, send=False)
            with patch('earnings_daily.command', return_value={'manifests': [], 'status': 'skipped'}), \
                 patch('earnings_daily.run_publication_work', return_value=[]) as publication:
                result = run(args)
            self.assertEqual(result['status'], 'success')
            self.assertEqual([call.kwargs.get('discover', True) for call in publication.call_args_list], [False, True, True])

    def test_archived_local_publication_can_cloud_sync_later_without_writer(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            body = root / 'report/earnings/publications/company/TEST/2026-Q2/v1/reader-report.md'; body.parent.mkdir(parents=True)
            body.write_text('# accepted\n')
            manifest = body.with_name('publication-manifest.json')
            atomic_write_json(manifest, {'publication_id': 'publication', 'series_id': 'series', 'title': 'TEST',
                'artifacts': {'markdown': {'path': str(body.relative_to(root)), 'sha256': sha256_file(body)}}})
            now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series-key', 'report/earnings/source.json', 'source', 'company',
                'TEST', '2026-Q2', 'full', 1, 'archived', None, str(manifest.relative_to(root)), None, 1, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text()); config['delivery']['lark_documents_enabled'] = True
            publisher = MagicMock(); publisher.publish.return_value = {'state': 'verified', 'document_id': 'doc', 'url': 'https://lark.invalid/doc'}
            with patch('earnings_daily.LarkDocumentPublisher', return_value=publisher), patch('earnings_daily.run_publication') as writer:
                run_publication_work(root, config, state, ledger, {'lark_documents': {}}, '2026-09-17', time.monotonic() + 5, discover=False)
            writer.assert_not_called(); publisher.publish.assert_called_once()
            self.assertEqual(state.db.execute("SELECT state FROM publication_jobs").fetchone()[0], 'complete')
            state.close(); ledger.db.close()

    def test_publication_preparation_failure_is_terminal_after_bounded_days(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            (root / 'config/earnings_research.json').write_bytes((ROOT / 'config/earnings_research.json').read_bytes())
            state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root); now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series', 'report/earnings/missing.json', 'missing', 'company',
                'TEST', '2026-Q2', 'stage', 1, 'local_pending', None, None, None, 0, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text()); config['budgets']['max_task_attempts'] = 2
            for day in ('2026-09-16', '2026-09-17'):
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'}, day, time.monotonic() + 5, discover=False)
            row = state.db.execute('SELECT state,attempts,error FROM publication_jobs').fetchone()
            self.assertEqual((row['state'], row['attempts']), ('terminal_failed', 2)); self.assertIn('missing', row['error'])
            state.close(); ledger.db.close()

    def test_budget_survives_restarts_and_resets_only_on_new_day(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = DailyLedger(root)
            self.assertTrue(ledger.reserve('2026-09-15', 'a', 'company', 1)); ledger.db.close()
            ledger = DailyLedger(root)
            self.assertFalse(ledger.reserve('2026-09-15', 'b', 'company', 1))
            self.assertTrue(ledger.reserve('2026-09-16', 'b', 'company', 1)); ledger.db.close()

    def test_season_window_changes_budget_but_weekend_is_not_skipped(self):
        config = json.loads((ROOT / 'config/earnings_research.json').read_text())
        self.assertEqual(season_limit(config, '2026-10-11'), 20)
        self.assertEqual(season_limit(config, '2026-09-13'), 10)

    def test_material_notification_requires_evidence_and_thesis_change(self):
        report = {'thesis_state': 'strengthening', 'completeness': {'status': 'partial'}, 'evidence': [{'id': 'e'}]}
        self.assertTrue(notification_material(report, {'thesis_state': 'emerging'}))
        self.assertFalse(notification_material(report, report))
        self.assertFalse(notification_material({**report, 'evidence': []}, None))

    def test_no_new_fulltext_notification_states_zero_without_fixed_success_claim(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir(); (root / 'runtime/earnings').mkdir(parents=True)
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': []})
            deployed = {'schema_version': 1, 'verified_repo': str(root), 'project': 'p', 'session': 's',
                'cc_connect_bin': '/bin/false', 'verified_at': 'now', 'verified_from_cron_id': 'cron',
                'delivery_enabled': False}
            deployed_path = root / 'runtime/earnings/deployment.json'; atomic_write_json(deployed_path, deployed)
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            result = finalize(root, deployed, deployed_path, '2026-09-20', [], ['publication:failed'], state, send=False)
            decision = json.loads((root / result['decision']).read_text())
            body = (root / decision['body_path']).read_text()
            self.assertIn('今日新增并回读全文：0 份', body)
            self.assertNotIn('核对通过的全文由显式用户身份写入并回读', body)
            self.assertFalse(decision['should_send'])
            state.close()

    def test_more_than_five_publications_keep_every_cloud_entry(self):
        rows = [({'title': f'报告{i}', 'edition': 'full', 'version': 1}, {'url': f'https://lark/{i}'}, {}) for i in range(7)]
        rendered = render_publication_entries(rows)
        self.assertEqual(len(rendered), 7)
        self.assertIn('https://lark/6', rendered[-1])

    def test_fixture_and_unknown_model_do_not_launch_process(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); manifest = root / 'runtime/earnings/runs/input.json'
            for profile, mode in [({'model': 'fake', 'effort': 'high'}, 'live'),
                                  ({'model': 'gpt-5.6-sol', 'effort': 'medium'}, 'fixture')]:
                atomic_write_json(manifest, {'profile': profile, 'source_mode': mode})
                with patch('earnings_role_runner.subprocess.Popen') as popen:
                    with self.assertRaises(ValueError):
                        run_role(root, manifest, binary=sys.executable, timeout=1)
                popen.assert_not_called()

    def test_no_work_resume_invokes_no_model_and_sends_nothing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            for name in ['earnings_research.json', 'earnings_universe.json']:
                (root / 'config' / name).write_bytes((ROOT / 'config' / name).read_bytes())
            deployed = {'schema_version': 1, 'verified_repo': str(root), 'project': 'test', 'session': 'test',
                'verified_at': 'test', 'verified_from_cron_id': 'test', 'cc_connect_bin': '/bin/false',
                'codex_bin': '/bin/false', 'delivery_enabled': True}
            atomic_write_json(root / 'runtime/earnings/deployment.json', deployed)
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=True, collect_only=False, send=True)
            with patch('earnings_daily.command', return_value={'manifests': [], 'status': 'skipped'}) as command, \
                 patch('earnings_daily.run_role') as model, patch('earnings_delivery.subprocess.run') as sender:
                result = run(args)
            self.assertEqual(result['status'], 'success'); model.assert_not_called(); sender.assert_not_called()
            self.assertEqual(result['delivery']['delivery']['state'], 'suppressed')

class EarningsDailyRecoveryTests(unittest.TestCase):
    def test_quota_releases_company_and_quarterly_attempt_manifest_for_next_day_success(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            for task_type in ('company', 'industry'):
                task, _ = state.enqueue_task(task_type=task_type, subject_id=f'{task_type}-subject', period_start=None,
                    period_end='2026-06-30', input_hash=task_type, method_version='v1', source_mode='live',
                    profile='daily', model='gpt-5.6-sol', effort='medium')
                claimed = state.claim_task(task, owner='day-one', lease_seconds=60)
                manifest_path = root / f'runtime/earnings/runs/day-one/{task_type}/input-manifest.json'
                atomic_write_json(manifest_path, {'task_id': task})
                state.register_attempt_manifest(task, 1, str(manifest_path.relative_to(root)), sha256_file(manifest_path))
                manifest = {'task_id': task, 'lease': {'owner': 'day-one', 'attempt': claimed['attempts']}}
                fail_owned_attempt(state, manifest, 'model_quota_exhausted: usage limit')
                row = state.db.execute('SELECT state,attempts FROM research_tasks WHERE task_id=?', (task,)).fetchone()
                self.assertEqual(tuple(row), ('queued', 0))
                self.assertFalse(state.db.execute('SELECT 1 FROM task_attempt_manifests WHERE task_id=?', (task,)).fetchone())
                next_claim = state.claim_task(task, owner='day-two', lease_seconds=60)
                next_manifest = root / f'runtime/earnings/runs/day-two/{task_type}/input-manifest.json'
                atomic_write_json(next_manifest, {'task_id': task, 'mock_model': 'success'})
                state.register_attempt_manifest(task, 1, str(next_manifest.relative_to(root)), sha256_file(next_manifest))
                self.assertEqual(next_claim['attempts'], 1)
                state.complete_task(task, f'{task_type}-mock-success.json')
                self.assertEqual(state.db.execute('SELECT state FROM research_tasks WHERE task_id=?', (task,)).fetchone()[0], 'completed')
            state.close()

    def test_publication_quota_releases_budget_and_attempt_before_next_day_mock_success(self):
        import time
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite'); ledger = DailyLedger(root)
            manifest = root / 'runtime/earnings/publications/runs/quota/input-manifest.json'
            atomic_write_json(manifest, {'permitted_outputs': {'runner_result':
                str((manifest.parent / 'runner-result.json').relative_to(root))}})
            now = '2026-09-16T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
              scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('quota-job', 'series', 'report/source.json', 'hash', 'company',
              'TEST', '2026-Q2', 'stage', 1, 'local_pending', str(manifest.relative_to(root)), None, None, 1, now, now))
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['budgets']['publication_full_start_threshold_seconds'] = 1
            config['budgets']['publication_checker_start_threshold_seconds'] = 1
            with patch('earnings_daily.run_publication', side_effect=RuntimeError('model_quota_exhausted: usage limit')):
                first = run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'},
                                             '2026-09-16', time.monotonic() + 5, discover=False)
            row = state.db.execute("SELECT state,attempts FROM publication_jobs WHERE job_id='quota-job'").fetchone()
            self.assertEqual(tuple(row), ('retryable_failed', 1))
            self.assertEqual(ledger.used('2026-09-16', 'publication_writer'), 0)
            self.assertEqual(ledger.used('2026-09-16', 'publication_checker'), 0)
            self.assertIn('model_quota_exhausted', first[0]['reason'])
            with patch('earnings_daily.run_publication', return_value={
                    'status': 'success', 'manifest_path': 'report/earnings/publications/mock.json'}):
                run_publication_work(root, config, state, ledger, {'codex_bin': '/bin/false'},
                                     '2026-09-17', time.monotonic() + 5, discover=False)
            row = state.db.execute("SELECT state,attempts FROM publication_jobs WHERE job_id='quota-job'").fetchone()
            self.assertEqual(tuple(row), ('archived', 2))
            state.close(); ledger.db.close()

    def test_exhausted_work_stays_visible_until_superseded(self):
        with TemporaryDirectory() as temp:
            state = EarningsState(Path(temp) / 'runtime/earnings/state.sqlite')
            kwargs = dict(task_type='company', subject_id='event', period_start=None, period_end=None,
                          method_version='v1', source_mode='live', profile='daily', model='gpt-5.6-sol', effort='medium')
            failed, _ = state.enqueue_task(input_hash='old', **kwargs)
            state.fail_task(failed, 'exhausted', retryable=False)
            self.assertEqual(unresolved_terminal_count(state), 1)
            state.enqueue_task(input_hash='new', **kwargs)
            self.assertEqual(unresolved_terminal_count(state), 0)
            state.close()

    def test_outer_error_does_not_revert_completed_or_reclaimed_task(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            task_id, _ = state.enqueue_task(task_type='company', subject_id='event-test', period_start=None,
                period_end=None, input_hash='test', method_version='v1', source_mode='fixture', profile='daily',
                model='gpt-5.6-sol', effort='medium')
            state.claim_task(task_id, owner='owner-two', lease_seconds=60)
            stale = {'task_id': task_id, 'lease': {'owner': 'owner-one', 'attempt': 1}}
            fail_owned_attempt(state, stale, 'old outer exception')
            self.assertEqual(state.db.execute('SELECT state FROM research_tasks').fetchone()[0], 'running')
            state.complete_task(task_id, 'test-completion')
            fail_owned_attempt(state, {'task_id': task_id, 'lease': {'owner': 'owner-two', 'attempt': 1}}, 'after commit')
            self.assertEqual(state.db.execute('SELECT state FROM research_tasks').fetchone()[0], 'completed')
            state.close()

    def test_initialization_requires_real_coverage_not_empty_failure_list(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir()
            (root / 'config/earnings_research.json').write_bytes((ROOT / 'config/earnings_research.json').read_bytes())
            atomic_write_json(root / 'config/earnings_universe.json', {'industries': [{'industry_id': 'test', 'issuers': [{'symbol': 'TEST'}]}]})
            atomic_write_json(root / 'runtime/earnings/deployment.json', {'schema_version': 1, 'verified_repo': str(root),
                'project': 'test', 'session': 'test', 'verified_at': 'test', 'verified_from_cron_id': 'test',
                'cc_connect_bin': sys.executable, 'codex_bin': sys.executable, 'delivery_enabled': False})
            args = argparse.Namespace(repo_root=str(root), config='config/earnings_research.json',
                deployment='runtime/earnings/deployment.json', resume_only=False, collect_only=True, send=False)
            for complete in [False, True]:
                response = {'status': 'success', 'summary': {'failures': [], 'initialization_coverage': {'TEST': {'complete': complete}}}}
                with patch('earnings_daily.command', return_value=response), patch('earnings_daily.run_role') as model:
                    run(args)
                model.assert_not_called()
                ledger = DailyLedger(root)
                self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM initialized').fetchone()[0], int(complete))
                ledger.db.close()

    def test_model_context_excludes_future_and_old_history_and_exposes_truncation(self):
        from datetime import date
        from earnings_context import bounded_financial_history
        facts = [{'metric': 'revenue', 'period': {'end': '2026-06-30'}},
                 {'metric': 'future', 'period': {'end': '2026-09-30'}},
                 {'metric': 'old', 'period': {'end': '2020-06-30'}},
                 {'metric': 'income', 'period': {'end': '2026-03-31'}}]
        selected, truncated = bounded_financial_history(facts, date(2026, 6, 30), limit=1)
        self.assertEqual([f['metric'] for f in selected], ['revenue'])
        self.assertTrue(truncated)
        selected, truncated = bounded_financial_history(facts, date(2026, 6, 30))
        self.assertEqual([f['metric'] for f in selected], ['revenue', 'income'])
        self.assertFalse(truncated)

    def test_budget_allows_bounded_retry_in_same_second(self):
        with TemporaryDirectory() as temp:
            ledger = DailyLedger(Path(temp).resolve())
            with patch('earnings_daily.utc_now', return_value='2026-09-15T00:00:00Z'):
                self.assertTrue(ledger.reserve('2026-09-15', 'same-task', 'company', 2))
                self.assertTrue(ledger.reserve('2026-09-15', 'same-task', 'company', 2))
                self.assertFalse(ledger.reserve('2026-09-15', 'same-task', 'company', 2))
            ledger.db.close()


class EarningsRoleProcessTests(unittest.TestCase):
    def test_stale_manifest_cannot_release_foreign_owner_same_attempt(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            task, _ = state.enqueue_task(task_type='industry', subject_id='industry', period_start=None,
                period_end='2026-06-30', input_hash='input', method_version='v1', source_mode='live',
                profile='daily', model='gpt-5.6-sol', effort='medium')
            claimed = state.claim_task(task, owner='old-owner', lease_seconds=60)
            manifest_path = root / 'runtime/earnings/runs/foreign/input-manifest.json'
            manifest = {'manifest_type': 'earnings-role-input', 'task_id': task,
                'profile': {'model': 'gpt-5.6-sol', 'effort': 'medium'}, 'source_mode': 'live',
                'lease': {'owner': 'old-owner', 'attempt': claimed['attempts']}, 'input_hash': 'input',
                'previous_artifacts': [], 'company_artifacts': []}
            atomic_write_json(manifest_path, manifest)
            state.register_attempt_manifest(task, 1, str(manifest_path.relative_to(root)), sha256_file(manifest_path))
            state.db.execute("UPDATE research_tasks SET lease_owner='new-owner' WHERE task_id=?", (task,))
            state.close()
            with patch('earnings_role_runner.subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'stale task lease'):
                    run_role(root, manifest_path, binary=sys.executable, timeout=1)
            popen.assert_not_called()
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            row = state.db.execute('SELECT state,attempts,lease_owner FROM research_tasks WHERE task_id=?', (task,)).fetchone()
            self.assertEqual(tuple(row), ('running', 1, 'new-owner'))
            self.assertEqual(state.db.execute('SELECT COUNT(*) FROM task_attempt_manifests').fetchone()[0], 1)
            state.close()

    def test_superseded_company_artifact_is_rejected_before_popen_and_attempt_is_restored(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / 'runtime/earnings/state.sqlite')
            old, _ = state.enqueue_task(task_type='company', subject_id='event', period_start=None,
                period_end='2026-06-30', input_hash='old', method_version='v1', source_mode='live',
                profile='daily', model='gpt-5.6-sol', effort='medium')
            state.claim_task(old, owner='old', lease_seconds=60); state.complete_task(old, 'old-completion')
            artifact = root / 'report/earnings/old.json'; atomic_write_json(artifact, {'cutoff': '2026-09-01T00:00:00Z'})
            state.db.execute("""INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('old-report', old, 'company', 'event', None, '2026-06-30', str(artifact.relative_to(root)),
                 sha256_file(artifact), 'manifest', 'live', 'complete', '2026-09-01T00:00:00Z'))
            state.enqueue_task(task_type='company', subject_id='event', period_start=None,
                period_end='2026-06-30', input_hash='new', method_version='v1', source_mode='live',
                profile='daily', model='gpt-5.6-sol', effort='medium')
            role, _ = state.enqueue_task(task_type='industry', subject_id='industry', period_start=None,
                period_end='2026-06-30', input_hash='role', method_version='v1', source_mode='live',
                profile='daily', model='gpt-5.6-sol', effort='medium')
            claimed = state.claim_task(role, owner='role-owner', lease_seconds=60)
            manifest_path = root / 'runtime/earnings/runs/preflight/input-manifest.json'
            manifest = {'manifest_type': 'earnings-role-input', 'task_id': role,
                'profile': {'model': 'gpt-5.6-sol', 'effort': 'medium'}, 'source_mode': 'live',
                'lease': {'owner': claimed['lease_owner'], 'attempt': claimed['attempts']}, 'input_hash': 'role',
                'previous_artifacts': [], 'company_artifacts': [{'report_id': 'old-report', 'task_id': old,
                    'path': str(artifact.relative_to(root)), 'sha256': sha256_file(artifact)}]}
            atomic_write_json(manifest_path, manifest)
            state.register_attempt_manifest(role, 1, str(manifest_path.relative_to(root)), sha256_file(manifest_path))
            state.close()
            with patch('earnings_role_runner.subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'artifact superseded'):
                    run_role(root, manifest_path, binary=sys.executable, timeout=1)
            popen.assert_not_called()
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            row = state.db.execute('SELECT state,attempts FROM research_tasks WHERE task_id=?', (role,)).fetchone()
            self.assertEqual(tuple(row), ('queued', 0)); state.close()

    def test_explicit_model_read_only_and_timeout_kills_process_group(self):
        import subprocess
        from unittest.mock import MagicMock
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            manifest = root / 'runtime/earnings/runs/attempt/input.json'
            atomic_write_json(manifest, {'task_id': 'task-test', 'profile': {'model': 'gpt-5.6-sol', 'effort': 'medium'}, 'source_mode': 'live'})
            process = MagicMock(pid=987654, returncode=None)
            process.communicate.side_effect = subprocess.TimeoutExpired('codex', 1)
            process.poll.return_value = None
            with patch('earnings_role_runner.subprocess.Popen', return_value=process) as popen, \
                 patch('earnings_role_runner.os.killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_role(root, manifest, binary=sys.executable, timeout=1)
            args = popen.call_args.args[0]
            self.assertIn('--ignore-user-config', args)
            self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
            self.assertEqual(args[args.index('-m') + 1], 'gpt-5.6-sol')
            self.assertIn('model_reasoning_effort="medium"', args)
            kill.assert_called_once()
            self.assertEqual(json.loads((manifest.parent / 'runner-result.json').read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
