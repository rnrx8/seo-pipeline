"""Dependency execution, same-revision acceptance, and no replay on unknown state."""
import copy
import json
import socket
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import upstream_recovery as u, editorial_recovery as r, content_quality as cq
from test_editorial_recovery import materials, passed


class DependencyRecoveryTests(unittest.TestCase):
    def setUp(self):
        article,outline,report=materials()
        self.store={'article':article,'outline':outline,'content_audit':{'content_text':'{}'},
                    'research_matrix':{'content_text':'old reviewed material'}}
        self.report=report;self.events=[];self.audit_calls=0
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')))
        def read(j,k):return copy.deepcopy(self.store[k])
        def optional(j,k):return copy.deepcopy(self.store.get(k))
        def save(**kw):self.store[kw['step']]=copy.deepcopy(kw);return copy.deepcopy(kw)
        for name,fn in [('get_artifact',read),('get_optional_artifact',optional),('upsert_artifact',save)]:
            self.stack.enter_context(patch.object(r,name,side_effect=fn))
        self.stack.enter_context(patch.object(r,'get_job',return_value={'delivery_type':'full'}))
        self.stack.enter_context(patch.object(r,'audit_context',side_effect=self.context))
        self.stack.enter_context(patch.object(r,'input_signature',side_effect=lambda *a:self.store['research_matrix']['content_text']))

    def context(self,*a):
        return dict(text=self.store['article']['content_text'],facts='',outline=self.store['outline']['content_text'],
                    contract={},requirements={},sources='')

    def execute(self,j,k,key,state,api):
        self.events.append(key)
        if key=='audit':
            self.audit_calls+=1
            if self.audit_calls<=self.fail_count:
                report={**copy.deepcopy(self.report),'snapshot':cq.snapshot(**self.context())}
                self.store['content_audit']={'content_text':json.dumps(report)}
                raise r.UpstreamRepairRequired('saved failed QA',report)
            report=passed(self.context());self.store['content_audit']={'content_text':json.dumps(report)}
            return self.store['content_audit']
        if key=='route':
            routed=copy.deepcopy(state['source_report'])
            routed['editorial_findings'][0]['stage_to_fix']=self.destination
            state.update(routed_report=routed,routing_receipt={'routes':[{'stage':self.destination,'research_ids':['q1']} ]})
        if key=='research':self.store['research_matrix']['content_text']='new targeted reviewed material'

    def test_source_finding_finishes_dependency_chain_without_replaying_initial_qa(self):
        self.fail_count=1;self.destination='research'
        with patch.object(u,'execute',side_effect=self.execute):
            result=u.run('job','kw')
        self.assertEqual(self.events,['audit','route','research','outline','structure_guard','research_validation','article','audit'])
        state=json.loads(self.store['editorial_recovery']['content_text'])
        self.assertEqual(state['status'],'completed');self.assertEqual(state['round'],1)
        self.assertTrue(json.loads(result['content_text'])['valid'])
        with patch.object(u,'execute',side_effect=AssertionError('No repeat after current full QA')):
            u.run('job','kw',state=state)

    def test_saved_fact_explanation_goes_directly_to_body_repair(self):
        self.fail_count=1;self.destination='article'
        with patch.object(u,'execute',side_effect=self.execute):u.run('job','kw')
        self.assertEqual(self.events,['audit','route','audit'])
        self.assertEqual(self.store['research_matrix']['content_text'],'old reviewed material')

    def test_repeated_unresolved_scope_cannot_run_forever(self):
        self.fail_count=10;self.destination='outline'
        with patch.object(u,'execute',side_effect=self.execute):
            with self.assertRaisesRegex(cq.ContentQualityError,'同じ範囲'):u.run('job','kw')
        self.assertEqual(self.events.count('route'),2)
        state=json.loads(self.store['editorial_recovery']['content_text'])
        self.assertEqual(state['status'],'failed');self.assertEqual(state['error'],'same_scope_unresolved')

    def test_uncertain_or_failed_step_is_never_resent(self):
        self.fail_count=0;self.destination='article'
        with patch.object(u,'execute',side_effect=ConnectionError('provider unavailable')):
            with self.assertRaises(ConnectionError):u.run('job','kw')
        state=json.loads(self.store['editorial_recovery']['content_text'])
        for status in ('failed','running'):
            state['status']=status
            with patch.object(u,'execute') as send:
                with self.assertRaises(cq.ContentQualityError):u.run('job','kw',state=state)
            send.assert_not_called()

    def test_pass_does_not_schedule_routing_research_or_writing(self):
        self.fail_count=0
        with patch.object(u,'execute',side_effect=self.execute):u.run('job','kw')
        self.assertEqual(self.events,['audit'])

    def test_only_recorded_source_refresh_can_change_outline_context(self):
        state={'version':u.VERSION,'status':'running','current_step':'outline',
            'routing_receipt':{'routes':[{'stage':'research','research_ids':['q1']}]},
            'expected_hashes':r.current_hashes('job'),'immutable_signature':u.immutable_signature('job')}
        self.store['editorial_recovery']={'content_text':json.dumps(state)}
        u.require_material_refresh('job',self.store['outline'],['q1'])
        for change in ({'current_step':'article'},{'status':'failed'},{'immutable_signature':'changed'}):
            self.store['editorial_recovery']['content_text']=json.dumps({**state,**change})
            with self.subTest(change=change),self.assertRaises(cq.ContentQualityError):
                u.require_material_refresh('job',self.store['outline'],['q1'])
        self.store['editorial_recovery']['content_text']=json.dumps(state)
        with self.assertRaises(cq.ContentQualityError):u.require_material_refresh('job',self.store['outline'],['q2'])

    def test_normal_entry_uses_the_contract_saved_in_production_brief_metadata(self):
        from pipeline.editorial_contract import VERSION,use
        self.store['production_brief']={'content_text':'{}','meta':{'editorial_contract':{'version':VERSION}}}
        with use(None),patch.object(u,'run',return_value='new dependency controller') as execute:
            result=r._run('job','kw')
        self.assertEqual(result,'new dependency controller');execute.assert_called_once_with('job','kw',None)

    def test_assignment_metadata_change_is_not_hidden_by_same_brief_text(self):
        self.store['production_brief']={'content_text':'{}','meta':{'editorial_contract':{'assignment':{'product':'A'}}}}
        before=u.immutable_signature('job')
        self.store['production_brief']['meta']['editorial_contract']['assignment']['product']='B'
        self.assertNotEqual(before,u.immutable_signature('job'))

    def test_factual_refresh_does_not_authorize_heading_restructuring(self):
        report=copy.deepcopy(self.report);report['editorial_findings'][0]['stage_to_fix']='research'
        state={'round':1,'routed_report':report,
            'routing_receipt':{'routes':[{'stage':'research','research_ids':['q1']}]}}
        with patch('pipeline.step_research_guard.repair_outline',return_value='scoped repair') as repair:
            self.assertEqual(u.execute('job','kw','outline',state,None),'scoped repair')
        mapped=repair.call_args.args[2]
        self.assertEqual(mapped['structural_issues'],[])
        self.assertEqual(mapped['blueprint_issues'][0]['repair_kind'],'editorial')
        self.assertEqual(mapped['material_refresh_question_ids'],['q1'])
