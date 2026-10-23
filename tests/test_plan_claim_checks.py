import copy
import unittest
from pipeline.research_plan_review import review_schema, validate_claim_checks
from pipeline.content_quality import ContentQualityError


class PlanClaimChecksTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'items':[
            {'id':'initial','source_requirement':'expert_allowed','official_check_required':True},
            {'id':'legal','source_requirement':'expert_allowed','official_check_required':True}]}
        self.value = {'valid':True,'issues':[], 'claim_checks':{
            'initial':{'answer_scope':'記録の保存や相談先の案内',
                       'source_requirement':'general_guidance','official_check_required':False},
            'legal':{'answer_scope':'法的責任の線引き',
                     'source_requirement':'expert_allowed','official_check_required':True}}}

    def test_each_question_required_in_transport_schema(self):
        schema = review_schema(self.plan)['format']['schema']
        self.assertIn('claim_checks', schema['required'])
        self.assertEqual(set(schema['properties']['claim_checks']['required']), {'initial','legal'})

    def test_issue_target_cannot_use_a_reader_decision_id(self):
        from pipeline.editorial_blueprint import validate_shape
        original = copy.deepcopy(self.plan)
        schema = review_schema(self.plan)['format']['schema']
        issue = schema['properties']['issues']['items']
        for identifier in ('initial', 'legal', 'overall'):
            validate_shape({'id': identifier, 'reason': '修正が必要'}, issue)
        for identifier in ('i02', 'unlisted-question'):
            with self.assertRaises(ContentQualityError):
                validate_shape({'id': identifier, 'reason': '修正が必要'}, issue)
        self.assertEqual(self.plan, original)

    def test_overall_pass_cannot_hide_question_classification_mismatch(self):
        original = copy.deepcopy(self.plan)
        validate_claim_checks(self.value, self.plan)
        self.assertFalse(self.value['valid'])
        self.assertEqual([x['id'] for x in self.value['issues']], ['initial'])
        self.assertEqual(self.plan, original)  # no silent downgrade of saved plan

    def test_missing_question_cannot_pass(self):
        del self.value['claim_checks']['legal']
        with self.assertRaises(ContentQualityError):
            validate_claim_checks(self.value, self.plan)

    def test_matching_classifications_pass_without_weakening_expert_requirement(self):
        self.plan['items'][0].update(source_requirement='general_guidance',official_check_required=False)
        validate_claim_checks(self.value, self.plan)
        self.assertTrue(self.value['valid'])
        self.assertEqual(self.value['issues'], [])
        self.assertEqual(self.plan['items'][1]['source_requirement'],'expert_allowed')
