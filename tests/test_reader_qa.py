"""Context ownership, frozen result provenance and normal-route integration."""
import copy
import os
import unittest
from unittest.mock import patch
from pipeline import reader_qa as rq, content_quality as cq, article_design as ad
from pipeline.content_edits import content_blocks
from pipeline.tiered_research import structured_review_output_limit
from test_article_progression import fixture
import test_stage5_authoring as stage5
from reader_journey_fixture import journey


@patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
class ReaderQaTests(unittest.TestCase):
    def test_actual_parent_context_is_not_child_evidence_and_needs_are_local(self):
        saved, design, text, mapping, _ = fixture()
        design['reader_needs'].append({'id': 'other', 'question': '無関係な分析'})
        packet = rq.packets(text, saved['content_text'], design)[0]
        child = next(c for c in packet['cases'] if c['id'] == 's10')
        parent = next(c for c in packet['cases'] if c['id'] == 's01')
        self.assertIsNone(child['section']['reader_progression'])
        self.assertEqual([c['section_id'] for c in child['reading_context']['ancestors']], ['s01'])
        self.assertIn('s01の実際の説明', child['reading_context']['ancestors'][0]['own_text'])
        self.assertNotIn('s01の実際の説明', '\n'.join(b['text'] for b in child['article_blocks']))
        self.assertIn('s10の実際の説明', '\n'.join(b['text'] for b in parent['article_blocks']))
        self.assertEqual({n['id'] for n in child['reader_needs']}, set(child['section']['need_ids']))
        self.assertNotIn('other', [n['id'] for n in child['reader_needs']])

    def test_batching_checks_every_heading_once_including_null_children(self):
        saved, design, text, _, _ = fixture()
        with patch.object(rq, 'BATCH_SIZE', 2): packets = rq.packets(text, saved['content_text'], design)
        self.assertEqual([c['id'] for p in packets for c in p['cases']], [s['id'] for s in design['sections']])
        self.assertEqual([len(p['cases']) for p in packets], [2, 2])

    def test_missing_need_is_rejected_and_legacy_context_is_not_invented(self):
        saved, design, text, _, _ = fixture()
        design['reader_needs'] = [{'id': 'unrelated'}]
        with self.assertRaises(cq.ContentQualityError): rq.packets(text, saved['content_text'], design)
        design.pop('reader_needs')
        case = rq.packets(text, saved['content_text'], design)[0]['cases'][0]
        self.assertEqual(case['reader_needs'], [])
        self.assertEqual(case['reader_context'], design['reader_focus'])

    def test_only_current_chapter_can_provide_quoted_proof(self):
        saved, design, text, _, _ = fixture()
        packet = rq.packets(text, saved['content_text'], design)[0]
        values = {c['id']: journey({b['id']: [b['text']] for b in c['article_blocks']}) for c in packet['cases']}
        rq.checked({'reader_journeys': values}, packet, text)
        values['s10'] = copy.deepcopy(values['s01'])
        with self.assertRaises(cq.ContentQualityError): rq.checked({'reader_journeys': values}, packet, text)
        values.pop('s10')
        with self.assertRaises(cq.ContentQualityError): rq.checked({'reader_journeys': values}, packet, text)

    def test_null_child_can_directly_answer_without_empathy_but_still_requires_guidance(self):
        saved, design, text, _, _ = fixture(); p = rq.packets(text, saved['content_text'], design)[0]
        rows = {c['id']: journey({b['id']: [b['text']] for b in c['article_blocks']}) for c in p['cases']}
        for name in ('attunement', 'lived_value'): rows['s10'][name].update(status='not_needed', reason='この節は確認済み方針を具体的な手順へ移す直接回答', evidence=[])
        self.assertEqual(rq.checked({'reader_journeys': rows}, p, text)['s10']['status'], 'pass')
        rows['s10']['guidance'].update(status='fail', reason='読者が何をするかの答えがない', evidence=[])
        self.assertEqual(rq.checked({'reader_journeys': rows}, p, text)['s10']['status'], 'fail')

    def test_strict_final_requires_all_batches_current_context_and_unaltered_verdicts(self):
        report, requests, ctx = stage5.Stage5AuthoringTests().audit_fixture('tiered', 'legacy')
        phase = report['phases']['coverage']; snap = report['snapshot']; design = ctx['requirements']['article_design']
        rq.require_trace(phase, text=ctx['text'], outline=ctx['outline'], design=design, snapshot=snap)
        for kind in ('missing', 'old_snapshot', 'packet', 'model', 'missing_section', 'changed_status', 'changed_final', 'malformed'):
            value = copy.deepcopy(phase); trace = value['reader_review_trace']
            if kind == 'missing': trace['batches'] = []
            elif kind == 'old_snapshot': trace['snapshot'] = 'old'
            elif kind == 'packet': trace['batches'][0]['packet_sha256'] = 'other context'
            elif kind == 'model': trace['batches'][0]['model'] = 'another model'
            elif kind == 'missing_section': trace['batches'][0]['reviews'].pop('s01')
            elif kind == 'changed_status': trace['batches'][0]['reviews']['s01']['status'] = 'fail'
            elif kind == 'changed_final': value['reader_progression_reviews']['s01']['reason'] = 'different'
            else: trace['batches'][0]['reviews']['s01'] = None
            with self.subTest(kind=kind), self.assertRaises(cq.ContentQualityError):
                rq.require_trace(value, text=ctx['text'], outline=ctx['outline'], design=design, snapshot=snap)
        changed = copy.deepcopy(design); changed['sections'][0]['reader_progression']['before']['emotion'] += '違う前提'
        with self.assertRaises(cq.ContentQualityError): rq.require_trace(phase, text=ctx['text'], outline=ctx['outline'], design=changed, snapshot=snap)
        changed_text = ctx['text'].replace('閲覧できます', '閲覧できません')
        with self.assertRaises(cq.ContentQualityError): rq.require_trace(phase, text=changed_text, outline=ctx['outline'], design=design, snapshot=snap)

    def test_main_coverage_does_not_duplicate_chapter_verdicts(self):
        report, requests, _ = stage5.Stage5AuthoringTests().audit_fixture('tiered', 'legacy')
        main = [r for r in requests if 'decision_reviews' in r['output_config']['format']['schema']['properties']]
        local = [r for r in requests if 'reader_journeys' in r['output_config']['format']['schema']['properties']]
        self.assertEqual(len(main), 1); self.assertEqual(len(local), 1)
        self.assertNotIn('reader_progression_reviews', main[0]['output_config']['format']['schema']['properties'])
        self.assertEqual(set(report['phases']), {'evidence', 'coverage', 'language', 'consistency', 'organization'})
        self.assertEqual(report['phases']['coverage']['input_tokens'], 2)

    def test_output_budget_and_snapshot_bind_reader_protocol(self):
        saved, design, text, _, _ = fixture(); p = rq.packets(text, saved['content_text'], design)[0]
        request = {'model': 'gpt-6.1-sol', 'max_tokens': 1000, 'output_config': rq.output_config(p)}
        self.assertGreaterEqual(structured_review_output_limit(request), 2000+2200*len(p['cases']))
        before = cq.snapshot(text, '', saved['content_text'], {}, {'article_design': design})
        with patch.object(rq, 'CONTEXT_POLICY', rq.CONTEXT_POLICY+' changed handoff'):
            self.assertNotEqual(before, cq.snapshot(text, '', saved['content_text'], {}, {'article_design': design}))
