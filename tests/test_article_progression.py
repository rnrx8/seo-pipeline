"""Actual chapter scopes and fail aggregation, not prose-quality proof."""
import copy
import unittest
from unittest.mock import patch

from pipeline import article_design as ad, article_progression as ap
from pipeline import content_quality as cq, focused_quality as fq
from pipeline.tiered_research import structured_review_output_limit
from test_editorial_blueprint import artifact
from test_reader_progression import chain
import test_stage5_authoring as stage5


def fixture():
    _, _, _, context, blueprint = chain()
    # A H2's own introduction can be short; the answer may be in its H3.
    child = copy.deepcopy(blueprint['sections'][0])
    child.update(id='s10', parent_id='s01', level=3, title='用途の具体例',
                 reader_progression=None, cta='', budget={'chars': 0, 'importance': 0, 'reason': ''})
    blueprint['sections'].insert(1, child)
    saved = artifact(blueprint, context)
    design = ad.design_context(saved)
    text = '# 選ぶための案内\n\n用途を整理します。\n\n' + '\n\n'.join(
        '#'*s['level']+' '+s['title']+'\n\n'+s['id']+'の実際の説明。'
        for s in blueprint['sections'])
    mapping = ad.correspondence(text, saved['content_text'], design)
    value = {'reader_progression_reviews': {
        s['id']: {'status': 'pass', 'reason': 'この章にある説明を確認した合成応答',
                  'affected_blocks': []} for s in ap.sections(design)}}
    return saved, design, text, mapping, value


class ArticleProgressionTests(unittest.TestCase):
    def test_parent_can_use_child_answer_but_not_later_sibling(self):
        _, design, text, mapping, value = fixture()
        rows = {r['blueprint_id']: r for r in mapping['sections']}
        self.assertTrue(set(rows['s10']['block_ids']) <= set(rows['s01']['review_block_ids']))
        self.assertFalse(set(rows['s02']['block_ids']) & set(rows['s01']['review_block_ids']))
        value['reader_progression_reviews']['s01'].update(status='fail', affected_blocks=[{
            'id': rows['s10']['block_ids'][-1], 'reason': 'ここで用途に応じて選ぶ説明を補う'}])
        self.assertEqual(ap.checked(value, design, mapping, text)[0]['section_id'], 's01')
        value['reader_progression_reviews']['s01']['affected_blocks'][0]['id'] = rows['s02']['block_ids'][-1]
        with self.assertRaises(cq.ContentQualityError):
            ap.checked(value, design, mapping, text)

    def test_missing_extra_or_unbound_chapter_results_cannot_be_accepted(self):
        _, design, text, mapping, value = fixture()
        for change in ('missing', 'extra', 'no_location', 'pass_with_location', 'blank_reason'):
            broken = copy.deepcopy(value)
            row = broken['reader_progression_reviews']['s01']
            if change == 'missing':
                broken['reader_progression_reviews'].pop('s01')
            elif change == 'extra':
                broken['reader_progression_reviews']['unknown'] = copy.deepcopy(row)
            elif change == 'no_location':
                row['status'] = 'fail'
            elif change == 'pass_with_location':
                row['affected_blocks'] = [{'id': 'block-0000', 'reason': '位置だけある'}]
            else:
                row['reason'] = ''
            with self.subTest(change=change), self.assertRaises(cq.ContentQualityError):
                ap.checked(broken, design, mapping, text)

    def test_absent_chapter_cannot_be_passed_or_repaired_in_unrelated_chapter(self):
        _, design, text, mapping, value = fixture()
        row = next(r for r in mapping['sections'] if r['blueprint_id'] == 's02')
        row.update(article_section_ids=[], block_ids=[], review_block_ids=[])
        with self.assertRaises(cq.ContentQualityError):
            ap.checked(value, design, mapping, text)
        value['reader_progression_reviews']['s02'].update(status='fail', affected_blocks=[{
            'id': 'block-0000', 'reason': '不足する章を記事に補う'}])
        self.assertEqual(ap.checked(value, design, mapping, text)[0]['section_id'], 's02')

    def test_null_progression_does_not_add_a_forced_empathy_review(self):
        _, design, _, _, _ = fixture()
        config = ad.review_config(cq.audit_output_config(('coverage',), locations=True), design)
        props = config['format']['schema']['properties']['reader_progression_reviews']['properties']
        self.assertNotIn('s10', props)
        self.assertEqual(set(props), {s['id'] for s in ap.sections(design)})
        plain = copy.deepcopy(design)
        for section in plain['sections']:
            section['reader_progression'] = None
        self.assertNotIn('reader_progression_reviews', ad.review_config(
            cq.audit_output_config(('coverage',), locations=True), plain)['format']['schema']['properties'])

    def test_local_chapter_failure_overrides_global_pass_in_same_existing_call(self):
        original = stage5.decision_result

        def reply(design, text, missing=None):
            result = original(design, text, missing)
            mapped = ad.correspondence(text, '\n\n'.join(
                '#'*(s['level']+1)+' H'+str(s['level'])+'：'+s['title'] for s in design['sections']), design)
            target = mapped['sections'][0]['review_block_ids'][-1]
            result['reader_progression_reviews']['s01'].update(status='fail',
                reason='一覧はあるが、この章で選べるための説明がない',
                affected_blocks=[{'id': target, 'reason': 'どの用途なら何を選ぶかを説明する'}])
            return result

        with patch.object(stage5, 'decision_result', side_effect=reply):
            report, requests, _ = stage5.Stage5AuthoringTests().audit_fixture('tiered', 'legacy')
        self.assertEqual(len(requests), len(fq.ROLES))
        self.assertFalse(report['valid'])
        phase = report['phases']['coverage']
        self.assertTrue(all(r['status'] == 'pass' for r in phase['decision_reviews'].values()))
        self.assertEqual(phase['checks'][0]['status'], 'fail')
        self.assertTrue(phase['checks'][0]['research_ids'])
        self.assertTrue(any(f.get('origin') == 'individual_article_progression_review'
                            for f in report['editorial_findings']))

    def test_existing_output_reservation_includes_article_chapter_verdicts(self):
        _, design, _, _, _ = fixture()
        request = {'model': 'gpt-6.1-sol', 'max_tokens': 1000,
                   'output_config': ad.review_config(cq.audit_output_config(('coverage',)), design)}
        self.assertGreaterEqual(structured_review_output_limit(request),
                                4000 + 400*len(ap.sections(design)))

    def test_policy_change_invalidates_prior_whole_article_acceptance(self):
        saved, design, text, _, _ = fixture()
        before = cq.snapshot(text, '', saved['content_text'], {}, {'article_design': design})
        with patch.object(ad, 'REVIEW_POLICY', ad.REVIEW_POLICY+' changed chapter criteria'):
            after = cq.snapshot(text, '', saved['content_text'], {}, {'article_design': design})
        self.assertNotEqual(before, after)
