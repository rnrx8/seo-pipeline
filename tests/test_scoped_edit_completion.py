"""Synthetic transport tests; the real saved candidate is reviewed separately."""
import copy,json,os,unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import scoped_edit_completion as finish,content_quality as cq
from pipeline import focused_quality as fq,article_design as ad,reader_qa,heading_scope
from pipeline.content_edits import content_blocks
from test_editorial_recovery import materials
from reader_journey_fixture import article_journeys,journey
from heading_review_fixture import with_heading_reviews,reviews

@patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
class ScopedEditCompletionTests(unittest.TestCase):
    @patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
    def setUp(self):
        self.article,self.outline,_=materials();text=self.article['content_text'].replace('希望を整理します。\n##','希望を整理します。\n\n##')
        from pipeline.section_identity import bind_sections
        self.article['content_text']=text;self.article['meta']['section_map']=bind_sections(text,self.outline['content_text'])
        design=ad.design_context(self.outline)
        self.ctx=dict(text=text,facts='',sources='',outline=self.outline['content_text'],contract={},requirements={'article_design':design})
        def send(job,step,request):
            payload=json.loads(request['messages'][0]['content'])
            if step.startswith('tiered_article_reader_'):
                rows=article_journeys(design,text,self.outline['content_text'])
                block=next(b for b in content_blocks(text) if b['text']=='希望を整理します。')
                rows['s02']['lived_value'].update(status='fail',reason='既存用途との接続が不足。',evidence=[{'id':block['id'],'quote':block['text']}])
                return {'reader_journeys':{c['id']:rows[c['id']] for c in payload['cases']}},SimpleNamespace(input_tokens=1,output_tokens=1)
            role=step.removeprefix('tiered_article_');role='evidence' if role=='evidence_scope' else role
            result={'checks':[{'key':k,'status':'pass','reason':'合成確認','affected_blocks':[]} for k in fq.ROLES[role]]}
            if role!='evidence':result['editorial_findings']=[]
            if role=='coverage':
                from pipeline.lead_review import KEYS
                result.update(decision_reviews={d['id']:{'status':'pass','reason':'合成確認','missing_requirements':[],'affected_blocks':[]} for d in design['reader_decisions']},
                    lead_review={k:{'status':'pass','reason':'合成確認','affected_blocks':[]} for k in KEYS})
            result=with_heading_reviews(result,request)
            if role=='organization':result['heading_scope_reviews']={p['id']:{c['id']:{'relation':'next_decision','reason':'親の約束が説明より狭い。'} for c in p['children']} for p in payload['heading_scope_groups']}
            return result,SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            self.report=fq.audit_article(None,**self.ctx)
        self.report.update(attempt=1,structural_issues=[])
        p=finish.packet(self.article,self.outline,self.report,self.ctx)
        blocks={b['id']:b['text'] for b in content_blocks(text)}
        self.proposal={'article_edits':[{'id':i,'new':blocks[i]+'使う場面につなげます。'} for i in p['mandatory_reader_block_ids']],
            'scope_promises':copy.deepcopy(p['scope_promises'])}
        self.proposal['scope_promises']['s01']['title']+='と準備'
        self.proposal['scope_promises']['s01']['question']+='準備も確認する。'
        self.proposal['scope_promises']['s01']['conclusion']+='準備も確認します。'
        heading=next(b for b in content_blocks(text) if b['text'].startswith('## '))
        self.proposal['article_edits'].append({'id':heading['id'],'new':heading['text']+'と準備'})

    def verdict(self,v):
        return {'checks':{k:{'status':'pass','reason':'合成した変更影響確認'} for k in finish.layout.KEYS},
            'reader_journeys':{c['id']:journey({b['id']:[b['text']] for b in c['article_blocks']}) for c in v['reader_cases']['cases']},
            'heading_answer_reviews':{h['id']:reviews(v['article_after'])[h['id']] for h in v['heading_answer_entries']},
            'heading_scope_reviews':{p['id']:{c['id']:{'relation':'answer','reason':'修正後の親へ回答。'} for c in p['children']} for p in v['heading_scope_groups']}}

    def completed(self):
        def send(job,name,request,**kwargs):
            return (self.proposal if name.endswith('_repair') else self.verdict(json.loads(request['messages'][0]['content']))),None
        with patch('pipeline.tiered_research.checked_request',side_effect=send) as sent,patch('pipeline.db.upsert_artifact'),patch('pipeline.db.get_optional_artifact',return_value=None),patch('pipeline.tiered_research.cached_result',return_value=None):
            result=finish.complete('job',None,article=self.article,outline=self.outline,report=self.report,context=self.ctx,check_candidate=lambda *a:None)
        self.assertEqual([c.args[1] for c in sent.call_args_list],['tiered_scoped_edit_repair','tiered_scoped_edit_review'])
        return result

    def test_combined_eligibility_retains_both_real_failures(self):
        before=copy.deepcopy((self.report,self.ctx))
        self.assertTrue(finish.eligible(self.article,self.outline,self.report,self.ctx))
        self.assertFalse(finish.reader.eligible(self.report,self.ctx))
        self.assertFalse(finish.layout.base_valid(self.report,self.ctx))
        self.assertEqual((self.report,self.ctx),before)
        self.assertEqual(finish.packet(self.article,self.outline,self.report,self.ctx)['target_section_ids'],['s01'])

    def test_main_answer_source_lead_and_reader_background_are_not_local(self):
        for kind in ('snapshot','source','lead','guidance','attunement','missing_role'):
            r=copy.deepcopy(self.report)
            if kind=='snapshot':r['snapshot']='old'
            elif kind=='source':r['checks'][1]['status']='fail'
            elif kind=='lead':next(iter(r['phases']['coverage']['lead_review'].values()))['status']='fail'
            elif kind=='missing_role':r['phases'].pop('language')
            else:r['phases']['coverage']['reader_progression_reviews']['s02']['journey'][kind]['status']='fail'
            with self.subTest(kind=kind):self.assertFalse(finish.eligible(self.article,self.outline,r,self.ctx))

    def test_patch_cannot_change_facts_order_other_sections_or_hide_reader_finding(self):
        for kind in ('number','new_parent','new_field','missing','lead','split'):
            p=copy.deepcopy(self.proposal)
            if kind=='number':p['article_edits'][0]['new']+='100円。'
            elif kind=='new_parent':p['scope_promises']['s03']=copy.deepcopy(p['scope_promises']['s01'])
            elif kind=='new_field':p['scope_promises']['s01']['research_ids']=[]
            elif kind=='missing':p['article_edits'].pop(0)
            elif kind=='lead':p['article_edits'].append({'id':'block-0002','new':'別の入口。'})
            else:p['article_edits'][0]['new']+='\n\n新段落。'
            with self.subTest(kind=kind),self.assertRaises(cq.ContentQualityError):finish.apply(self.article,self.outline,self.report,self.ctx,p)

    def test_strict_receipt_replays_scope_and_all_changed_reading_contexts(self):
        text,o,b,r=self.completed();ctx=finish.layout.candidate_context(self.ctx,o,b);ctx['text']=text
        cq._require_audit_strict(r,cq.snapshot(**ctx),stage='article',context=ctx)
        self.assertEqual(r['phases'],self.report['phases'])
        for kind in ('reader','scope','finding','fact','phase','foreign'):
            bad=copy.deepcopy(r);c=copy.deepcopy(ctx);v=bad['scoped_edit_completion']['review']
            if kind=='reader':v['reader_journeys'].pop(next(iter(v['reader_journeys'])))
            elif kind=='scope':v['heading_scope_reviews']['s01']['s02']['relation']='next_decision'
            elif kind=='finding':bad['editorial_findings']=[]
            elif kind=='fact':c['facts']='changed'
            elif kind=='foreign':bad['editorial_completion']={'fake':True}
            else:bad['phases']['organization']['checks'][0]['status']='pass'
            with self.subTest(kind=kind),self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(bad,cq.snapshot(**c),stage='article',context=c)

    def test_mechanical_failure_prevents_buying_review(self):
        with patch('pipeline.tiered_research.checked_request',return_value=(self.proposal,None)) as sent,patch('pipeline.db.get_optional_artifact',return_value=None),patch('pipeline.tiered_research.cached_result',return_value=None):
            with self.assertRaises(cq.ContentQualityError):finish.complete('job',None,article=self.article,outline=self.outline,report=self.report,context=self.ctx,
                check_candidate=lambda *a:(_ for _ in ()).throw(cq.ContentQualityError('mechanical fail')))
        self.assertEqual(sent.call_count,1)

    def test_flagged_parent_is_reviewed_even_if_writer_keeps_its_question(self):
        p=copy.deepcopy(self.proposal)
        p['scope_promises']=copy.deepcopy(finish.packet(self.article,self.outline,self.report,self.ctx)['scope_promises'])
        p['scope_promises']['s01']['conclusion']+='準備も扱います。'
        p['article_edits']=[e for e in p['article_edits'] if not e['new'].startswith('## ')]
        t,o,b=finish.apply(self.article,self.outline,self.report,self.ctx,p)
        v,_=finish.view(self.article,self.outline,self.report,self.ctx,p,t,o,b)
        self.assertEqual([g['id'] for g in v['heading_scope_groups']],['s01'])
        verdict=self.verdict(v);verdict['heading_scope_reviews']['s01']['s02']['relation']='next_decision'
        with self.assertRaises(cq.ContentQualityError):finish.require_review(verdict,v,self.report,self.ctx,t)

    def test_shared_budget_and_one_shot_roles_apply_in_production_too(self):
        from pipeline import quality_budget as q
        for role in ('scoped_edit_repair','scoped_edit_review'):
            self.assertEqual(q.review_operation('tiered_'+role),('article',role,1))
            meta={'phase':'article','role':role,'call_limit':1,'request_sha256':'new'}
            old={'review_operation':{**meta,'request_sha256':'old'},'reserved_usd':0,'status':'accounted','cost_usd':0}
            token=q.REQUEST.set(meta)
            try:
                with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'unlimited'}),self.assertRaises(cq.ContentQualityError):q.guard_review({'calls':[old]},0)
            finally:q.REQUEST.reset(token)
