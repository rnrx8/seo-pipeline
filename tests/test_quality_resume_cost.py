"""Resume failure scenarios: identical paid replies must not be purchased twice."""
import copy
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import content_quality as quality, tiered_research as tiered
from pipeline import step_content_audit as content_audit
from pipeline.quality_budget import scope
from test_content_quality import SOURCE, report


class QualityResumeCostTests(unittest.TestCase):
    def setUp(self):
        evidence=patch('pipeline.generation_context.generation_evidence',side_effect=lambda job,facts,sources,**kw:facts)
        evidence.start();self.addCleanup(evidence.stop)
        self.saved={}
        env=patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'})
        env.start();self.addCleanup(env.stop)
        load=patch.object(tiered,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(self.saved.get(s)))
        load.start();self.addCleanup(load.stop)
        save=patch.object(tiered,'upsert_artifact',side_effect=lambda **kw:self.saved.update({kw['step']:copy.deepcopy(kw)}))
        save.start();self.addCleanup(save.stop)

    def message(self,value):
        return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(value))],usage=NS(input_tokens=10,output_tokens=5))

    def test_outline_check_reuses_reply_but_revalidates_against_changed_sources(self):
        args=dict(stage='research',text='構成',facts='',outline='構成',contract={},requirements={},sources='原資料')
        with scope('same-job'), patch.object(tiered,'create_with_retry',return_value=self.message(report())) as call:
            first=quality.audit(None,**args)
            second=quality.audit(None,**args)
            self.assertEqual(call.call_count,1)
            self.assertEqual(first['snapshot'],second['snapshot'])
            self.assertEqual(second['input_tokens'],0)
            changed=quality.audit(None,**{**args,'sources':'更新した原資料'})
            self.assertEqual(call.call_count,2)
            self.assertNotEqual(first['snapshot'],changed['snapshot'])
            # An unchanged cached model pass still receives mechanical checks.
            guarded={**args,'text':'無料なのでノーリスクです。'}
            self.assertFalse(quality.audit(None,**guarded)['valid'])
            self.assertFalse(quality.audit(None,**guarded)['valid'])
            self.assertEqual(call.call_count,3)

    def test_repair_reply_survives_interruption_before_article_is_changed(self):
        artifacts={'article':{'content_text':'## 比較\n十分な説明。'},
                   'outline':{'content_text':'### H2：比較'},
                   'content_contract':{'content_text':'{"required_sections":[]}'},
                   'fresh_sources':SOURCE,'fact_sheet':{'content_text':'> 確認済みの事実 [confirmed]'}}
        class Interrupted(Exception):pass
        fail_once=[True]
        def save(**kw):
            if kw['step']=='content_repair_response_1' and fail_once[0]:
                fail_once[0]=False
                raise Interrupted()
            return kw
        result=self.message({'edits':[{'id':'block-0000','new':'## 比較\n一度修正した説明。'}]})
        with patch.object(content_audit,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(content_audit,'get_job',return_value={}), \
             patch.object(content_audit,'audit',side_effect=lambda *a,**kw:report('conclusion_consistency')) as audit, \
             patch.object(content_audit,'upsert_artifact',side_effect=save), \
             patch.object(tiered,'create_with_retry',return_value=result) as generate:
            with self.assertRaises(Interrupted):content_audit.run('same-job','比較')
            self.assertEqual(artifacts['article']['content_text'],'## 比較\n十分な説明。')
            with self.assertRaises(quality.ContentQualityError):content_audit.run('same-job','比較')
            self.assertEqual(generate.call_count,1)
            self.assertEqual(audit.call_args.kwargs['text'],'## 比較\n一度修正した説明。')
            self.assertEqual(audit.call_count,3)  # old original + resumed original + repaired text
