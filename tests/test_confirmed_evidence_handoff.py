"""Verified claims survive incomplete sibling claims without approving the question."""
import copy
import json
import os
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import claim_evidence as ce, research_requirements as req
from pipeline import generation_context as gc, editorial_blueprint as bp
from pipeline import quality_warnings as warnings
from pipeline import quality_context as qc
from pipeline.content_quality import ContentQualityError
from pipeline.source_spans import expand_references
from pipeline import focused_research as focused, tiered_research as tiered
from test_editorial_blueprint import fixture


class ConfirmedEvidenceHandoffTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'ARTICLE_REVIEW_PROVIDER': 'tiered', 'QUALITY_RESEARCH_ROUTING': 'focused'})
        self.env.start(); self.addCleanup(self.env.stop)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('offline only'))
        network.start(); self.addCleanup(network.stop)

    def mixed(self):
        plan, pages, matrix, _, blueprint = fixture()
        item = matrix['items'][0]
        missing = copy.deepcopy(item['claims'][0])
        missing.update(text='未確認の地域検索と無料利用条件', operation='地域検索',
                       evidence_indices=[], disposition='withhold', required_for_answer=True)
        item['claims'].append(missing)
        item['additional_sources_needed'] = True
        req.validate_matrix(matrix, plan, pages)
        matrix.update(valid=False, attempt=3, policy_sha256=req.matrix_policy())
        return plan, pages, matrix, blueprint

    def delivery(self, fn):
        # Only the warning persistence is replaced; the projection remains native.
        with patch.object(warnings, 'warn'):
            return warnings.scoped(lambda _: fn(), 'outline')('offline-job')

    def test_verified_claims_survive_an_unknown_required_sibling(self):
        plan, _, matrix, _ = self.mixed()
        original = copy.deepcopy(matrix)
        facts = req.accepted_facts(matrix, plan)
        self.assertIn('男性スタンダードは閲覧できます。', facts)
        self.assertIn('送信はプレミアムのみです。', facts)
        self.assertNotIn('未確認の地域検索', facts)
        data = self.delivery(lambda: gc.bundle_data(plan, matrix, quotes=True))
        self.assertEqual([c['id'] for c in data['decisions'][0]['claims']], ['q1.c01', 'q1.c02'])
        self.assertEqual(data['decisions'][0]['claims'][1]['source_ids'], ['ref2'])
        self.assertFalse(matrix['valid']); self.assertFalse(matrix['items'][0]['verified'])
        self.assertEqual(matrix, original)

    def test_outline_and_repair_receive_the_same_claims_conditions_and_sources(self):
        plan, _, matrix, blueprint = self.mixed()
        ctx = self.delivery(lambda: bp.context(plan, matrix))
        bp.validate(blueprint, ctx)
        self.assertEqual(set(ctx['accepted_claims']), {'q1.c01', 'q1.c02'})
        repaired = gc.repair_requirements({'research_plan': plan, 'research_decisions': matrix})
        row = repaired['research_decisions']['items'][0]
        self.assertTrue(row['publishable'])
        self.assertEqual([c['id'] for c in row['claims']], ['q1.c01', 'q1.c02'])
        self.assertEqual(row['claims'][0]['source_indices'],[0])
        self.assertEqual(row['claims'][1]['source_indices'],[1])
        self.assertIn('プレミアム', json.dumps(row['claims'][1]['conditions'], ensure_ascii=False))
        self.assertNotIn('未確認の地域検索', json.dumps(row, ensure_ascii=False))
        self.assertEqual(repaired, gc.repair_requirements(repaired))

    def test_compact_qa_to_repair_keeps_the_same_supported_units(self):
        plan, _, matrix, _ = self.mixed()
        original=copy.deepcopy(matrix)
        compact=qc.review_requirements({'research_plan':plan,'research_decisions':matrix},'evidence')
        self.assertNotIn('claim_states',compact['research_decisions']['items'][0])
        repaired=gc.repair_requirements(compact)
        row=repaired['research_decisions']['items'][0]
        self.assertTrue(row['publishable'])
        self.assertEqual([c['id'] for c in row['claims']],['q1.c01','q1.c02'])
        self.assertIn('プレミアム',json.dumps(row['claims'][1]['conditions'],ensure_ascii=False))
        self.assertEqual({r['url'] for r in row['evidence']},{'https://a.example/pricing','https://a.example/faq'})
        self.assertEqual(row['claims'][1]['source_indices'],[1])
        self.assertEqual(row['evidence'][row['claims'][1]['source_indices'][0]]['url'],'https://a.example/faq')
        self.assertNotIn('未確認の地域検索',json.dumps(row,ensure_ascii=False))
        self.assertEqual(gc.repair_requirements(repaired),repaired)
        self.assertEqual(matrix,original)

    def test_compact_review_cannot_clear_a_rejected_mapping(self):
        plan,pages,matrix,_=self.mixed()
        matrix['items'][0]['claims'][0]['evidence_indices']=[999]
        req.validate_matrix(matrix,plan,pages)
        compact=qc.review_requirements({'research_plan':plan,'research_decisions':matrix},'evidence')
        self.assertTrue(compact['research_decisions']['items'][0]['claim_policy_issues'])
        repaired=gc.repair_requirements(compact)
        self.assertFalse(repaired['research_decisions']['items'][0]['publishable'])
        self.assertEqual(repaired['research_decisions']['items'][0]['claims'],[])

    def test_real_saved_reviews_keep_only_explicitly_publishable_claims(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/confirmed-evidence-handoff-20261027.json').read_text())['cases']
        for case in cases:
            with self.subTest(question=case['question']['id']):
                value = expand_references(copy.deepcopy(case['raw_response']), case['source_index'])
                plan = {'items': [case['question']]}
                self.assertTrue(req.validate_matrix(value, plan, case['pages']))
                item = value['items'][0]
                self.assertFalse(item['verified'])
                self.assertEqual([r['operation'] for r in item['claim_states'] if r['publishable']],
                                 case['expected_kept_operations'])
                value.update(valid=False, attempt=3, policy_sha256=req.matrix_policy())
                facts = req.accepted_facts(value, plan)
                data = self.delivery(lambda: gc.bundle_data(plan, value, quotes=True))
                self.assertTrue(data['decisions'])
                self.assertTrue(facts)
                self.assertEqual([r['operation'] for r in data['decisions'][0]['claims']],
                                 case['expected_kept_operations'])
                expected_ids=[f"{item['id']}.c{n:02d}" for n,c in enumerate(item['claims'],1)
                              if c['disposition'] in ('publish','qualified')]
                self.assertEqual([r['id'] for r in data['decisions'][0]['claims']],expected_ids)
                for compact in (False,True):
                    packet={'research_plan':plan,'research_decisions':value}
                    if compact:packet=qc.review_requirements(packet,'evidence')
                    fixed=gc.repair_requirements(packet)['research_decisions']['items'][0]
                    self.assertEqual([r['id'] for r in fixed['claims']],expected_ids)
                    for claim in fixed['claims']:
                        expected=next(r for r in data['decisions'][0]['claims'] if r['id']==claim['id'])
                        self.assertEqual(claim['conditions'],expected['conditions'])
                        refs=fixed.get('evidence',fixed.get('evidence_sources',[]))
                        self.assertEqual({refs[n]['url'] for n in claim['source_indices']},
                                         {data['sources'][ref]['url'] for ref in expected['source_ids']})
                for row in item['claim_states']:
                    self.assertEqual(row['text'] in facts, row['publishable'])

    def test_bounded_focused_review_does_not_erase_accepted_parts_after_a_source_gap(self):
        plan,pages,matrix,_=self.mixed()
        original=copy.deepcopy(matrix);calls=[];template=matrix['items'][0]
        def respond(job,step,request):
            calls.append((step,request['model']))
            payload=json.loads(request['messages'][0]['content'])
            fields=('id','reason','official_checked_urls','applicable_at','omission_reason',
                    'claim_limits','exploration_complete','exploration_reason','claims','additional_sources_needed')
            row={k:copy.deepcopy(template[k]) for k in fields}
            row['evidence']=[]
            for ref in template['claim_evidence']:
                page=next(p for p in payload['sources'] if p['url']==ref['url'])
                span=next(e for e in page['excerpts'] if ref['quote'] in e['text'])
                row['evidence'].append({k:copy.deepcopy(v) for k,v in ref.items() if k not in ('quote','url')}
                    | {'source_ref':span['source_ref'],'expert_source_ref':''})
            return {'items':[row],'coverage_sufficient':True,'coverage_reason':'per-claim review'},SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch.object(tiered,'checked_request',side_effect=respond):
            result,_=focused.review_pending('offline-job',1,matrix,plan['items'],
                {'items':plan['items']},{},pages,pages,[])
        self.assertEqual([model for _,model in calls],['gpt-6-luna','gpt-6.1-sol'])
        self.assertTrue(req.validate_matrix(result,plan,pages))
        self.assertIn('男性スタンダードは閲覧できます。',req.accepted_facts(result,plan))
        self.assertNotIn('未確認の地域検索',req.accepted_facts(result,plan))
        self.assertFalse(result['items'][0]['verified'])
        self.assertTrue(result['items'][0]['additional_sources_needed'])
        self.assertEqual(matrix,original)

    def test_unknown_or_conflicting_claim_cannot_be_promoted(self):
        plan, pages, matrix, _ = self.mixed()
        for claim in matrix['items'][0]['claims']:
            claim['disposition'] = 'withhold'
        req.validate_matrix(matrix, plan, pages)
        self.assertEqual(req.accepted_facts(matrix, plan), '')
        self.assertEqual(self.delivery(lambda: gc.bundle_data(plan, matrix, quotes=True))['decisions'], [])

    def test_partial_evidence_does_not_clear_strict_completeness(self):
        plan, pages, matrix, _ = self.mixed()
        self.assertTrue(req.validate_matrix(matrix, plan, pages))
        with self.assertRaises(ContentQualityError): gc.bundle_data(plan, matrix, quotes=True)
        self.assertFalse(matrix['items'][0]['verified'])

    def test_bad_quote_conflict_or_unseen_evidence_stays_out_of_the_partial_view(self):
        for defect in ('quote', 'conflict', 'visibility', 'indices'):
            with self.subTest(defect=defect):
                plan, pages, matrix, _ = self.mixed()
                item = matrix['items'][0]
                if defect == 'quote': item['claim_evidence'][0]['quote'] = '架空の引用'
                if defect == 'conflict':
                    item['claims'][0]['conflict'] = dict(status='unresolved', evidence_indices=[1], resolution='解決していない')
                if defect == 'visibility': item['claim_visibility_failed'] = ['q1.c01']
                if defect == 'indices': item['claims'][0]['evidence_indices'] = [999]
                req.validate_matrix(matrix, plan, pages)
                facts = req.accepted_facts(matrix, plan)
                self.assertNotIn('男性スタンダードは閲覧できます。', facts)
                self.assertNotIn('未確認の地域検索', facts)
                self.assertFalse(item['verified'])
                self.assertFalse(matrix['valid'])

    def test_optional_unknown_does_not_erase_supported_answer_or_force_an_omission(self):
        plan, pages, matrix, _ = self.mixed()
        plan['items'][0].update(required=False, priority='supporting')
        original = copy.deepcopy(matrix['items'][0])
        req.propose_optional_omissions(matrix['items'], plan, pages)
        self.assertEqual(matrix['items'][0], original)
        self.assertTrue(req.accepted_facts(matrix, plan))

    def test_evidence_ledger_distinguishes_claim_support_from_question_completion(self):
        _, _, matrix, _ = self.mixed()
        rows = matrix['evidence_ledger']['claims']
        self.assertEqual([r['publishable'] for r in rows], [True, True, False])
        self.assertTrue(all(r['question_verified'] is False for r in rows))
        self.assertIn('スタンダード', json.dumps(rows[0]['conditions'], ensure_ascii=False))

    def test_partial_projection_is_not_an_escape_from_a_stale_policy(self):
        plan, _, matrix, _ = self.mixed()
        matrix['policy_sha256'] = 'previous-policy'
        data = self.delivery(lambda: gc.bundle_data(plan, matrix, quotes=True))
        self.assertEqual(data['decisions'], [])
        self.assertEqual(data['sources'], {})


if __name__ == '__main__': unittest.main()
