"""Source review transport completeness without inferred claim decisions."""
import copy
import unittest
from pipeline.content_quality import ContentQualityError, audit_output_config
from pipeline.tiered_evidence import claim_decision_schema, decoded_claim_decisions, validate_claim_verdicts

class SourceDecisionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.ids=[f'block-{i:04d}' for i in range(61)]
        self.candidates=[{'key':'evidence_support','status':'fail','affected_blocks':[{'id':i,'reason':'原文照合'} for i in self.ids]}]
        self.value={'checks':[{'key':'evidence_support','status':'pass','affected_blocks':[], 'reason':'照合済み'}],
                    'claim_decisions':{'evidence_support':{i:{'status':'pass','reason':'原文と一致'} for i in self.ids}}}
    def test_all_61_ids_are_required_without_extra_properties(self):
        schema=claim_decision_schema(audit_output_config(('evidence_support',),locations=True),self.candidates)
        root=schema['format']['schema'];self.assertIn('claim_decisions',root['required'])
        row=root['properties']['claim_decisions']['properties']['evidence_support']
        self.assertEqual(set(row['required']),set(self.ids));self.assertFalse(row['additionalProperties'])
        self.assertNotIn('claim_verdicts',root['properties']['checks']['items']['properties'])
    def test_explicit_decisions_retain_existing_receipt_format(self):
        before=copy.deepcopy(self.value)
        decoded=decoded_claim_decisions(self.value,self.candidates)
        validate_claim_verdicts(decoded['checks'],self.candidates)
        self.assertEqual({r['id'] for r in decoded['checks'][0]['claim_verdicts']},set(self.ids))
        self.assertEqual(self.value,before)
    def test_missing_and_unrequested_ids_are_never_filled(self):
        for mode in ('missing','extra'):
            with self.subTest(mode=mode):
                value=copy.deepcopy(self.value)
                if mode=='missing':value['claim_decisions']['evidence_support'].pop(self.ids[0])
                else:value['claim_decisions']['evidence_support']['block-9999']={'status':'pass','reason':'extra'}
                with self.assertRaises(ContentQualityError):decoded_claim_decisions(value,self.candidates)
    def test_pass_summary_cannot_override_failed_claim(self):
        value=copy.deepcopy(self.value)
        value['claim_decisions']['evidence_support'][self.ids[0]]={'status':'fail','reason':'条件が一致しない'}
        decoded=decoded_claim_decisions(value,self.candidates)
        with self.assertRaises(ContentQualityError):validate_claim_verdicts(decoded['checks'],self.candidates)
    def test_followup_schema_requires_only_still_unresolved_ids(self):
        pending=copy.deepcopy(self.candidates);pending[0]['affected_blocks']=pending[0]['affected_blocks'][:1]
        schema=claim_decision_schema(audit_output_config(('evidence_support',),locations=True),pending)
        row=schema['format']['schema']['properties']['claim_decisions']['properties']['evidence_support']
        self.assertEqual(row['required'],[self.ids[0]])
