import copy,json,unittest
from unittest.mock import patch
from pipeline import editorial_blueprint as bp, reader_journey as rj, blueprint_transport as bt
from pipeline.content_edits import content_blocks
from pipeline.content_quality import ContentQualityError
from pipeline.step_plan import research_resume_stage
from test_editorial_blueprint import fixture,artifact


class OutlineQARecoveryTests(unittest.TestCase):
    def test_shown_paragraph_alias_requires_same_semantic_text_and_chapter(self):
        *_,ctx,value=fixture();a=artifact(value,ctx);review=bp.review_context(a)
        sources=bp.journey_evidence(review,a['content_text'])
        sid=value['sections'][0]['id'];allowed=rj.chapter_evidence(sid,sources)
        aliases=[key for key in allowed if key.startswith('block-')];self.assertTrue(aliases)
        key=aliases[0];quote=allowed[key][0]
        row={'reader_state':'読むときの状態','attunement':{'status':'not_needed','reason':'定義','evidence':[]},'lived_value':{'status':'not_needed','reason':'定義','evidence':[]},'guidance':{'status':'pass','reason':'次に進める','evidence':[{'id':key,'quote':quote}]}}
        self.assertEqual(rj.checked(row,allowed),[])
        row['guidance']['evidence'][0]['quote']='原稿にない言葉'
        with self.assertRaises(ContentQualityError):rj.checked(row,allowed)
        row['guidance']['evidence'][0]={'id':'block-99999','quote':quote}
        with self.assertRaises(ContentQualityError):rj.checked(row,allowed)

    def test_psychology_field_alone_is_not_an_attunement_alias(self):
        *_,ctx,value=fixture();a=artifact(value,ctx);review=bp.review_context(a)
        sources=bp.journey_evidence(review,a['content_text'])
        for key,v in sources.items():
            if key.startswith('block-'):
                self.assertTrue(all(t in review['journey_sections'][v['parent_id']]['texts'] for t in v['texts']))

    def test_titles_editable_only_for_exact_reviewed_title_blocks(self):
        *_,ctx,value=fixture();a=artifact(value,ctx)
        blocks=content_blocks(a['content_text'])
        title=next(b['id'] for b in blocks if value['titles'][0] in b['text'])
        report={'checks':[{'status':'fail','key':'evidence_support','affected_blocks':[{'id':title}]}]}
        self.assertTrue(bp.title_repair_allowed(a,report))
        body=next(b['id'] for b in blocks if value['sections'][0]['conclusion'] in b['text'])
        report['checks'][0]['affected_blocks']=[{'id':body}]
        self.assertFalse(bp.title_repair_allowed(a,report))
        response={'edits':[],'delete_ids':[],'section_order':[],'titles':['条件の比較と確認方法']}
        changed=bp.apply_edits(value,response,ctx,structural=False,targets=[],allow_titles=True)
        self.assertEqual(changed['sections'],value['sections'])
        with self.assertRaises(ContentQualityError):bp.apply_edits(value,response,ctx,structural=False,targets=[])
        wire,original=bt.prepare({'model':'claude-opus-5-5','system':'policy','output_config':bp.repair_config(allow_titles=True)})
        self.assertEqual(original,bp.repair_schema(allow_titles=True));self.assertNotIn('format',wire['output_config'])

    def test_resume_returns_to_unfinished_outline_qa_without_research_or_outline_regeneration(self):
        keys=['research_completeness','outline','research_validation','article','content_audit']
        saved={'search_intent':{'meta':{'reader_needs_version':'v1'}},'research_matrix':{'content_text':'{"valid":true}'},'outline':{'meta':{'blueprint_version':'v1'}},'research_validation':{'content_text':'{"valid":false,"error":"bad citation"}'}}
        load=lambda j,k:copy.deepcopy(saved.get(k))
        with patch('pipeline.research_requirements._require_matrix_strict',return_value={}):
            self.assertEqual(research_resume_stage('j','article',keys,load),'research_validation')
            saved['research_validation']['content_text']='{"valid":true}'
            with patch.object(bp,'require_review',return_value=None):self.assertEqual(research_resume_stage('j','article',keys,load),'article')
            with patch.object(bp,'require_review',side_effect=ContentQualityError('stale')):self.assertEqual(research_resume_stage('j','article',keys,load),'research_validation')
            with patch.object(bp,'require_review',side_effect=RuntimeError('transport')):
                with self.assertRaises(RuntimeError):research_resume_stage('j','article',keys,load)

    def test_same_quote_from_another_chapter_does_not_establish_support(self):
        evidence={'s01':{'parent_id':'','texts':['同じ答え']},'s02':{'parent_id':'','texts':['別の答え']},
                  'block-1':{'parent_id':'s01','texts':['同じ答え']},'block-2':{'parent_id':'s02','texts':['別の答え']}}
        scoped=rj.chapter_evidence('s02',evidence)
        row={'reader_state':'次の疑問','attunement':{'status':'not_needed','reason':'直接回答','evidence':[]},'lived_value':{'status':'not_needed','reason':'直接回答','evidence':[]},'guidance':{'status':'pass','reason':'答える','evidence':[{'id':'block-1','quote':'同じ答え'}]}}
        with self.assertRaises(ContentQualityError):rj.checked(row,scoped)
