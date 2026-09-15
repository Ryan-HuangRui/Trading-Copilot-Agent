import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'script'))
from earnings_common import atomic_write_json
from earnings_daily import DailyLedger, fail_owned_attempt, notification_material, run, season_limit, unresolved_terminal_count
from earnings_state import EarningsState
from earnings_role_runner import run_role


class EarningsDailyTests(unittest.TestCase):
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
