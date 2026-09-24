import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
from earnings_common import atomic_write_json, read_json
from earnings_delivery import deliver, prepare_notification


class EarningsDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'runtime/earnings'
        self.runtime.mkdir(parents=True)
        self.binary = self.runtime / 'sender'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)
        self.deployment = {'schema_version': 1, 'verified_repo': str(self.root), 'project': 'this-repo',
            'session': 'verified-session', 'cc_connect_bin': str(self.binary), 'verified_at': 'test',
            'verified_from_cron_id': 'prior-repo-task', 'delivery_enabled': True}
        self.deployment_path = self.runtime / 'deployment.json'
        atomic_write_json(self.deployment_path, self.deployment)

    def prepare(self, text='Test-only summary', send=True, day='2026-09-15'):
        return prepare_notification(self.root, self.deployment, day=day, body=text, report_versions=[],
            kind='daily', rationale='test', should_send=send)

    def test_preview_and_suppressed_do_not_send(self):
        for send in [False, True]:
            path = self.prepare(str(send), send)
            with patch('earnings_delivery.subprocess.run') as run:
                result = deliver(self.root, path, self.deployment_path)
            run.assert_not_called()
            self.assertEqual(result['state'], 'preview' if send else 'suppressed')

    def test_success_receipt_and_exact_routing_then_idempotency(self):
        path = self.prepare()
        with patch('earnings_delivery.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'accepted', b'')) as run:
            self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['state'], 'sent')
            self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['status'], 'skipped')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:], ['send', '--project', 'this-repo', '--session', 'verified-session', '--stdin'])
        self.assertTrue(read_json(path)['receipt']['sha256'])

    def test_send_timeout_and_nonzero_are_unknown_never_auto_retried(self):
        for error in [subprocess.TimeoutExpired('send', 1), subprocess.CompletedProcess([], 1, b'', b'error')]:
            path = self.prepare(str(type(error)), day=str(type(error)))
            with patch('earnings_delivery.subprocess.run') as run:
                if isinstance(error, Exception):
                    run.side_effect = error
                else:
                    run.return_value = error
                self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['state'], 'unknown')
                self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['state'], 'unknown')
            self.assertEqual(run.call_count, 1)

    def test_crash_after_send_started_is_reconciled_without_sender(self):
        path = self.prepare()
        d = read_json(path); d['state'] = 'sending'; atomic_write_json(path, d)
        with patch('earnings_delivery.subprocess.run') as run:
            self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['state'], 'unknown')
        run.assert_not_called()

    def test_changed_destination_or_body_rejected(self):
        path = self.prepare()
        d = read_json(path); d['session'] = 'other-workspace'; atomic_write_json(path, d)
        with self.assertRaisesRegex(ValueError, 'destination'):
            deliver(self.root, path, self.deployment_path, execute=True)
        d['session'] = 'verified-session'; atomic_write_json(path, d)
        (self.root / d['body_path']).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'body hash'):
            deliver(self.root, path, self.deployment_path, execute=True)

    def test_only_one_normal_notification_per_day(self):
        first = self.prepare('one'); second = self.prepare('two')
        with patch('earnings_delivery.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'ok', b'')) as run:
            deliver(self.root, first, self.deployment_path, execute=True)
            self.assertEqual(deliver(self.root, second, self.deployment_path, execute=True)['state'], 'deferred')
        self.assertEqual(run.call_count, 1)

    def test_explicit_heartbeat_does_not_use_daily_delivery_slot(self):
        heartbeat = prepare_notification(self.root, self.deployment, day='2026-09-15', body='16:00 heartbeat',
            report_versions=[], kind='heartbeat', rationale='explicit user request', should_send=True)
        daily = self.prepare('normal daily report')
        with patch('earnings_delivery.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'ok', b'')) as run:
            self.assertEqual(deliver(self.root, heartbeat, self.deployment_path, execute=True)['state'], 'sent')
            self.assertEqual(deliver(self.root, heartbeat, self.deployment_path, execute=True)['status'], 'skipped')
            self.assertEqual(deliver(self.root, daily, self.deployment_path, execute=True)['state'], 'sent')
        self.assertEqual(run.call_count, 2)

    def test_missing_sender_fails_before_send_and_can_retry_delivery_only(self):
        path = self.prepare(); self.binary.unlink()
        self.assertEqual(deliver(self.root, path, self.deployment_path, execute=True)['state'], 'retryable_failed')
        self.assertEqual(read_json(path)['attempts'], 0)

    def test_traversal_and_modified_report_rejected(self):
        path = self.prepare(); d = read_json(path); d['body_path'] = '../../outside'; atomic_write_json(path, d)
        with self.assertRaisesRegex(ValueError, 'outside allowed'):
            deliver(self.root, path, self.deployment_path)
        with self.assertRaisesRegex(ValueError, 'outside allowed'):
            prepare_notification(self.root, self.deployment, day='test', body='report', kind='daily', rationale='test',
                should_send=True, report_versions=[{'path': '../outside', 'sha256': 'x'}])


if __name__ == '__main__':
    unittest.main()
