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
        self.assertNotIn('status',root['properties']['checks']['items']['properties'])
        self.assertNotIn('affected_blocks',root['properties']['checks']['items']['properties'])
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
        validate_claim_verdicts(decoded['checks'],self.candidates)
        self.assertEqual(decoded['checks'][0]['status'],'fail')
        self.assertEqual(decoded['checks'][0]['affected_blocks'],[{'id':self.ids[0],'reason':'条件が一致しない'}])
    def test_followup_schema_requires_only_still_unresolved_ids(self):
        pending=copy.deepcopy(self.candidates);pending[0]['affected_blocks']=pending[0]['affected_blocks'][:1]
        schema=claim_decision_schema(audit_output_config(('evidence_support',),locations=True),pending)
        row=schema['format']['schema']['properties']['claim_decisions']['properties']['evidence_support']
        self.assertEqual(row['required'],[self.ids[0]])

    def test_all_four_failures_survive_summary_that_only_names_one(self):
        ids=self.ids[:4]
        value=copy.deepcopy(self.value)
        value['checks'][0].update(status='fail',affected_blocks=[{'id':ids[0],'reason':'only one listed'}])
        for key in ids:value['claim_decisions']['evidence_support'][key]={'status':'fail','reason':'condition missing '+key}
        decoded=decoded_claim_decisions(value,self.candidates)
        self.assertEqual({x['id'] for x in decoded['checks'][0]['affected_blocks']},set(ids))
        self.assertEqual(decoded['checks'][0]['status'],'fail')
        self.assertEqual(len(decoded['checks'][0]['claim_verdicts']),61)
    def test_individual_passes_override_stale_fail_summary(self):
        value=copy.deepcopy(self.value)
        value['checks'][0].update(status='fail',affected_blocks=[{'id':self.ids[0],'reason':'stale'}])
        decoded=decoded_claim_decisions(value,self.candidates)
        self.assertEqual(decoded['checks'][0]['status'],'pass')
        self.assertEqual(decoded['checks'][0]['affected_blocks'],[])
    def test_production_response_has_no_second_status_or_target_list(self):
        value=copy.deepcopy(self.value)
        for key in ('status','affected_blocks'):value['checks'][0].pop(key)
        value['claim_decisions']['evidence_support'][self.ids[-1]]={'status':'fail','reason':'missing condition'}
        decoded=decoded_claim_decisions(value,self.candidates)
        self.assertEqual(decoded['checks'][0]['affected_blocks'],[{'id':self.ids[-1],'reason':'missing condition'}])
    def test_invalid_individual_decision_never_becomes_pass(self):
        for row in ({'status':'unknown','reason':'pending'},{'status':'pass','reason':''},{'status':'pass'}):
            value=copy.deepcopy(self.value);value['claim_decisions']['evidence_support'][self.ids[0]]=row
            with self.assertRaises(ContentQualityError):decoded_claim_decisions(value,self.candidates)

    def test_unrouted_summary_cannot_steal_or_hide_routed_failure(self):
        value=copy.deepcopy(self.value)
        value['checks'].append({'key':'comparison_conditions','status':'fail','reason':'other category','affected_blocks':[{'id':self.ids[0],'reason':'other category'}]})
        value['claim_decisions']['evidence_support'][self.ids[0]]={'status':'fail','reason':'same block requires correction'}
        decoded=decoded_claim_decisions(value,self.candidates)
        routed=next(c for c in decoded['checks'] if c['key']=='evidence_support')
        self.assertEqual(routed['affected_blocks'],[{'id':self.ids[0],'reason':'same block requires correction'}])
