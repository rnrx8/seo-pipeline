import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import article_design as ad,content_quality as cq,focused_quality as fq
from pipeline.section_identity import carry_sections
from pipeline.article_quality import article_sections
from pipeline.lead_review import KEYS
from test_editorial_recovery import materials


class ReaderReviewBindingTests(unittest.TestCase):
    def fixture(self):
        article,outline,_=materials();before=article['content_text']
        heading=next(s['title'] for s in article_sections(before) if s['level']==2)
        after=before.replace('## '+heading,'## 図面を確かめる手順',1)
        binding=carry_sections(before,after,outline['content_text'],article['meta']['section_map'])
        article={**article,'content_text':after,'meta':{**article['meta'],'section_map':binding}}
        return article,outline,binding

    def test_repaired_heading_keeps_local_ranges_through_full_qa_and_strict_gate(self):
        article,outline,binding=self.fixture();text=article['content_text'];design=ad.design_context(outline)
        self.assertFalse(ad.correspondence(text,outline['content_text'],design)['sections'][0]['article_section_ids'])
        requirements=ad.bind_review_requirements({'article_design':design},text,outline['content_text'],binding)
        requests=[]
        def send(job,step,request):
            role=step.removeprefix('tiered_article_');payload=json.loads(request['messages'][0]['content']);requests.append(payload)
            result={'checks':[{'key':k,'status':'pass','reason':'合成確認','affected_blocks':[]} for k in fq.ROLES[role]]}
            if role!='evidence':result['editorial_findings']=[]
            if role=='coverage':
                self.assertTrue(all(row['article_section_ids'] for row in payload['reader_correspondence']['sections']))
                result.update(decision_reviews={d['id']:{'status':'pass','reason':'合成確認','missing_requirements':[],'affected_blocks':[]} for d in design['reader_decisions']},
                    lead_review={k:{'status':'pass','reason':'合成確認','affected_blocks':[]} for k in KEYS},
                    reader_progression_reviews={s['id']:{'status':'pass','reason':'合成確認','affected_blocks':[]} for s in design['sections'] if s['reader_progression']})
            if role=='organization':
                self.assertTrue(all(c['block_ids'] for p in payload['heading_scope_groups'] for c in p['children']))
                result['heading_scope_reviews']={p['id']:{c['id']:{'relation':'answer','reason':'合成確認'} for c in p['children']} for p in payload['heading_scope_groups']}
            return result,SimpleNamespace(input_tokens=1,output_tokens=1)
        context=dict(text=text,outline=outline['content_text'],facts='',sources='',contract={},requirements=requirements)
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'}),patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=fq.audit_article(None,**context)
            cq._require_audit_strict(report,cq.snapshot(**context),stage='article',context=context)
        self.assertEqual(len(requests),5);self.assertTrue(report['valid'])
        self.assertNotIn('section_binding',design)
        from pipeline import step_content_audit
        with patch.object(step_content_audit,'astra_review_enabled',return_value=False),patch.object(step_content_audit,'upsert_artifact') as saved:
            step_content_audit.publish_pass('job',article,text,report,binding,True,{'id':'audit'})
        stored=saved.call_args.kwargs
        self.assertEqual(stored['meta']['reader_correspondence'],report['phases']['coverage']['reader_correspondence'])
        self.assertEqual(stored['meta']['reader_correspondence']['text_sha256'],cq.digest(stored['content_text']))
        stale=copy.deepcopy(report);stale['phases']['coverage']['reader_correspondence']['text_sha256']='stale'
        with patch.object(step_content_audit,'upsert_artifact') as saved,self.assertRaises(cq.ContentQualityError):
            step_content_audit.publish_pass('job',article,text,stale,binding,True,{'id':'audit'})
        saved.assert_not_called()

    def test_saved_final_context_loads_same_binding(self):
        article,outline,binding=self.fixture()
        with patch.object(cq,'intent_review_requirements',return_value={}),patch.object(cq,'astra_review_enabled',return_value=False),patch('pipeline.db.get_optional_artifact',side_effect=lambda j,k:outline if k=='outline' else article):
            requirements=cq.final_review_requirements({'id':'job'},'条件')
        self.assertEqual(requirements['article_design']['section_binding'],binding)

    def test_stale_body_or_outline_binding_is_rejected_not_guessed(self):
        article,outline,binding=self.fixture();requirements={'article_design':ad.design_context(outline)}
        for text,source in ((article['content_text'].replace('## 図面を確かめる手順','## 別の章'),outline['content_text']),
                            (article['content_text'],outline['content_text']+'\n変更')):
            with self.assertRaises(cq.ContentQualityError):ad.bind_review_requirements(requirements,text,source,binding)
        bound=ad.bind_review_requirements(requirements,article['content_text'],outline['content_text'],binding)
        with self.assertRaises(cq.ContentQualityError):ad.correspondence(article['content_text'].replace('## 図面を確かめる手順','## 別の章'),outline['content_text'],bound['article_design'])
