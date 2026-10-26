"""Transport/provenance and edit-scope tests; synthetic QA is not prose proof."""
import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import scoped_reader_completion as finish, content_quality as cq
from pipeline import reader_qa, heading_answers, article_design, article_progression
from pipeline import heading_scope, editorial_findings, step_content_audit as step
from pipeline.content_edits import content_blocks
from pipeline.section_identity import bind_sections
import test_stage5_authoring as stage5
from reader_journey_fixture import journey
from heading_review_fixture import reviews


@patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
class ScopedReaderCompletionTests(unittest.TestCase):
    def setUp(self):
        self.report, _, self.ctx = stage5.Stage5AuthoringTests().audit_fixture('tiered','legacy')
        r=self.report;c=self.ctx;t=c['text'];cov=r['phases']['coverage'];org=r['phases']['organization']
        self.targets=[s['opening_block_ids'][0] for s in heading_answers.entries(t)[:2]]
        sid=c['requirements']['article_design']['sections'][1]['id']
        rows=cov['reader_progression_reviews'];row=rows[sid]
        bid=self.targets[1];quote=next(b['text'] for b in content_blocks(t) if b['id']==bid)
        row['journey']['lived_value'].update(status='fail',reason='既存の用途へつなぐ説明が不足',evidence=[{'id':bid,'quote':quote}])
        packets=reader_qa.packets(t,c['outline'],c['requirements']['article_design'])
        for p,batch in zip(packets,cov['reader_review_trace']['batches']):
            values=reader_qa.checked({'reader_journeys':{i:rows[i]['journey'] for i in batch['section_ids']}},p,t)
            batch['reviews']=values;rows.update(values)
        cov['checks']=article_design.checked_result(cov['checks'],cov,c['requirements']['article_design'],t,outline=c['outline'])
        cov['editorial_findings']=article_progression.findings(article_progression.checked(cov,c['requirements']['article_design'],cov['reader_correspondence'],t))
        org['heading_answer_reviews']['heading-0001']['opening'].update(status='fail',reason='既存の答えを第一文へ置く')
        findings=heading_answers.checked(org,t)
        org['checks']=heading_scope.merge(org['checks'],findings);org['editorial_findings']=findings
        r.update(valid=False,attempt=1,structural_issues=[])
        r['editorial_findings']=[{**f,'role':role} for role,p in r['phases'].items() for f in p.get('editorial_findings',[])]
        for check in r['checks']:
            failed=[x for p in r['phases'].values() for x in p['checks'] if x['key']==check['key'] and x['status']=='fail']
            if failed:
                check.update(status='fail',reason='合成した失敗応答',affected_blocks=[loc for x in failed for loc in x['affected_blocks']])
        blocks={b['id']:b['text'] for b in content_blocks(t)}
        self.proposal={'edits':[{'id':i,'new':blocks[i]+'使う場面を具体的に考えましょう。'} for i in self.targets]}

    def verdict(self, view):
        text=view['article_after'];headings={h['id'] for h in view['heading_answer_entries']}
        return {'checks':{k:{'status':'pass','reason':'合成した変更影響確認'} for k in finish.editorial_completion.KEYS},
            'reader_journeys':{c['id']:journey({b['id']:[b['text']] for b in c['article_blocks']}) for c in view['reader_cases']['cases']},
            'heading_answer_reviews':{i:r for i,r in reviews(text).items() if i in headings}}

    def completed(self):
        def send(job, name, request, **kwargs):
            if name.endswith('_repair'):return self.proposal,SimpleNamespace(input_tokens=1,output_tokens=1)
            return self.verdict(json.loads(request['messages'][0]['content'])),SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch('pipeline.tiered_research.checked_request',side_effect=send) as sent,patch('pipeline.tiered_research.cached_result',return_value=None):
            result=finish.complete('job',None,report=self.report,context=self.ctx,check_candidate=lambda t:None)
        self.assertEqual([a.args[1] for a in sent.call_args_list],['tiered_reader_finish_repair','tiered_reader_finish_review'])
        return result

    def test_current_native_failure_is_eligible_and_not_mutated(self):
        before=copy.deepcopy((self.report,self.ctx))
        self.assertTrue(finish.eligible(self.report,self.ctx))
        self.assertEqual(set(finish.packet(self.report,self.ctx)['target_block_ids']),set(self.targets))
        self.assertEqual((self.report,self.ctx),before)

    def test_duplicate_global_opening_finding_stays_bound_to_per_heading_failure(self):
        r=copy.deepcopy(self.report);org=r['phases']['organization']
        duplicate=copy.deepcopy(org['editorial_findings'][0])
        for k in ('origin','heading_id','aspect'):duplicate.pop(k)
        org['editorial_findings'].insert(0,duplicate)
        r['editorial_findings']=[{**f,'role':role} for role,p in r['phases'].items() for f in p.get('editorial_findings',[])]
        self.assertTrue(finish.eligible(r,self.ctx))
        org['editorial_findings'][0]['stage_to_fix']='outline'
        r['editorial_findings']=[{**f,'role':role} for role,p in r['phases'].items() for f in p.get('editorial_findings',[])]
        self.assertFalse(finish.eligible(r,self.ctx))

    def test_source_main_answer_lead_or_structure_failures_are_not_eligible(self):
        for kind in ('source','main','lead','structure','snapshot','missing_role','missing_trace','findings','attunement','guidance','heading','scope','extra_location'):
            r=copy.deepcopy(self.report);c=copy.deepcopy(self.ctx)
            if kind=='source':r['checks'][1]['status']='fail'
            elif kind=='main':r['phases']['coverage']['decision_reviews']['i01']['status']='fail'
            elif kind=='lead':next(iter(r['phases']['coverage']['lead_review'].values()))['status']='fail'
            elif kind=='structure':r['structural_issues']=[{'key':'missing_heading'}]
            elif kind=='snapshot':r['snapshot']='old'
            elif kind=='missing_role':r['phases'].pop('evidence')
            elif kind=='missing_trace':r['phases']['coverage'].pop('reader_review_trace')
            elif kind=='findings':r['editorial_findings']=[]
            elif kind in ('attunement','guidance'):
                r['phases']['coverage']['reader_progression_reviews']['s02']['journey'][kind]['status']='fail'
            elif kind=='heading':r['phases']['organization']['heading_answer_reviews']['heading-0001']['heading']['status']='fail'
            elif kind=='scope':r['editorial_findings'][0]['source_urls']=['https://source.example']
            else:r['checks'][0]['affected_blocks'].append({'id':'block-0000','reason':'追加'})
            with self.subTest(kind=kind):self.assertFalse(finish.eligible(r,c))

    def test_patch_rejects_facts_quotes_numbers_topology_lead_and_missing_edits(self):
        p=finish.packet(self.report,self.ctx)
        for kind in ('number','quote','negation','link','heading','list','split','lead','missing','duplicate','unchanged'):
            v=copy.deepcopy(self.proposal)
            if kind=='number':v['edits'][0]['new']+='100円。'
            elif kind=='quote':v['edits'][0]['new']+='「新しい引用」。'
            elif kind=='negation':v['edits'][0]['new']+='必ず成功します。'
            elif kind=='link':v['edits'][0]['new']+='[出典](https://new.example)'
            elif kind=='heading':v['edits'][0]['new']='### 新しい見出し'
            elif kind=='list':v['edits'][0]['new']='- 新しい項目'
            elif kind=='split':v['edits'][0]['new']+='\n\n別段落。'
            elif kind=='lead':v['edits'][0]['id']='block-0000'
            elif kind=='missing':v['edits'].pop()
            elif kind=='duplicate':v['edits'][1]=copy.deepcopy(v['edits'][0])
            else:v['edits'][0]['new']=next(b['text'] for b in p['article_blocks'] if b['id']==v['edits'][0]['id'])
            with self.subTest(kind=kind),self.assertRaises(cq.ContentQualityError):finish.apply(self.ctx['text'],v,p)

    def test_every_changed_reading_context_is_rechecked_including_neighbors(self):
        p=finish.packet(self.report,self.ctx);t=finish.apply(self.ctx['text'],self.proposal,p)
        v=finish.view(self.report,self.ctx,self.proposal,t)
        self.assertEqual({c['id'] for c in v['reader_cases']['cases']},{'s01','s02','s03'})
        self.assertEqual({h['id'] for h in v['heading_answer_entries']},{'heading-0001','heading-0002'})
        verdict=self.verdict(v);finish.require_review(verdict,v,self.report,self.ctx,t)
        for kind in ('neighbor','reader_fail','wrong_quote','heading_missing','heading_fail','evidence','new_goal'):
            r=copy.deepcopy(verdict)
            if kind=='neighbor':r['reader_journeys'].pop('s03')
            elif kind=='reader_fail':r['reader_journeys']['s02']['lived_value']['status']='fail'
            elif kind=='wrong_quote':r['heading_answer_reviews']['heading-0001']['opening_quote']='違う引用'
            elif kind=='heading_missing':r['heading_answer_reviews'].pop('heading-0001')
            elif kind=='heading_fail':r['heading_answer_reviews']['heading-0001']['opening']['status']='fail'
            else:r['checks']['evidence_preserved' if kind=='evidence' else 'reader_value_preserved']['status']='fail'
            with self.subTest(kind=kind),self.assertRaises(cq.ContentQualityError):finish.require_review(r,v,self.report,self.ctx,t)

    def test_receipt_keeps_original_failures_and_binds_all_material_and_results(self):
        t,r=self.completed();ctx={**self.ctx,'text':t};snap=cq.snapshot(**ctx)
        cq._require_audit_strict(r,snap,stage='article',context=ctx)
        self.assertEqual(r['phases'],self.report['phases'])
        self.assertEqual(r['phases']['coverage']['checks'][0]['status'],'fail')
        self.assertEqual(r['acceptance_mode'],'full_audit_plus_scoped_reader_review')
        for key in ctx:
            changed=copy.deepcopy(ctx)
            changed[key]=changed[key]+'改変' if isinstance(changed[key],str) else {**changed[key],'tampered':True}
            with self.subTest(key=key),self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(r,cq.snapshot(**changed),stage='article',context=changed)
        for kind in ('phase','findings','review','policy','hash','merged_status','foreign_receipt'):
            bad=copy.deepcopy(r);receipt=bad['scoped_reader_completion']
            if kind=='phase':bad['phases']['organization']['checks'][0]['status']='pass'
            elif kind=='findings':bad['editorial_findings']=[]
            elif kind=='review':receipt['review']['checks'].pop('language')
            elif kind=='policy':receipt['policy_sha256']='old'
            elif kind=='hash':receipt['review_input_sha256']='other'
            elif kind=='merged_status':bad['checks'][0]['status']='fail'
            else:bad['editorial_completion']={'fake':True}
            with self.subTest(kind=kind),self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(bad,snap,stage='article',context=ctx)

    def test_invalid_mechanics_prevent_independent_paid_review(self):
        with patch('pipeline.tiered_research.checked_request',return_value=(self.proposal,None)) as sent,patch('pipeline.tiered_research.cached_result',return_value=None):
            with self.assertRaises(cq.ContentQualityError):finish.complete('job',None,report=self.report,context=self.ctx,
                check_candidate=lambda t:(_ for _ in ()).throw(cq.ContentQualityError('mechanical fail')))
        self.assertEqual(sent.call_count,1)

    def test_prose_budget_counts_untouched_neighbors_and_shares_the_room(self):
        text='# 題\n\n## 章\n\n'+'あ'*200+'\n\n'+'い'*70
        blocks=content_blocks(text);a,b=blocks[-2:]
        p={'article_blocks':blocks,'target_block_ids':[b['id']]}
        self.assertEqual(finish.paragraph_limits(text,p),{b['id']:100})
        p['target_block_ids']=[a['id'],b['id']]
        self.assertEqual(finish.paragraph_limits(text,p),{a['id']:215,b['id']:85})

    def test_cached_long_proposal_is_reused_then_only_changed_run_is_shortened(self):
        from pipeline.readability import readability_issues
        long=copy.deepcopy(self.proposal);long['edits'][0]['new']+='長'*400
        calls=[]
        def send(job,name,request,**kwargs):
            calls.append(name)
            if name.endswith('_mechanical'):
                p=json.loads(request['messages'][0]['content'])
                self.assertEqual(p['target_block_ids'],[self.targets[0]])
                return {'edits':[self.proposal['edits'][0]]},None
            self.assertTrue(name.endswith('_review'))
            return self.verdict(json.loads(request['messages'][0]['content'])),None
        def check(text):
            if readability_issues(text):raise cq.ContentQualityError('長い文章')
        with patch('pipeline.tiered_research.checked_request',side_effect=send),patch('pipeline.tiered_research.cached_result',return_value=long):
            text,r=finish.complete('job',None,report=self.report,context=self.ctx,check_candidate=check)
        self.assertEqual(calls,['tiered_reader_finish_mechanical','tiered_reader_finish_review'])
        self.assertEqual(r['scoped_reader_completion']['mechanical_adjustment']['initial_proposal'],long)
        ctx={**self.ctx,'text':text};cq._require_audit_strict(r,cq.snapshot(**ctx),stage='article',context=ctx)
        bad=copy.deepcopy(r);bad['scoped_reader_completion']['mechanical_adjustment']['target_block_ids'].append(self.targets[1])
        with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(bad,cq.snapshot(**ctx),stage='article',context=ctx)

    def test_normal_route_reuses_saved_failure_and_does_not_restart_full_QA(self):
        t,r=self.completed();saved=[];ctx=self.ctx
        binding=bind_sections(ctx['text'],ctx['outline'],ctx['contract'])
        artifacts={key:{'content_text':value,'meta':{}} for key,value in {'fact_sheet':'','fresh_sources':ctx['sources'],'outline':ctx['outline'],
            'content_contract':json.dumps(ctx['contract']),'content_audit':json.dumps(self.report)}.items()}
        artifacts['article']={'content_text':ctx['text'],'meta':{'section_map':binding}}
        def save(**kw):saved.append(kw);return kw
        with patch.object(step,'get_job',return_value={'id':'job'}),patch.object(step,'get_artifact',side_effect=lambda j,k:artifacts[k]),\
            patch.object(step,'upsert_artifact',side_effect=save),patch.object(step,'audit_facts',return_value=ctx['facts']),\
            patch.object(step,'source_evidence',return_value=ctx['sources']),patch.object(step,'final_review_requirements',return_value=ctx['requirements']),\
            patch('pipeline.article_design.bind_review_requirements',side_effect=lambda req,*args:req),\
            patch.object(step,'validate_delivery',return_value=[]),patch.object(step,'readability_issues',return_value=[]),\
            patch.object(step,'validate_structure',return_value=[]),patch.object(step,'cta_placement_issues',return_value=[]),\
            patch.object(step,'audit',side_effect=AssertionError('A third full QA must not run')),\
            patch.object(finish,'complete',return_value=(t,r)) as completed:
            step.run('job','用途')
        self.assertEqual(completed.call_count,1)
        article=next(v for v in saved if v['step']=='article')
        self.assertEqual(article['content_text'],t)
        self.assertEqual(article['meta']['reader_correspondence']['text_sha256'],cq.digest(t))
        review=next(v for v in saved if v['step']=='review')
        self.assertIn('変更段落と影響を受ける章',review['content_text'])

    def test_completion_budget_roles_cannot_repeat_even_in_unlimited_mode(self):
        from pipeline import quality_budget as qb
        for role in ('reader_finish_repair','reader_finish_mechanical','reader_finish_review'):
            self.assertEqual(qb.review_operation('tiered_'+role),('article',role,1))
            meta={'phase':'article','role':role,'call_limit':1,'request_sha256':'new'}
            value={'calls':[{'status':'accounted','cost_usd':.1,'reserved_usd':.1,'review_operation':{**meta,'request_sha256':'previous'}}]}
            token=qb.REQUEST.set(meta)
            try:
                with patch.dict(os.environ,QUALITY_BUDGET_MODE='unlimited'),self.assertRaises(cq.ContentQualityError):qb.guard_review(value,.1)
            finally:qb.REQUEST.reset(token)
