import copy
import unittest
from pipeline.review_scope import validate_routes
from pipeline.content_quality import ContentQualityError

class ClaimRouteHandoffTests(unittest.TestCase):
    def requirements(self):
        return {'research_decisions': {'items': [{'id':'q14','claims':[{'id':'q14.c02'}]}]}}
    def check(self, reference='q14.c02'):
        return {'key':'evidence_support','status':'fail','reason':'対象主張の照合が必要',
                'affected_blocks':[{'id':'block-1','reason':'調査主体の照合'}],
                'requires_source_check':False,'research_ids':[reference],'source_urls':['https://official.example/study']}
    def validate(self, checks, requirements=None):
        validate_routes(checks,[{'id':'block-1'}],requirements or self.requirements(),
                        [{'url':'https://official.example/study','text':'原文'}],('evidence_support',))
    def test_real_child_address_routes_to_owned_question_and_source_proof_not_a_pass(self):
        c=self.check();before=copy.deepcopy(c);self.validate([c])
        self.assertEqual(c['research_ids'],['q14']);self.assertTrue(c['requires_source_check'])
        for key in ('status','reason','affected_blocks','source_urls'):self.assertEqual(c[key],before[key])
    def test_unknown_claim_ambiguous_owner_or_unseen_block_is_not_guessed(self):
        for kind in ('unknown','ambiguous','block'):
            c=self.check();r=self.requirements()
            if kind=='unknown':c['research_ids']=['q14.c99']
            elif kind=='ambiguous':r['research_decisions']['items'].append({'id':'q15','claims':[{'id':'q14.c02'}]})
            else:c['affected_blocks'][0]['id']='unseen'
            with self.subTest(kind=kind),self.assertRaises(ContentQualityError):self.validate([c],r)
    def test_non_factual_findings_are_not_forced_to_source_review(self):
        c=self.check('q14');c['key']='coverage'
        validate_routes([c],[{'id':'block-1'}],self.requirements(),[{'url':'https://official.example/study','text':'原文'}],())
        self.assertFalse(c['requires_source_check'])
