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
