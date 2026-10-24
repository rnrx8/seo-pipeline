from heading_review_fixture import with_heading_reviews
from reader_journey_fixture import article_journeys
import copy
import json
import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import heading_scope as scope, editorial_findings, content_quality as cq
from pipeline.content_edits import content_blocks
from test_editorial_recovery import materials
from pipeline.article_design import design_context


class HeadingScopeTests(unittest.TestCase):
    def fixture(self):
        article, outline, _ = materials()
        groups = scope.groups(article['content_text'], outline['content_text'], design_context(outline))
        response = {'heading_scope_reviews': {p['id']: {c['id']: {'relation': 'answer', 'reason': '親の問いへ回答する合成確認'}
                                                     for c in p['children']} for p in groups}}
        return article['content_text'], groups, response

    def test_real_hierarchy_and_addresses_reach_only_parent_child_group(self):
        text, groups, response = self.fixture()
        self.assertEqual([g['id'] for g in groups], ['s01'])
        self.assertEqual([c['id'] for c in groups[0]['children']], ['s02'])
        self.assertTrue(groups[0]['children'][0]['block_ids'])
        self.assertEqual(scope.checked(response, groups, text), [])

    def test_next_decision_overrides_general_prose_pass_with_outline_finding(self):
        text, groups, response = self.fixture()
        response['heading_scope_reviews']['s01']['s02'] = {'relation': 'next_decision', 'reason': '方法を選んだ後の準備であり別の問い'}
        findings = scope.checked(response, groups, text)
        result = scope.merge([{'key': 'prose_quality', 'status': 'pass', 'reason': '全体では見落とした合成応答', 'affected_blocks': []}], findings)
        self.assertEqual(result[0]['status'], 'fail')
        self.assertEqual(findings[0]['stage_to_fix'], 'outline')
        editorial_findings.checked_findings({'editorial_findings': findings}, result, text)

    def test_helpful_qualification_is_not_a_required_new_chapter(self):
        text, groups, response = self.fixture()
        response['heading_scope_reviews']['s01']['s02']['relation'] = 'qualification'
        self.assertEqual(scope.checked(response, groups, text), [])

    def test_missing_unknown_or_incomplete_review_cannot_be_accepted(self):
        for change in ('missing', 'unknown', 'relation', 'reason'):
            text, groups, response = self.fixture()
            if change == 'missing':response = {}
            if change == 'unknown':response['heading_scope_reviews']['s99'] = {}
            if change == 'relation':response['heading_scope_reviews']['s01']['s02']['relation'] = 'guess'
            if change == 'reason':response['heading_scope_reviews']['s01']['s02']['reason'] = ''
            with self.subTest(change=change), self.assertRaises(cq.ContentQualityError):scope.checked(response, groups, text)

    def test_schema_does_not_mutate_original_or_add_review_when_no_children(self):
        original = {'format': {'schema': {'properties': {}, 'required': []}}}
        _, groups, _ = self.fixture()
        scoped = scope.output_config(original, groups)
        self.assertIn('heading_scope_reviews', scoped['format']['schema']['required'])
        self.assertEqual(original['format']['schema']['required'], [])
        self.assertEqual(scope.output_config(original, []), original)

    def test_global_pass_cannot_hide_missing_parent_child_review_at_final_gate(self):
        from test_editorial_recovery import passed
        article, outline, _ = materials()
        context = dict(text=article['content_text'], outline=outline['content_text'], facts='', sources='',
                       contract={}, requirements={'article_design': design_context(outline)})
        report = passed(context)
        with self.assertRaisesRegex(cq.ContentQualityError, '親見出し'):
            cq._require_audit_strict(report, cq.snapshot(**context), stage='article', context=context)

    def test_existing_five_role_audit_promotes_individual_scope_issue_to_outline(self):
        from pipeline import focused_quality as fq
        from pipeline.lead_review import KEYS
        article, outline, _ = materials(); design = design_context(outline); requests = []
        def send(job, step, request):
            role = step.removeprefix('tiered_article_'); requests.append((role, request))
            payload = json.loads(request['messages'][0]['content'])
            result = {'checks': [{'key': k, 'status': 'pass', 'reason': '合成応答', 'affected_blocks': []} for k in fq.ROLES[role]]}
            if role != 'evidence':result['editorial_findings'] = []
            if role == 'coverage':
                result.update(decision_reviews={d['id']: {'status': 'pass', 'reason': '合成確認', 'missing_requirements': [], 'affected_blocks': []}
                                               for d in design['reader_decisions']},
                    lead_review={k: {'status': 'pass', 'reason': '合成確認', 'affected_blocks': []} for k in KEYS},
                    reader_progression_reviews={s['id']: {'status': 'pass', 'reason': '合成確認', 'affected_blocks': [],'journey':article_journeys(design,article['content_text'],outline['content_text'])[s['id']]}
                                                for s in design['sections']})
            if role == 'organization':
                self.assertIn(scope.POLICY, request['system'])
                self.assertIn('heading_scope_reviews', request['output_config']['format']['schema']['required'])
                result['heading_scope_reviews'] = {p['id']: {c['id']: {'relation': 'next_decision', 'reason': '準備は方法を選ぶ次の問い'}
                                                                           for c in p['children']} for p in payload['heading_scope_groups']}
            else:self.assertNotIn('heading_scope_groups', payload)
            return with_heading_reviews(result, request), SimpleNamespace(input_tokens=1, output_tokens=1)
        with patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER': 'tiered', 'QUALITY_RESEARCH_ROUTING': 'legacy'}), \
             patch('pipeline.tiered_research.checked_request', side_effect=send):
            report = fq.audit_article(None, text=article['content_text'], facts='', outline=outline['content_text'],
                contract={}, requirements={'article_design': design}, sources='')
        self.assertEqual(len(requests), 5)
        self.assertFalse(report['valid'])
        self.assertEqual(report['phases']['organization']['editorial_findings'][0]['stage_to_fix'], 'outline')
        self.assertIn('heading_scope_reviews', report['phases']['organization'])


if __name__ == '__main__':unittest.main()
