import copy
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'evaluations/tiered-facts/shared_context.py'
spec=importlib.util.spec_from_file_location('shared_context_prototype',path)
shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)


class SharedContextTests(unittest.TestCase):
    def test_repeated_evidence_and_conditions_are_exactly_reversible(self):
        quote='月額1,000円。12ヶ月分を一括決済。女性のみ無料。\n'*20
        page={'url':'https://example.test/price','fetched_at':'2026-10-03','truncated':False,
              'excerpts':[{'source_ref':'s1','text':quote}]}
        value=[{'scope':'A','sources':[page],'question':'総額はいくらか'},
               {'scope':'B','sources':[copy.deepcopy(page)],'question':'無料範囲は何か'}]
        original=copy.deepcopy(value)
        packed=shared.pack(value)
        self.assertTrue(packed['definitions'])
        self.assertEqual(shared.unpack(packed),original)
        self.assertEqual(value,original)
        self.assertEqual(shared.pack(value),packed)

    def test_local_source_ids_do_not_merge_different_source_texts(self):
        value=[{'source_ref':'s1','quote':'Aの税込価格。'*40},
               {'source_ref':'s1','quote':'Bの税抜価格。'*40}]
        self.assertEqual(shared.unpack(shared.pack(value)),value)
        changed=copy.deepcopy(value);changed[0]['quote']='改定後の価格。'*40
        self.assertEqual(shared.unpack(shared.pack(changed)),changed)
        self.assertNotEqual(shared.pack(value),shared.pack(changed))

    def test_reserved_input_keys_unknown_refs_and_cycles_are_rejected(self):
        with self.assertRaises(ValueError):shared.pack({'shared_context_ref':'user-controlled'})
        with self.assertRaises(KeyError):shared.unpack({'definitions':{},'requests':{'shared_context_ref':'missing'}})
        with self.assertRaises(ValueError):
            shared.unpack({'definitions':{'d1':{'shared_context_ref':'d1'}},'requests':{'shared_context_ref':'d1'}})
