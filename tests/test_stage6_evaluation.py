"""Protect the local paid-trial recorder from losing tool conversations."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from anthropic.types import ThinkingBlock, ToolUseBlock

path=Path(__file__).resolve().parents[1]/'evaluations/product-quality/run_isolated.py'
spec=importlib.util.spec_from_file_location('stage6_local_recorder',path)
recorder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


class LocalRecorderTests(unittest.TestCase):
    def trial(self,folder,status='accounted'):
        root=Path(folder)
        recorder.dump(root/'manifest.json',{'cap_usd':12.274548,'settings_sha256':'frozen','jpy_per_usd_for_planning':200})
        recorder.dump(root/'status.json',{'status':'stopped'})
        recorder.dump(root/'ledger/job.json',{'calls':[{'status':status,'reserved_usd':1}],
            'limit_usd':12.274548,'total_limit_usd':12.274548,
            'review_policy':{'phase_limits_usd':{'outline':12.274548,'article':12.274548}}})
        return root

    def test_authorized_trial_extension_preserves_history_and_phase_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.trial(folder,'unknown_cost_reserved')
            before=json.loads((root/'ledger/job.json').read_text())
            recorder.extend_trial_budget(root,5,'うん、大丈夫')
            after=json.loads((root/'ledger/job.json').read_text())
            manifest=json.loads((root/'manifest.json').read_text())
            self.assertEqual(after['calls'],before['calls'])
            self.assertEqual(after['review_policy'],before['review_policy'])
            self.assertEqual(after['total_limit_usd'],17.274548)
            self.assertEqual(manifest['review_phase_limit_usd'],12.274548)
            self.assertEqual(manifest['cap_usd'],17.274548)
            with self.assertRaisesRegex(RuntimeError,'already recorded'):
                recorder.extend_trial_budget(root,5,'duplicate')

    def test_pending_request_prevents_extension_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.trial(folder,'pending')
            before=(root/'ledger/job.json').read_text()
            with self.assertRaisesRegex(RuntimeError,'Pending requests'):
                recorder.extend_trial_budget(root,5,'approved')
            self.assertEqual((root/'ledger/job.json').read_text(),before)
            self.assertFalse((root/'approved-qa-budget-extension.json').exists())

    def test_sdk_tool_and_thinking_blocks_survive_local_recording(self):
        blocks=[ThinkingBlock(type='thinking',thinking='fixture reasoning',signature='fixture-signature'),
                ToolUseBlock(type='tool_use',id='fixture-tool',name='fetch_current_page',input={'url':'https://example.test/page'})]
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'request.json'
            recorder.dump(dest,{'messages':[{'role':'assistant','content':blocks}]})
            saved=json.loads(dest.read_text())['messages'][0]['content']
        self.assertEqual([b['type'] for b in saved],['thinking','tool_use'])
        self.assertEqual(saved[1]['input']['url'],'https://example.test/page')
        self.assertEqual(saved[0]['signature'],'fixture-signature')

    def test_sdk_request_fingerprint_matches_its_serialized_receipt(self):
        request={'messages':[{'role':'assistant','content':[ThinkingBlock(type='thinking',thinking='fixture',signature='sig'),
                  ToolUseBlock(type='tool_use',id='tool',name='fetch_current_page',input={'url':'https://example.test'})]}]}
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'request.json';recorder.dump(dest,request)
            self.assertEqual(recorder.sha(request),recorder.sha(json.loads(dest.read_text())))
