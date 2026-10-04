import copy
import json
import unittest
from unittest.mock import patch

from pipeline import research_collection as collection
from pipeline import research_requirements as requirements
from pipeline.content_quality import ContentQualityError
from test_evidence_policy import fixture


class CollectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.plan,_,_=fixture()
        self.plan['items'].append({**self.plan['items'][0],'id':'q2','subject':'B'})
        self.records={
            'research_collection_1':{'content_text':'existing A notes','meta':{'subject':'A','question_ids':['q1']}},
            'research_collection_2':{'content_text':'','meta':{'subject':'B','question_ids':['q2']}},
            'research_supplement':{'content_text':json.dumps({'status':'completed','request_sha256':'spent'})}}
        self.original=copy.deepcopy(self.records)

    def loader(self,job,step):
        return copy.deepcopy(self.records.get(step))

    def save(self,**kw):
        self.records[kw['step']]=copy.deepcopy(kw)

    def test_only_empty_original_questions_recovered_without_resetting_supplement(self):
        def run(job,keyword,**kw):
            self.assertEqual([g['id'] for g in json.loads(kw['research_gaps'])],['q2'])
            self.records['research_collection_2']['content_text']='B recovered raw notes'
        with patch.object(collection,'get_optional_artifact',side_effect=self.loader), \
             patch.object(collection,'upsert_artifact',side_effect=self.save), \
             patch('pipeline.step_fact_sheet.run',side_effect=run) as run_mock:
            self.assertTrue(collection.recover_empty_collections('j','keyword',self.plan))
            self.assertFalse(collection.recover_empty_collections('j','keyword',self.plan))
        self.assertEqual(run_mock.call_count,1)
        self.assertEqual(self.records['research_supplement'],self.original['research_supplement'])
        self.assertEqual(self.records['research_collection_1'],self.original['research_collection_1'])
        self.assertNotIn('research_matrix',self.records)

    def test_failed_recovery_not_automatically_repeated(self):
        with patch.object(collection,'get_optional_artifact',side_effect=self.loader), \
             patch.object(collection,'upsert_artifact',side_effect=self.save), \
             patch('pipeline.step_fact_sheet.run',side_effect=RuntimeError('interrupted')) as run:
            with self.assertRaises(RuntimeError):collection.recover_empty_collections('j','keyword',self.plan)
            with self.assertRaises(ContentQualityError):collection.recover_empty_collections('j','keyword',self.plan)
        self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads(self.records['research_collection_recovery']['content_text'])['status'],'failed')

    def test_unknown_empty_question_does_not_trigger_broader_research(self):
        for meta in ({'subject':'B'}, {'subject':'B','question_ids':['deleted']},
                     {'subject':'A','question_ids':['q2']}):
            self.records['research_collection_2']['meta']=meta
            with patch.object(collection,'get_optional_artifact',side_effect=self.loader), \
                 patch('pipeline.step_fact_sheet.run') as run:
                with self.assertRaises(ContentQualityError):collection.recover_empty_collections('j','keyword',self.plan)
                run.assert_not_called()

    def test_collection_failure_precedes_matrix_review_and_source_refresh(self):
        with patch.object(requirements,'load_plan',return_value=self.plan), \
             patch.object(requirements,'get_optional_artifact',side_effect=self.loader), \
             patch.object(collection,'upsert_artifact',side_effect=self.save), \
             patch('pipeline.step_fact_sheet.run',side_effect=RuntimeError('empty task failed')), \
             patch.object(requirements,'refresh_dynamic_sources') as refresh, \
             patch('pipeline.research_verification.audit_matrix') as audit:
            with self.assertRaises(RuntimeError):requirements.verify('j','keyword')
        refresh.assert_not_called()
        audit.assert_not_called()

    def test_changed_plan_is_not_recovered_as_the_old_request(self):
        self.records['research_collection_2']['meta']['plan_sha256']='previous-plan'
        with patch.object(collection,'get_optional_artifact',side_effect=self.loader), \
             patch('pipeline.step_fact_sheet.run') as run:
            with self.assertRaises(ContentQualityError):collection.recover_empty_collections('j','keyword',self.plan)
        run.assert_not_called()

    def test_recovery_does_not_grant_pass_or_replenish_two_audit_attempts(self):
        from types import SimpleNamespace
        plan,pages,result=fixture()
        self.plan=plan
        self.records['research_collection_1']['content_text']=''
        del self.records['research_collection_2']
        self.records['research_matrix']={'content_text':json.dumps({
            'valid':False,'attempt':2,'gaps':[{'id':'q1','reason':'not enough'}],
            'policy_sha256':requirements.matrix_policy(),
            'plan_sha256':requirements.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True))})}
        result['items'][0].update(status='unresearched',basis='unresolved')
        artifacts={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        def recover(*args,**kwargs):self.records['research_collection_1']['content_text']='recovered notes'
        with patch.object(requirements,'load_plan',return_value=plan), \
             patch.object(requirements,'get_optional_artifact',side_effect=self.loader), \
             patch.object(requirements,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(requirements,'upsert_artifact',side_effect=lambda **kw:self.save(**kw) or kw), \
             patch.object(collection,'upsert_artifact',side_effect=self.save), \
             patch.object(requirements,'get_step_config',return_value=('gpt-6.1-sol',100)), \
             patch.object(requirements,'refresh_dynamic_sources'), \
             patch.object(requirements,'supplement_once') as supplement, \
             patch('pipeline.step_fact_sheet.run',side_effect=recover), \
             patch('pipeline.research_verification.audit_matrix',return_value=(result,SimpleNamespace(input_tokens=0,output_tokens=0))) as audit:
            with self.assertRaises(ContentQualityError):requirements.verify('j','keyword')
        self.assertEqual(audit.call_count,1)
        supplement.assert_not_called()
        matrix=json.loads(self.records['research_matrix']['content_text'])
        self.assertFalse(matrix['valid'])
        self.assertEqual(matrix['attempt'],2)
