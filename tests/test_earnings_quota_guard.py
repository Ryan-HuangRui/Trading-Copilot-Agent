import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'script'))
from earnings_quota_guard import evaluate, refresh, require_quota, POLICY, STATUS

class QuotaGuardTests(unittest.TestCase):
    def result(self,used,secondary=None):
        d={'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':used,'windowDurationMins':10080}}}
        if secondary is not None:d['rateLimits']['secondary']={'usedPercent':secondary,'windowDurationMins':300}
        return d
    def test_strict_threshold_and_multiple_windows(self):
        self.assertFalse(evaluate(self.result(40),60)['pause_required'])
        self.assertTrue(evaluate(self.result(41),60)['pause_required'])
        self.assertTrue(evaluate(self.result(3,50),60)['pause_required'])
    def test_failed_query_pauses_without_fabricating_balance(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);p=root/POLICY;p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'enabled':True,'minimum_remaining_percent':60}))
            with patch('earnings_quota_guard.read_limits',side_effect=TimeoutError):
                with self.assertRaisesRegex(RuntimeError,'model_quota_exhausted'):require_quota(root,'codex')
            self.assertIsNone(json.loads((root/STATUS).read_text())['remaining_percent'])
            with patch('earnings_quota_guard.read_limits',return_value=self.result(3)):
                self.assertFalse(require_quota(root,'codex')['pause_required'])
    def test_disabled_does_not_contact_account(self):
        with TemporaryDirectory() as temp,patch('earnings_quota_guard.read_limits') as read:
            self.assertFalse(refresh(Path(temp),'codex')['pause_required']);read.assert_not_called()
    def test_invalid_or_missing_usage_is_not_zero(self):
        for value in [None,{}, {'rateLimits':{'primary':{'usedPercent':True}}}]:
            with self.assertRaises((ValueError,AttributeError)):evaluate(value,60)

    def test_all_role_launchers_refuse_new_calls_below_reserve(self):
        from earnings_role_runner import run_role
        from earnings_gap_review_runner import run_gap_review
        from earnings_publication_runner import _codex
        with TemporaryDirectory() as temp:
            root=Path(temp);p=root/POLICY;p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'enabled':True,'minimum_remaining_percent':60}))
            missing=root/'missing.json'
            calls=[lambda:run_role(root,missing,binary='/unused',timeout=1),
                   lambda:run_gap_review(root,missing,binary='/unused',profile={},timeout=1,attempt_dir=root/'attempt'),
                   lambda:_codex(root,Path('/unused'),{},'',missing,missing,missing,1)]
            with patch('earnings_quota_guard.read_limits',return_value=self.result(41)), \
                 patch('subprocess.Popen') as spawn:
                for call in calls:
                    with self.assertRaisesRegex(RuntimeError,'user reserve guard'):call()
                spawn.assert_not_called()
