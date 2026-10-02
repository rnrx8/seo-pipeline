import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from pipeline import research_collection as collection
from pipeline import research_requirements as requirements
from pipeline.fresh_sources import FreshSources, run_with_fetch
from test_evidence_policy import fixture


class SimplifiedFlowTests(unittest.TestCase):
    def test_same_job_resume_restores_today_but_not_stale_or_blocked_sources(self):
        from pipeline.step_fact_sheet import resumable_pages
        fresh=FreshSources({'never_reference_urls':'https://blocked.example'},[])
        pages=[{'url':'https://a.example','fetched_at':'2026-10-02T20:00:00+00:00','text':'today JST'},
               {'url':'https://old.example','fetched_at':'2026-10-01T20:00:00+00:00','text':'yesterday'},
               {'url':'https://blocked.example','fetched_at':'2026-10-02T20:00:00+00:00','text':'blocked'},
               {'url':'https://unknown.example','text':'no date'}]
        self.assertEqual(resumable_pages({'content_text':json.dumps(pages)},fresh,'2026-10-03'),[pages[0]])

    def test_many_questions_share_one_subject_collection(self):
        plan, _, _ = fixture()
        plan['items'] = [{**plan['items'][0], 'id':f'q{i}'} for i in range(23)]
        tasks = collection.batches(plan)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(tasks[0]['items']), 23)

    def test_completed_subject_survives_interruption_and_rechecks_changed_sources(self):
        plan, pages, _ = fixture()
        plan['items'].append({**plan['items'][0], 'id':'q2', 'subject':'B'})
        fresh = FreshSources({}, [])
        fresh.pages = {p['url']:p for p in pages}
        records = {}
        def save(**kw): records[kw['step']] = copy.deepcopy(kw)
        response = NS(usage=NS(input_tokens=1, output_tokens=1))
        note = '原文の料金 ' + pages[0]['url']
        args = dict(plan=plan, fresh=fresh, system='policy', prompt='same date', model='test', search_tool={})
        with patch.object(collection, 'get_optional_artifact', side_effect=lambda j,s:records.get(s)), \
             patch.object(collection, 'upsert_artifact', side_effect=save), patch.object(fresh, 'save'), \
             patch.object(collection, 'run_with_fetch', side_effect=[(response,note,[],[]),RuntimeError('interrupted')]):
            with self.assertRaises(RuntimeError): collection.collect('j',None,**args)
        self.assertNotIn('research_collection_manifest', records)
        fresh.pages[pages[0]['url']]['fetched_at'] = 'same day refetch'
        with patch.object(collection,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(collection,'upsert_artifact',side_effect=save),patch.object(fresh,'save'), \
             patch.object(collection,'run_with_fetch',return_value=(response,note,[],[])) as run:
            collection.collect('j',None,**args)
            self.assertEqual(run.call_count,1)  # only unfinished B
            self.assertIn('"subject": "B"', run.call_args.kwargs['prompt'])
            fresh.pages[pages[0]['url']]['text'] = '価格が改定された'
            collection.collect('j',None,**args)
            self.assertEqual(run.call_count,3)  # both use changed evidence

    def test_collection_migration_excludes_old_extra_slots(self):
        records = {'research_collection_manifest':{'content_text':json.dumps({'steps':['research_collection_1']})},
            'research_collection_1':{'content_text':'new'},'research_collection_20':{'content_text':'obsolete'}}
        self.assertEqual(collection.collection_records('j',lambda j,s:records.get(s)),[records['research_collection_1']])

    def test_invalid_scope_never_launches_collection(self):
        plan, _, _ = fixture()
        for gaps in ([{'id':'overall'}],[{'id':'unknown'}],[{'key':'coverage'}]):
            with patch.object(collection,'run_with_fetch') as fetch:
                with self.assertRaises(ValueError):
                    collection.collect('j',None,plan=plan,fresh=FreshSources({},[]),system='',prompt='',model='test',search_tool={},gaps=json.dumps(gaps))
                fetch.assert_not_called()

    def test_supplement_cannot_gain_new_allowance_on_resume_or_plan_change(self):
        plan, _, _ = fixture(); records = {}
        def save(**kw): records[kw['step']] = kw
        gaps = [{'id':'q1','reason':'price'}]
        with patch.object(requirements,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(requirements,'upsert_artifact',side_effect=save), patch('pipeline.step_fact_sheet.run') as run:
            requirements.supplement_once('j','keyword',plan,gaps)
            requirements.supplement_once('j','keyword',plan,gaps)
            self.assertEqual(run.call_count,1)
            with self.assertRaises(ValueError): requirements.supplement_once('j','keyword',plan,[{'id':'q1','reason':'other'}])
            changed=copy.deepcopy(plan);changed['items'][0]['question']='new'
            with self.assertRaises(ValueError): requirements.supplement_once('j','keyword',changed,gaps)
            self.assertEqual(run.call_count,1)

    def test_optional_question_can_be_retired_but_required_answer_cannot_be_lost(self):
        plan, _, _ = fixture()
        plan['items'].append({**plan['items'][0],'id':'optional','required':False,'priority':'supporting'})
        base={'updates':[],'additions':[],'candidate_services':['A'],'scope_reason':'A'}
        removed=requirements.apply_plan_repair(plan,{**base,'removals':[{'id':'optional','reason':'検索意図に不要な細目','replaced_by':[]}]})
        self.assertEqual([q['id'] for q in removed['items']],['q1'])
        for removal in ({'id':'q1','reason':'調べてもない','replaced_by':[]},
                        {'id':'optional','reason':'','replaced_by':[]}):
            with self.assertRaises(ValueError): requirements.apply_plan_repair(plan,{**base,'removals':[removal]})
        with self.assertRaises(ValueError):
            requirements.apply_plan_repair(plan,{**base,'updates':[{**plan['items'][0],'required':False,'priority':'supporting'}]})

    def test_required_merger_needs_existing_required_same_subject_replacement(self):
        plan, _, _ = fixture()
        plan['items'].append({**plan['items'][0],'id':'q2'})
        base={'updates':[],'additions':[],'candidate_services':['A'],'scope_reason':'A',
              'removals':[{'id':'q1','reason':'重複を統合、独立計画レビューで意味を確認','replaced_by':['q2']}]}
        self.assertEqual(len(requirements.apply_plan_repair(plan,base)['items']),1)
        plan['items'][1]['subject']='B'
        with self.assertRaises(ValueError):requirements.apply_plan_repair(plan,base)

    def test_final_retrieval_turn_finishes_without_new_tool_work_and_duplicate_body(self):
        fresh = FreshSources({},[])
        page={'url':'https://a.example','status':'success','text':'same source '*100}
        def response(blocks,stop):return NS(content=blocks,stop_reason=stop,usage=NS(input_tokens=1,output_tokens=1))
        fetch=lambda i:response([NS(type='tool_use',name='fetch_current_page',id=i,input={'url':page['url']})],'tool_use')
        calls=[]
        responses=iter([fetch('1'),fetch('2'),response([NS(type='text',text='不足は不足として記録')],'end_turn')])
        def create(*a,**kw):calls.append(copy.deepcopy(kw));return next(responses)
        with patch.object(fresh,'fetch',return_value=page):
            run_with_fetch(None,create=create,model='test',max_tokens=100,system='',prompt='',search_tool={},fresh=fresh,max_rounds=3)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[-1]['tool_choice'],{'type':'none'})
        repeated=json.loads(calls[-1]['messages'][-1]['content'][0]['content'])
        self.assertTrue(repeated['already_in_context'])
        self.assertNotIn(page['text'],repeated['text'])
