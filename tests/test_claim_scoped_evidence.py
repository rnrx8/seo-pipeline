import unittest
from test_evidence_policy import fixture
from pipeline import research_requirements as req
from pipeline.evidence_policy import EVIDENCE_POLICY


class ClaimScopedEvidenceTests(unittest.TestCase):
    def guidance(self):
        plan,pages,value=fixture()
        plan['items'][0].update(question='旅行の待ち合わせ場所をどう決めるか',source_requirement='general_guidance')
        pages=[{'url':'https://travel.example/meeting','status':'success','text':'初めて会う人との待ち合わせには、人通りのある場所を選びましょう。'}]
        value['items'][0].update(answer='初めて会う人との待ち合わせには、人通りのある場所を選ぶとよい。',
            basis='guidance',official_checked_urls=[],exploration_complete=False,
            evidence=[{'url':pages[0]['url'],'quote':pages[0]['text'],'source_kind':'secondary','independence_group':'travel.example'}])
        return plan,pages,value

    def test_general_howto_accepts_one_relevant_body_without_expert_credentials(self):
        plan,pages,value=self.guidance()
        req.validate_plan(plan)
        self.assertEqual(req.validate_matrix(value,plan,pages),[])
        self.assertTrue(value['items'][0]['verified'])
        self.assertIn('人通りのある場所',req.accepted_facts(value,plan))

    def test_guidance_cannot_bypass_commercial_or_expert_or_primary_only_requirements(self):
        for requirement in ('standard','expert_allowed','primary_only'):
            with self.subTest(requirement=requirement):
                plan,pages,value=self.guidance()
                plan['items'][0]['source_requirement']=requirement
                self.assertTrue(req.validate_matrix(value,plan,pages))
                self.assertFalse(value['items'][0]['verified'])

    def test_guidance_still_requires_exact_quote_from_successful_body(self):
        for change in (lambda p,v:p[0].update(status='failed'),
                       lambda p,v:v['items'][0]['evidence'][0].update(quote='原文にない効果を保証する'),
                       lambda p,v:v['items'][0].update(evidence=[])):
            plan,pages,value=self.guidance();change(pages,value)
            self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_authors_own_report_is_primary_only_for_attributed_report(self):
        plan,pages,value=fixture()
        plan['items'][0]['question']='初めての登山について利用者はどう報告しているか'
        pages=[{'url':'https://personal.example/report','status':'success','text':'私は初登山で往復4時間かかりました。'}]
        item=value['items'][0]
        item.update(answer='投稿者は初登山で往復4時間かかったと報告している。',basis='primary',
            official_checked_urls=[],exploration_complete=False,
            evidence=[{'url':pages[0]['url'],'quote':pages[0]['text'],'source_kind':'primary','independence_group':'author'}])
        self.assertEqual(req.validate_matrix(value,plan,pages),[])
        # A third-party retelling or sole commercial secondary citation is not
        # upgraded automatically; source attribution still needs model review.
        item['evidence'][0]['source_kind']='secondary'
        self.assertTrue(req.validate_matrix(value,plan,pages))
        item['basis']='corroborated'
        self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_writing_preserves_attributed_answer_and_guidance_scope(self):
        from pipeline.generation_context import decision_bundle, POLICY
        plan,pages,value=self.guidance();req.validate_matrix(value,plan,pages)
        value.update(valid=True,policy_sha256=req.matrix_policy())
        bundle=decision_bundle(plan,value,quotes=True)
        self.assertIn(value['items'][0]['answer'],bundle)
        self.assertIn('一般的ハウツーは助言の範囲を保ち',POLICY)
        self.assertIn('本人の体験・意見は発信者の報告として',POLICY)

    def test_common_policy_replaces_conflicting_source_tier_instructions(self):
        from pipeline.step_fact_sheet import SYSTEM_PROMPT
        from pipeline.research_plan_review import PLAN_REVIEW_SYSTEM
        from pipeline.research_verification import COVERAGE_SYSTEM
        from pipeline.content_quality import AUDIT_SYSTEM
        for prompt in (req.PLAN_SYSTEM,req.MATRIX_SYSTEM,PLAN_REVIEW_SYSTEM,COVERAGE_SYSTEM,AUDIT_SYSTEM):
            self.assertIn(EVIDENCE_POLICY,prompt)
        self.assertNotIn('同じ数値・情報を複数サイトで確認できた場合のみ',SYSTEM_PROMPT)
        self.assertNotIn('一般的な補足はexpert_allowed',EVIDENCE_POLICY)
        self.assertIn('全媒体の取得を合格条件にしない',EVIDENCE_POLICY)
        self.assertIn('媒体間の見解比較や特定媒体そのものを指定した場合',EVIDENCE_POLICY)

    def test_valid_guidance_does_not_always_buy_specialist_adjudication(self):
        from pipeline.tiered_research import needs_adjudication
        plan,pages,value=self.guidance();req.validate_matrix(value,plan,pages)
        q=plan['items'][0]
        q['id']=next('q'+str(n) for n in range(100) if int(req.digest('q'+str(n))[:8],16)%10!=0)
        self.assertFalse(needs_adjudication(value['items'][0],q))
        q['source_requirement']='expert_allowed'
        self.assertTrue(needs_adjudication(value['items'][0],q))
