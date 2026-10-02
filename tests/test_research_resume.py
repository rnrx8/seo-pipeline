import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_evidence_policy import fixture
from pipeline import research_verification as r


def message(value):
    return SimpleNamespace(stop_reason="end_turn",content=[SimpleNamespace(text=json.dumps(value))],
                           usage=SimpleNamespace(input_tokens=3,output_tokens=4))


class ResearchResumeTests(unittest.TestCase):
    def run_case(self, change=None, unresolved=False):
        plan,pages,result=fixture()
        if unresolved:result['items'][0].update(status='unresearched',basis='unresolved')
        saved={};calls=[]
        def create(*args,**kw):
            calls.append(kw)
            return message(result if '今回は対象別' in kw['system'] else {
                'coverage_sufficient':not unresolved,'coverage_reason':'test'})
        def save(**kw):saved[kw['step']]=copy.deepcopy(kw);return kw
        with patch.object(r,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))), \
             patch.object(r,'upsert_artifact',side_effect=save),patch.object(r,'create_with_retry',side_effect=create):
            r.audit_matrix('j',None,plan,pages,'facts','model',100)
            if change:change(plan,pages,saved)
            final,usage=r.audit_matrix('j',None,plan,pages,'facts','model',100)
        return calls,final,usage

    def test_same_request_reuses_subject_but_rechecks_overall(self):
        calls,_,usage=self.run_case()
        self.assertEqual(len(calls),3)
        self.assertEqual(usage.input_tokens,3)
        self.assertEqual(usage.output_tokens,4)

    def test_failed_check_is_preserved_not_promoted_to_pass(self):
        calls,value,_=self.run_case(unresolved=True)
        self.assertEqual(len(calls),3)
        self.assertFalse(value['items'][0]['verified'])
        self.assertFalse(value['coverage_sufficient'])

    def test_changed_source_or_plan_requires_fresh_review(self):
        for change in (lambda p,s,c:s[0].update(text='new source'),
                       lambda p,s,c:p['items'][0].update(question='別の条件')):
            with self.subTest(change=change):
                calls,_,_=self.run_case(change)
                self.assertEqual(len(calls),4)

    def test_tampered_or_legacy_result_is_not_reused(self):
        for change in (lambda p,s,c:c['research_check_1'].update(content_text='{}'),
                       lambda p,s,c:c['research_check_1'].update(meta={})):
            with self.subTest(change=change):
                calls,_,_=self.run_case(change)
                self.assertEqual(len(calls),4)
