import copy
import json
import unittest
from pipeline import content_quality as cq,verification_completion as vc
from pipeline.content_edits import content_blocks
from quality_fixtures import phases


class VerificationCompletionTests(unittest.TestCase):
    def setUp(self):
        self.context=dict(text='# 比較\n\n## 会員規模\n\n登録数を時点付きで比べます。\n\n| 項目 | A |\n|---|---|\n| 累計人数 | 100万人（取材記事の登録数） |',
            outline='### H2：会員規模',facts='登録人数100万人',contract={},requirements={},
            sources=json.dumps([{'url':'https://a.example','text':'登録人数100万人'}]))
        self.block=content_blocks(self.context['text'])[-1]
        self.edits=[{'id':self.block['id'],'new':self.block['text'].replace('累計人数','公表人数')}]
        self.report=dict(stage='article',attempt=2,snapshot=cq.snapshot(**self.context),policy_version=cq.POLICY_VERSION,
            valid=False,evidence_pending=False,structural_issues=[],readability_issues=[],phases=phases(cq.snapshot(**self.context),self.context['text']))
        loc=[{'id':self.block['id'],'reason':'登録人数と累計人数を区別'}]
        from pipeline.editorial_findings import VERSION
        for role,phase in self.report['phases'].items():
            if role!='evidence':phase.update(findings_version=VERSION,editorial_findings=[])
        self.report['phases']['evidence']['checks'][-1].update(status='fail',affected_blocks=loc)
        self.report['checks']=[dict(key=k,status='fail' if k=='metric_scope' else 'pass',reason='確認',affected_blocks=loc if k=='metric_scope' else []) for k in cq.CHECKS]
        self.review={'edits':{self.block['id']:{k:{'status':'pass','reason':'掲載定義と取得原文を照合'} for k in vc.VERDICTS}},
                     'resolved_checks':{'metric_scope':{'status':'pass','reason':'登録人数として表記を区別'}}}

    def compose(self,**kw):
        return vc.compose(kw.get('report',self.report),self.context,kw.get('edits',self.edits),['https://a.example'],kw.get('review',self.review))

    def test_pass_retains_original_failed_phases_and_requires_same_final_context(self):
        report,current,_=self.compose()
        self.assertEqual(report['phases'],self.report['phases'])
        self.assertEqual(report['phases']['evidence']['checks'][-1]['status'],'fail')
        cq._require_audit_strict(report,cq.snapshot(**current),stage='article',context=current)
        with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(report,cq.snapshot(**current),stage='research',context=current)
        changed=copy.deepcopy(current);changed['sources']='別の資料'
        with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(report,cq.snapshot(**changed),stage='article',context=changed)

    def test_unknown_and_numeric_or_heading_edits_are_rejected_before_model(self):
        for edits in ([{'id':'block-0004','new':'別の説明です。'}],[{'id':self.block['id'],'new':self.edits[0]['new'].replace('100','200')}],
                      [{'id':self.block['id'],'new':self.edits[0]['new']+'\n\n## 別の章'}]):
            with self.subTest(edits=edits),self.assertRaises(cq.ContentQualityError):self.compose(edits=edits)

    def test_incomplete_or_failed_review_is_rejected(self):
        for key in vc.VERDICTS:
            review=copy.deepcopy(self.review);review['edits'][self.block['id']][key]['status']='fail'
            with self.subTest(key=key),self.assertRaises(cq.ContentQualityError):self.compose(review=review)
        review=copy.deepcopy(self.review);review['resolved_checks']={}
        with self.assertRaises(cq.ContentQualityError):self.compose(review=review)

    def test_source_or_reader_failure_cannot_use_local_completion(self):
        for key in ('evidence_support','coverage'):
            report=copy.deepcopy(self.report);next(c for c in report['checks'] if c['key']==key)['status']='fail'
            with self.subTest(key=key),self.assertRaises(cq.ContentQualityError):self.compose(report=report)

    def test_old_pass_cannot_be_rebound_to_another_text(self):
        report,current,_=self.compose();report['snapshot']='unrelated'
        with self.assertRaises(cq.ContentQualityError):vc.validate_completion(report,cq.snapshot(**current),current)

    def test_missing_role_or_hidden_failure_is_rejected(self):
        report=copy.deepcopy(self.report);report['phases'].pop('language')
        with self.assertRaises(cq.ContentQualityError):self.compose(report=report)
        report=copy.deepcopy(self.report);report['phases']['consistency']['checks'][0].update(status='fail',affected_blocks=[{'id':self.block['id'],'reason':'未解決'}])
        with self.assertRaises(cq.ContentQualityError):self.compose(report=report)


if __name__=='__main__':unittest.main()
