"""Draft permission is distinct from editorial/publication approval."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from pipeline import draft_readiness as ready, content_quality as cq, editorial_blueprint as bp
from pipeline import quality_warnings as warnings, quality_context
from pipeline.content_edits import content_blocks
from test_editorial_blueprint import fixture,artifact


class DraftReadinessTests(unittest.TestCase):
    def setUp(self):
        self.plan,pages,self.matrix,self.ctx,self.value=fixture()
        self.store={'outline':artifact(self.value,self.ctx),'content_contract':{'content_text':'{"required_sections":[]}'},
                    'fact_sheet':{'content_text':'checked facts'},'fresh_sources':{'content_text':json.dumps(pages)}}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=self.plan))
        self.stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=self.matrix))

    def evaluate(self):
        return ready.evaluate('job','条件',load=lambda j,k:self.store.get(k),job={})

    def test_old_failed_model_qa_does_not_trigger_new_model_or_approve_publication(self):
        self.store['research_validation']={'content_text':'{"valid":false,"error":"invalid quote"}'}
        before=copy.deepcopy(self.store)
        with patch.object(cq,'create_with_retry',side_effect=AssertionError('No paid outline QA')):
            report=self.evaluate()
        self.assertTrue(report['ready_for_draft']);self.assertFalse(report['publication_approved'])
        self.assertNotIn('valid',report);self.assertEqual(before,self.store)
        with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(report,'current',stage='article')

    def test_current_matrix_is_strict_even_inside_warning_stage(self):
        from pipeline.research_requirements import require_matrix
        def strict_probe(*a):
            self.assertFalse(warnings.enabled())
            raise cq.ContentQualityError('current facts unavailable')
        with patch('pipeline.research_requirements.require_matrix',side_effect=strict_probe),patch.object(warnings,'warn') as warn:
            with self.assertRaises(ready.DraftReadinessError):warnings.scoped(lambda *a:self.evaluate(),'research_validation')('job')
        warn.assert_not_called();self.assertFalse(warnings.enabled())

    def test_missing_input_and_changed_outline_cannot_start_writer(self):
        for key in ('content_contract','fact_sheet','fresh_sources'):
            old=self.store.pop(key)
            with self.subTest(key=key),self.assertRaises(ready.DraftReadinessError):self.evaluate()
            self.store[key]=old
        old=copy.deepcopy(self.store['outline']);self.store['outline']['content_text']+='detached changes'
        with self.assertRaises(ready.DraftReadinessError):self.evaluate()
        self.store['outline']=old

    def test_input_binding_is_stable_and_changes_with_actual_settings(self):
        a=self.evaluate();self.assertEqual(a,self.evaluate())
        b=ready.evaluate('job','条件',load=lambda j,k:self.store[k],job={'target_audience':'different reader'})
        self.assertNotEqual(a['input_sha256'],b['input_sha256'])

    def test_failed_receipt_save_does_not_complete_guard(self):
        with patch.object(ready,'upsert_artifact',side_effect=ConnectionError('save unavailable')):
            with self.assertRaises(ConnectionError):ready.run('job','条件',load=lambda j,k:self.store[k],job={})

    def test_nonstructural_edit_preserves_question_refs_budget_and_hierarchy(self):
        s=self.value['sections'][0]
        for key,new in [('question','different scope'),('need_ids',[]),('decision_ids',[]),('research_ids',[]),('budget',{**s['budget'],'chars':1})]:
            changed=copy.deepcopy(s);changed[key]=new;changed['conclusion']='clearer answer'
            response={'edits':[{'id':s['id'],'section':changed}],'delete_ids':[],'section_order':[]}
            with self.subTest(key=key),self.assertRaises(cq.ContentQualityError):
                bp.apply_edits(self.value,response,self.ctx,structural=False,targets=[s['id']])

    def test_local_coverage_does_not_unlock_all_assigned_or_linked_sections(self):
        sections=self.value['sections']
        sections.append({**copy.deepcopy(sections[0]),'id':'s02','title':'次の判断'})
        a=artifact(self.value,self.ctx)
        loc=next(b['id'] for b in content_blocks(a['content_text']) if sections[0]['conclusion'] in b['text'])
        report={'checks':[{'key':'coverage','status':'fail','affected_blocks':[{'id':loc}]}],
                'blueprint_issues':[{'section_ids':['s01','s02'],'repair_kind':'editorial'}]}
        self.assertFalse(bp.structural_edit_allowed(report));self.assertEqual(bp.repair_targets(a,report),['s01'])

    def test_provider_failure_stops_all_verification_stages_with_original_error(self):
        for stage in warnings.SOFT_STAGES:
            original=cq.ContentQualityError('insufficient_quota')
            def fail(*a):raise original
            with self.subTest(stage=stage),patch.object(warnings,'warn') as warn:
                with self.assertRaises(cq.ContentQualityError) as err:warnings.scoped(fail,stage)('job')
                self.assertIs(err.exception,original);warn.assert_not_called()

    def test_projection_keeps_unknown_semantic_fields_and_original_matrix(self):
        c={'id':'q.c','text':'claim','conditions':['condition'],'future_semantics':{'limit':7},
           'evidence':[{'url':'https://a.example','quote':'raw quote','reviewed_at':'today'}],
           'conflicting_evidence':[{'url':'https://b.example','quote':'conflict quote'}]}
        requirements={'research_decisions':{'valid':True,'evidence_ledger':{'duplicate':True},
            'items':[{'id':'q','claim_format':'claim-evidence-v1','claim_states':[c],
                      'claims':[{'old':'candidate'}],'claim_evidence':{'duplicate':True},'future_item_limit':'keep'}]}}
        before=copy.deepcopy(requirements);out=quality_context.decision_brief(requirements)['research_decisions']
        self.assertEqual(requirements,before);projected=out['items'][0]['claims'][0]
        self.assertEqual(projected['future_semantics'],c['future_semantics']);self.assertEqual(projected['conditions'],c['conditions'])
        self.assertEqual(projected['evidence'],[{'url':'https://a.example','reviewed_at':'today'}])
        self.assertEqual(projected['conflicting_evidence'],[{'url':'https://b.example'}])
        self.assertEqual(out['items'][0]['future_item_limit'],'keep');self.assertNotIn('evidence_ledger',out)

    def test_unknown_claim_format_retains_raw_evidence_structure(self):
        value={'research_decisions':{'items':[{'id':'q','claim_format':'future','claim_states':[{'text':'new'}],
                    'claim_evidence':{'new':'keep'},'claims':[{'candidate':'keep'}]}],'evidence_ledger':{'new':'keep'}}}
        self.assertEqual(quality_context.decision_brief(value),value)
