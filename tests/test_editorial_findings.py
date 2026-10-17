import copy
import json
import unittest
from unittest.mock import patch
from pipeline import editorial_findings as ef, content_quality as cq, focused_quality as fq
from pipeline import step_content_audit as ca
import test_stage5_authoring as stage5

TEXT = '## 選ぶ\n\n本、動画、講座があります。\n\n次に確認します。'
TARGET = cq.content_blocks(TEXT)[1]['id']


def finding(severity='blocking'):
    return {'check_key':'coverage', 'severity':severity, 'block_ids':[TARGET],
            'problem':'方法名だけで違いがない', 'reader_impact':'自分に合う方法を選べない',
            'required_change':'向く状況と主な制約を補う', 'stage_to_fix':'article'}


def checks(status='fail'):
    return [{'key':'coverage', 'status':status, 'reason':'確認した',
             'affected_blocks':[{'id':TARGET,'reason':'方法の違いを補う'}] if status=='fail' else []}]


class EditorialFindingsTests(unittest.TestCase):
    def test_missing_main_answer_has_action_and_impact(self):
        rows=ef.checked_findings({'editorial_findings':[finding()]},checks(),TEXT)
        self.assertEqual(rows[0]['stage_to_fix'],'article')
        self.assertIn('選べない',rows[0]['reader_impact'])

    def test_optional_empathy_or_cta_does_not_block_or_enter_repair(self):
        row=finding('improvement');row.update(problem='結びに短い共感を追加できる',reader_impact='今でも判断できるが親しみを増やせる',required_change='必要なら一文だけ追加',stage_to_fix='article')
        self.assertEqual(ef.checked_findings({'editorial_findings':[row]},checks('pass'),TEXT),[row])
        packet={'requirements':{},'article_blocks':cq.content_blocks(TEXT),'target_block_ids':[TARGET]}
        report={'checks':checks('pass'),'editorial_findings':[row]}
        with patch('pipeline.quality_context.compact_enabled',return_value=True),patch('pipeline.review_scope.repair_packet',return_value=packet),patch.object(ca,'hybrid_writing_enabled',return_value=False):
            request,_=ca.prepare_content_repair(None,job_id='j',text=TEXT,report=report,requirements={},sources='[]',facts='',contract={},issues=[])
        self.assertEqual(json.loads(request['messages'][0]['content'])['editorial_findings'],[])

    def test_fail_cannot_be_just_an_optional_improvement(self):
        with self.assertRaises(cq.ContentQualityError):ef.checked_findings({'editorial_findings':[finding('improvement')]},checks(),TEXT)

    def test_pass_cannot_hide_blocking_finding(self):
        with self.assertRaises(cq.ContentQualityError):ef.checked_findings({'editorial_findings':[finding()]},checks('pass'),TEXT)

    def test_rejects_unknown_ids_missing_impact_and_unsupported_owner(self):
        for changes in ({'block_ids':['unknown']},{'reader_impact':''},{'stage_to_fix':'guess'},{'check_key':'other'}):
            row={**finding(),**changes}
            with self.subTest(changes=changes),self.assertRaises(cq.ContentQualityError):ef.checked_findings({'editorial_findings':[row]},checks(),TEXT)

    def test_publication_records_bind_policy_and_individual_findings(self):
        helper=stage5.Stage5AuthoringTests()
        report,requests,ctx=helper.audit_fixture('tiered','legacy')
        self.assertEqual(len(requests),5)
        for role in fq.ROLES:
            if role!='evidence':self.assertEqual(report['phases'][role]['findings_version'],ef.VERSION)
        with patch('pipeline.quality_context.compact_enabled',return_value=False),patch.dict('os.environ',{'ARTICLE_REVIEW_PROVIDER':'tiered','ARTICLE_WRITING_FLOW':'legacy'}):
            expected=cq.snapshot(**ctx)
            cq._require_audit_strict(report,expected,stage='article',context=ctx)
            broken=copy.deepcopy(report);broken['phases']['language'].pop('editorial_findings')
            with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(broken,expected,stage='article',context=ctx)

    def test_individual_failure_has_exact_missing_answer_without_guessed_cause(self):
        report,_,_=stage5.Stage5AuthoringTests().audit_fixture('tiered','legacy',1)
        rows=report['editorial_findings']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['origin'],'individual_decision_review')
        self.assertEqual(rows[0]['stage_to_fix'],'investigate')
        self.assertTrue(rows[0]['missing_answers'])
        self.assertFalse(report['valid'])

    def test_snapshot_changes_with_finding_policy(self):
        original=cq.snapshot(TEXT,'','',{}, {},'[]')
        with patch.object(ef,'POLICY',ef.POLICY+'changed'):
            self.assertNotEqual(original,cq.snapshot(TEXT,'','',{}, {},'[]'))

    def test_schema_preserves_existing_checks_and_adds_no_new_reviewer(self):
        old=cq.audit_output_config(('coverage','unfinished_content'),locations=True)
        schema=ef.output_config(old,('coverage','unfinished_content'))
        self.assertNotIn('editorial_findings',old['format']['schema']['properties'])
        self.assertIn('editorial_findings',schema['format']['schema']['required'])
        self.assertEqual(len(fq.ROLES),5)


    def test_local_completion_keeps_finding_locations_bound_to_original_snapshot(self):
        import test_prose_completion as existing
        tc=existing.CompletionTests()
        tc.setUp()
        try:
            tc.ctx['requirements']['article_design']={'reader_decisions':[{
                'id':'d1','role':'primary','question':'何ができるか','research_ids':['q1'],
                'answer_requirements':['持ち運べると分かる']}], 'sections':[],
                'version':'article-reader-design-v1','blueprint_sha256':'test'}
            tc.base=tc.report(tc.text)
            for role,phase in tc.base['phases'].items():
                if role=='evidence':continue
                phase.update(findings_version=ef.VERSION,editorial_findings=[])
                if role=='language':
                    phase['editorial_findings']=[{'check_key':'prose_quality','severity':'blocking','block_ids':[tc.block],
                        'problem':'主述が不自然','reader_impact':'機能の説明を読み取りにくい','required_change':'主述を整える','stage_to_fix':'article'}]
                if role=='coverage':phase['decision_reviews']={'d1':{'status':'pass','reason':'持ち運びの基本機能と説明','missing_requirements':[],'affected_blocks':[]}}
            (candidate,result),calls=tc.execute()
            self.assertEqual(len(calls),2)
            self.assertEqual(result['phases'],tc.base['phases'])
            cq._require_audit_strict(result,cq.snapshot(candidate,**tc.ctx),stage='article',context={'text':candidate,**tc.ctx})
            result['phases']['language']['editorial_findings'][0]['block_ids']=['unknown']
            with self.assertRaises(cq.ContentQualityError):
                cq._require_audit_strict(result,cq.snapshot(candidate,**tc.ctx),stage='article',context={'text':candidate,**tc.ctx})
        finally:tc.doCleanups()
