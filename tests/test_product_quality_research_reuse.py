"""Raw-input reuse cannot inherit acceptance or disable ordinary gap collection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'evaluations/product-quality/research_reuse.py'
spec=importlib.util.spec_from_file_location('product_quality_research_reuse',path)
reuse=importlib.util.module_from_spec(spec);spec.loader.exec_module(reuse)


class ResearchReuseTests(unittest.TestCase):
    def source(self,root):
        (root/'artifacts').mkdir();settings={'source_job':{'main_keyword':'探し方'}}
        (root/'settings.json').write_text(json.dumps(settings))
        page={'url':'https://example.com/source','status':'success','text':'原文の条件と特徴',
              'fetched_at':'2026-10-18T00:00:00+00:00','evidence_quotes':['旧レビューの選択箇所']}
        values={'serp':'検索結果','competitor_evidence':'{}','fresh_sources':json.dumps([page]),
                'research_draft':'原文に照らして再確認する調査メモ',
                'research_collection_manifest':json.dumps({'steps':['research_collection_1']}),
                'research_collection_1':'原文のメモ','research_matrix':'{"valid":true}', 'article':'旧記事'}
        for step,text in values.items():
            (root/'artifacts'/(step+'.json')).write_text(json.dumps({'job_id':'old','step':step,
                'content_type':'text/plain','content_text':text,'meta':{'subject':'共通','plan_sha256':'old-pass','source_urls':[page['url']]}}))
        return settings,page

    def test_only_raw_inputs_survive_without_old_verdicts_or_changed_source_dates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings,page=self.source(root);spec=reuse.prepare(root,settings,'2026-10-18');saved={}
            reuse.seed(spec,job_id='new',save=lambda **kw:saved.update({kw['step']:kw}))
            self.assertNotIn('article',saved);self.assertNotIn('research_matrix',saved)
            copied=json.loads(saved['fresh_sources']['content_text'])[0]
            self.assertEqual(copied['text'],page['text']);self.assertEqual(copied['fetched_at'],page['fetched_at'])
            self.assertNotIn('evidence_quotes',copied)
            self.assertNotIn('plan_sha256',saved['research_collection_1']['meta'])
            self.assertFalse(saved['fact_sheet']['meta']['research_reuse']['acceptance_inherited'])
            self.assertEqual(saved['fact_sheet']['meta']['research_reuse']['source_step'],'research_draft')

    def test_stale_sources_or_changed_settings_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings,_=self.source(root)
            with self.assertRaises(ValueError):reuse.prepare(root,settings,'2026-10-19')
            with self.assertRaises(ValueError):reuse.prepare(root,{},'2026-10-18')

    def test_changed_frozen_input_is_rejected_before_any_seeding(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings,_=self.source(root);spec=reuse.prepare(root,settings,'2026-10-18')
            (root/'artifacts/research_draft.json').write_text('{}');saved=[]
            with self.assertRaises(ValueError):reuse.seed(spec,job_id='new',save=lambda **kw:saved.append(kw))
            self.assertEqual(saved,[])

    def test_diagnosed_resume_cannot_reset_holds_change_cap_or_resend_failed_request(self):
        location=path.with_name('run_isolated.py')
        loader=importlib.util.spec_from_file_location('product_quality_run_isolated',location)
        runner=importlib.util.module_from_spec(loader);loader.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'requests').mkdir()
            request={'model':'trial','output_config':{'bad':'old'}}
            (root/'requests/0001.json').write_text(json.dumps(request))
            snapshot={'files':{'pipeline/a.py':'current'}}
            (root/'backend-source-manifest.json').write_text(json.dumps(snapshot))
            previous={'status':'stopped','cost_usd':1};calls=[{'status':'unknown_cost_reserved','reserved_usd':1}]
            diagnosis={'previous_status_sha256':runner.sha(previous),'retained_calls_sha256':runner.sha(calls),
                'resume_from':'research_plan','cap_usd':2,'current_pipeline_sha256':runner.sha(snapshot),
                'reason':'Diagnosed producer schema error','blocked_original_request_hashes':[runner.sha(request)]}
            runner.validate_resume_diagnosis(root,diagnosis,previous,calls,'research_plan',2)
            for broken in ({**diagnosis,'cap_usd':3},{**diagnosis,'retained_calls_sha256':'reset'},
                           {**diagnosis,'blocked_original_request_hashes':[]}):
                with self.assertRaises(RuntimeError):runner.validate_resume_diagnosis(root,broken,previous,calls,'research_plan',2)

    def test_reused_acquisition_does_not_bypass_normal_targeted_supplement(self):
        from pipeline import step_serp,step_fact_sheet
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings,_=self.source(root);spec=reuse.prepare(root,settings,'2026-10-18');saved={};receipts=[]
            with patch.object(step_serp,'run'),patch.object(step_fact_sheet,'run',return_value='normal supplement') as fetch:
                reuse.install(spec,job_id='new',get=lambda job,step:saved[step],
                    save=lambda **kw:saved.update({kw['step']:kw}),receipt=receipts.append)
                step_serp.run('new','探し方');self.assertEqual(len(receipts),1)
                self.assertEqual(step_fact_sheet.run('new','探し方')['step'],'fact_sheet');fetch.assert_not_called()
                self.assertEqual(step_fact_sheet.run('new','探し方',research_gaps='[{"id":"q1"}]',research_question_ids=['q1']),'normal supplement')
                fetch.assert_called_once()
