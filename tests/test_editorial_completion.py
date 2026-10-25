"""Bounded layout edits must never launder a failed source/reader audit."""
import copy,json,unittest
from unittest.mock import patch
from pipeline import editorial_completion as finish, content_quality as cq
from pipeline.content_edits import content_blocks
from pipeline.section_identity import bind_sections
from test_editorial_blueprint import fixture,artifact
from quality_fixtures import phases

class EditorialCompletionTests(unittest.TestCase):
    def setUp(self):
        _,_,_,ctx,value=fixture()
        value=copy.deepcopy(value)
        s=copy.deepcopy(value['sections'][0]);s.update(id='s02',parent_id='s01',level=3,title='準備と限界',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        value['sections'].append(s)
        self.outline=artifact(value,ctx)
        self.text='# 記事\n\n## '+value['sections'][0]['title']+'\n\n用途で選びます。\n\n### 準備と限界\n\n図面を用意します。\n\n限界も確認します。'
        self.article={'content_text':self.text,'meta':{'section_map':bind_sections(self.text,self.outline['content_text'])}}
        self.context=dict(text=self.text,facts='根拠',outline=self.outline['content_text'],contract={},requirements={},sources='資料')
        self.bid=content_blocks(self.text)[-1]['id']
        self.report={'valid':False,'stage':'article','snapshot':cq.snapshot(**self.context),'policy_version':cq.POLICY_VERSION,
            'checks':[{'key':k,'status':'fail' if k=='prose_quality' else 'pass','reason':'確認','affected_blocks':[{'id':self.bid,'reason':'区切り'}] if k=='prose_quality' else []} for k in cq.CHECKS],
            'phases':phases(cq.snapshot(**self.context),self.text),'structural_issues':[],
            'editorial_findings':[{'check_key':'prose_quality','severity':'blocking','stage_to_fix':'outline','role':'organization','block_ids':[self.bid],
                'problem':'別の論点','reader_impact':'区切りが不明','required_change':'見出しを合わせる'}]}
        self.report['phases']['organization']['checks'][0].update(status='fail',affected_blocks=[{'id':self.bid,'reason':'区切り'}])
        new=copy.deepcopy(s);new.update(id='s03',level=4,parent_id='s02',title='制約を確認する',question='どの制約を確認するか',conclusion='限界を確認します。')
        self.proposal={'article_edits':[{'id':self.bid,'new':'#### 制約を確認する\n\n限界も確認します。'}],
          'outline_patch':{'edits':[{'id':'s03','section':new}],'delete_ids':[],'section_order':['s01','s02','s03']}}

    def test_five_complete_base_roles_are_required(self):
        self.assertTrue(finish.base_valid(self.report,self.context))
        for role in finish.ROLES:
            r=copy.deepcopy(self.report);r['phases'].pop(role)
            self.assertFalse(finish.base_valid(r,self.context))
        for role in ('evidence','coverage','consistency'):
            r=copy.deepcopy(self.report);r['phases'][role]['checks'][0]['status']='fail'
            r['phases'][role]['checks'][0]['affected_blocks']=[{'id':self.bid,'reason':'欠落'}]
            self.assertFalse(finish.base_valid(r,self.context))

    def test_source_reader_or_unknown_findings_are_not_layout(self):
        for stage in ('research','investigate'):
            r=copy.deepcopy(self.report);r['editorial_findings'][0]['stage_to_fix']=stage
            self.assertFalse(finish.base_valid(r,self.context))
        r=copy.deepcopy(self.report);r['snapshot']='stale';self.assertFalse(finish.base_valid(r,self.context))
        r=copy.deepcopy(self.report);r['structural_issues']=[{'key':'missing_answer'}];self.assertFalse(finish.base_valid(r,self.context))
        r=copy.deepcopy(self.report);r['editorial_findings'][0]['research_ids']=['q01'];self.assertFalse(finish.base_valid(r,self.context))

    def test_insert_related_h4_preserves_body_and_identity(self):
        text,outline,binding=finish.apply_proposal(self.article,self.outline,self.report,self.context,self.proposal)
        self.assertIn('#### 制約を確認する\n\n限界も確認します。',text)
        self.assertEqual([e['blueprint_id'] for e in binding['entries']],['s01','s02','s03'])
        self.assertEqual(self.text.replace('限界も確認します。','#### 制約を確認する\n\n限界も確認します。'),text)

    def test_model_temporary_labels_are_allocated_by_code(self):
        p=copy.deepcopy(self.proposal);e=p['outline_patch']['edits'][0]
        e['id']='child-a';e['section']['id']='child-a';p['outline_patch']['section_order'][-1]='child-a'
        _,_,binding=finish.apply_proposal(self.article,self.outline,self.report,self.context,p)
        self.assertEqual(binding['entries'][-1]['blueprint_id'],'s03')
        self.assertEqual(p['outline_patch']['edits'][0]['id'],'child-a')
        p['outline_patch']['edits'].append(copy.deepcopy(e))
        with self.assertRaises(cq.ContentQualityError):finish.apply_proposal(self.article,self.outline,self.report,self.context,p)

    def test_repeating_a_qualifier_is_not_a_numeric_or_citation_change(self):
        self.assertEqual(finish.immutable_anchors('必ずではない。100円です。※1'),finish.immutable_anchors('必ずではない。対象ではない場合もある。100円です。※1'))
        self.assertNotEqual(finish.immutable_anchors('100円です。※1'),finish.immutable_anchors('200円です。※1'))
        self.assertNotEqual(finish.immutable_anchors('100円です。※1'),finish.immutable_anchors('100円です。※2'))
        v={'checks':{k:{'status':'pass','reason':'照合'} for k in finish.KEYS}}
        v['checks']['evidence_preserved']['status']='fail'
        with self.assertRaises(cq.ContentQualityError):finish.require_review(v)

    def test_delete_reorder_change_claims_or_reader_goals_rejected(self):
        for change in ('delete','reorder','claim','reader','h2','unrelated','body','number','new_fact'):
            p=copy.deepcopy(self.proposal)
            if change=='delete':p['outline_patch']['delete_ids']=['s02']
            if change=='reorder':p['outline_patch']['section_order']=['s02','s01','s03']
            if change in ('claim','reader'):
                s=copy.deepcopy(self.outline['meta']['editorial_blueprint']['sections'][1])
                if change=='claim':s['research_ids']=[]
                else:s['reader_progression']={'invented':'reader'}
                p['outline_patch']['edits'].append({'id':'s02','section':s})
            if change=='h2':p['outline_patch']['edits'][0]['section']['level']=2
            if change=='unrelated':p['outline_patch']['edits'][0]['section']['research_ids']=['unknown']
            if change=='body':p['article_edits'].append({'id':'block-0000','new':'# 別の記事'})
            if change=='number':p['article_edits'][0]['new']+='100円です。'
            if change=='new_fact':p['article_edits'][0]['new']+='\n\n- 新しいリスト'
            with self.subTest(change=change),self.assertRaises(cq.ContentQualityError):
                finish.apply_proposal(self.article,self.outline,self.report,self.context,p)

    def test_every_independent_delta_verdict_required(self):
        good={'checks':{k:{'status':'pass','reason':'変更前後を確認'} for k in finish.KEYS}}
        finish.require_review(good)
        for k in finish.KEYS:
            for variant in ('missing','fail','empty'):
                v=copy.deepcopy(good)
                if variant=='missing':v['checks'].pop(k)
                if variant=='fail':v['checks'][k]['status']='fail'
                if variant=='empty':v['checks'][k]['reason']=''
                with self.subTest(k=k,variant=variant),self.assertRaises(cq.ContentQualityError):finish.require_review(v)

    def test_receipt_binds_all_context_and_retains_failed_phases(self):
        text,o,b=finish.apply_proposal(self.article,self.outline,self.report,self.context,self.proposal)
        context=finish.candidate_context(self.context,o,b);context['text']=text
        view=finish.review_view(self.article,self.outline,self.report,self.context,self.proposal,text,o)
        r=copy.deepcopy(self.report);r.update(valid=True,snapshot=cq.snapshot(**context),acceptance_mode='full_audit_plus_local_editorial_validation')
        for c in r['checks']:
            if c['key']=='prose_quality':c.update(status='pass',affected_blocks=[])
        r['editorial_completion']={'policy':finish.POLICY,'policy_sha256':finish.policy_digest(),'base_context':self.context,
          'base_report':self.report,'base_article':self.article,'base_outline':self.outline,'proposal':self.proposal,
          'review':{'checks':{k:{'status':'pass','reason':'比較済み'} for k in finish.KEYS}},
          'review_input_sha256':cq.digest(json.dumps(view,ensure_ascii=False,sort_keys=True))}
        cq._require_audit_strict(r,cq.snapshot(**context),stage='article',context=context)
        self.assertEqual(r['phases']['organization']['checks'][0]['status'],'fail')
        for key in ('text','outline','facts','sources','contract','requirements'):
            c=copy.deepcopy(context)
            c[key]=(c[key]+'変更') if isinstance(c[key],str) else {**c[key],'tampered':True}
            with self.subTest(key=key),self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(r,cq.snapshot(**c),stage='article',context=c)
        for variant in ('phase','findings','verdict'):
            changed=copy.deepcopy(r)
            if variant=='phase':changed['phases']['organization']['checks'][0]['status']='pass'
            if variant=='findings':changed['editorial_findings']=[]
            if variant=='verdict':changed['editorial_completion']['review']['checks'].pop('language')
            with self.subTest(variant=variant),self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(changed,cq.snapshot(**context),stage='article',context=context)

class WarningResolutionTests(unittest.TestCase):
    def test_failed_final_never_clears_warnings(self):
        from pipeline.quality_warnings import resolve_after_final
        with patch('pipeline.db.get_artifact',return_value={'content_text':'{"valid":false}'}),patch('pipeline.db.upsert_artifact') as write:
            with self.assertRaises(cq.ContentQualityError):resolve_after_final('job')
            write.assert_not_called()
    def test_pass_archives_warnings_and_is_idempotent(self):
        from pipeline.quality_warnings import resolve_after_final
        store={'content_text':json.dumps({'warnings':[{'stage':'research','message':'old failure','at':'before'}]})}
        def write(**kw):store.update(kw)
        with patch('pipeline.db.get_artifact',return_value={'content_text':'{"valid":true,"violations":[]}'}),patch('pipeline.db.get_optional_artifact',side_effect=lambda *a:store),patch('pipeline.db.upsert_artifact',side_effect=write) as save:
            resolve_after_final('job');resolve_after_final('job')
            self.assertEqual(save.call_count,1)
            v=json.loads(store['content_text']);self.assertEqual(v['warnings'],[]);self.assertEqual(v['resolved_warnings'][0]['message'],'old failure')

class CompletionBudgetTests(unittest.TestCase):
    def test_production_cannot_repeat_editorial_finish_with_different_input(self):
        from pipeline import quality_budget as q
        import os
        for role in ('editorial_finish_repair','editorial_finish_review','editorial_finish_mechanical','editorial_finish_titles','editorial_finish_recheck'):
            meta={'phase':'article','role':role,'call_limit':1,'request_sha256':'new'}
            value={'calls':[{'status':'accounted','cost_usd':.1,'reserved_usd':.1,
                'review_operation':{**meta,'request_sha256':'previous'}}]}
            token=q.REQUEST.set(meta)
            try:
                with patch.dict(os.environ,QUALITY_BUDGET_MODE='unlimited'),self.assertRaises(cq.ContentQualityError):q.guard_review(value,.1)
            finally:q.REQUEST.reset(token)

class MechanicalAddressTests(unittest.TestCase):
    def test_retry_maps_shifted_ids_to_changed_original_blocks_only(self):
        from pipeline.content_edits import apply_block_edits
        text='# 題\n\n## 親\n\n入口。\n\n### 子\n\n説明。\n\n次の段落。'
        blocks=content_blocks(text);a=blocks[2]['id'];b=blocks[-2]['id']
        p={'article_edits':[{'id':a,'new':'入口。\n\n#### 先に増える見出し\n\n説明。'},
                            {'id':b,'new':'長い説明。'}],'outline_patch':{}}
        candidate=apply_block_edits(text,json.dumps({'edits':p['article_edits']}))
        failed=next(x['id'] for x in content_blocks(candidate) if x['text']=='長い説明。')
        neighbor=content_blocks(candidate)[-1]['id']
        with patch('pipeline.readability.readability_issues',return_value=[{'key':'continuous_prose','affected_blocks':[{'id':failed},{'id':neighbor}]}]):
            self.assertEqual(finish.mechanical_targets({'content_text':text},p,candidate),[b])
        adjusted=finish.apply_mechanical(p,{'edits':[{'id':b,'new':'説明。'}]},{'content_text':text},[b])
        self.assertEqual(adjusted['article_edits'],[p['article_edits'][0]])
        with self.assertRaises(cq.ContentQualityError):finish.apply_mechanical(p,{'edits':[{'id':neighbor,'new':'別の内容'}]},{'content_text':text},[b])

class HeadingMeaningTests(unittest.TestCase):
    def test_correction_only_changes_the_quoted_new_heading(self):
        proposal={'outline_patch':{'edits':[{'id':'new-a','section':{'id':'new-a','title':'兼用では解決しない'}}]},
                  'article_edits':[{'id':'block-0010','new':'#### 兼用では解決しない\n\n自動で解決するわけではありません。'}]}
        fixed=finish.apply_titles(proposal,{'titles':{'new-a':'兼用でも確認は必要'}},['new-a'])
        self.assertEqual(fixed['article_edits'][0]['new'],'#### 兼用でも確認は必要\n\n自動で解決するわけではありません。')
        self.assertEqual(proposal['outline_patch']['edits'][0]['section']['title'],'兼用では解決しない')
        for titles in ({'other':'別の話'},{'new-a':'兼用では解決しない'},{'new-a':'100円です'},{'new-a':'別の話\n本文'}):
            with self.assertRaises(cq.ContentQualityError):finish.apply_titles(proposal,{'titles':titles},['new-a'])
    def test_only_a_quoted_new_heading_can_enter_the_terminal_title_fix(self):
        _,_,_,ctx,value=fixture();outline=artifact(value,ctx)
        proposal={'outline_patch':{'edits':[{'id':value['sections'][0]['id'],'section':{'level':2,'title':'兼用では解決しない'}},
            {'id':'new-a','section':{'level':4,'title':'兼用では解決しない'}}]}}
        verdict={'checks':{'evidence_preserved':{'status':'fail','reason':'新見出し「兼用では解決しない」は断定が強い。'},
                           'organization':{'status':'fail','reason':'AIの指定した手段と異なる。'}}}
        self.assertEqual(finish.title_targets(proposal,verdict,outline),['new-a'])
        verdict['checks']['evidence_preserved']['reason']='具体的な箇所は不明'
        self.assertEqual(finish.title_targets(proposal,verdict,outline),[])
