"""Saved condition sources reach real review inputs; model verdicts are fixtures."""
import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import focused_research as focused, research_requirements as req
from pipeline import tiered_research as tiered
from pipeline.generation_context import decision_bundle
from test_claim_evidence import fixture


class LinkedFeatureConditionTests(unittest.TestCase):
    def scenario(self):
        plan, _, result = fixture()
        plan['items'][0]['question'] = 'Aのプライバシー機能と利用できる範囲'
        overview = {'url': 'https://a.example/', 'status': 'success',
                    'text': '写真のぼかしと知人非表示に対応しています。',
                    'links': [{'url': 'https://a.example/price'},
                              {'url': 'https://other.example/advertisement'},
                              {'url': 'https://a.example/not-fetched'}]}
        price = {'url': 'https://a.example/price', 'status': 'success',
                 'text': '知人非表示は女性全員と男性スタンダード以上が利用できます。',
                 'links': [{'url': 'https://a.example/faq/privacy'}]}
        faq = {'url': 'https://a.example/faq/privacy', 'status': 'success',
               'text': '登録した電話番号の相手と相互にプロフィールが非表示になります。'}
        other = {'url': 'https://other.example/advertisement', 'status': 'success',
                 'text': '無関係な商品の広告。'}
        pages = [overview, price, faq, other]
        row = result['items'][0]
        row.update(official_checked_urls=[overview['url']], claim_limits=[],
                   evidence=[{'url': overview['url'], 'quote': overview['text'],
                              'source_kind': 'primary', 'independence_group': 'A'}])
        first = row['claims'][0]
        first.update(text='写真ぼかし機能があります。', operation='写真をぼかす',
                     conditions=[], evidence_indices=[0])
        second = copy.deepcopy(first)
        second.update(text='知人非表示機能を使えます。', operation='知人を非表示にする')
        row['claims'].append(second)
        return plan, pages, result

    def test_only_already_fetched_same_host_links_are_selected(self):
        _, pages, _ = self.scenario()
        candidate = {'official_checked_urls': [pages[0]['url']], 'evidence': []}
        selected = focused.related_pages(pages, [candidate])
        self.assertEqual([p['url'] for p in selected], [p['url'] for p in pages[:3]])
        # With only the overview saved, link labels do not become source bodies.
        self.assertEqual(focused.related_pages(pages[:1], [candidate]), pages[:1])

    def test_raw_link_catalog_selects_only_the_visible_body_revision(self):
        _, pages, _ = self.scenario()
        packed = [{'url': p['url'], 'status': 'success', 'text': '今回の抜粋'} for p in pages]
        before = copy.deepcopy(pages)
        selected = focused.related_pages(packed, [{'evidence': [{'url': pages[0]['url']}]}],
                                         source_catalog=pages)
        self.assertEqual([p['url'] for p in selected], [p['url'] for p in pages[:3]])
        self.assertTrue(all(p['text'] == '今回の抜粋' for p in selected))
        self.assertEqual(pages, before)

    def test_unavailable_primary_body_keeps_saved_alternative_sources(self):
        _, pages, _ = self.scenario()
        pages[0].update(status='failed', text='', links=[])
        self.assertEqual(focused.related_pages(pages, [{'evidence': [{'url': pages[0]['url']}]}]), pages)

    def test_normal_focused_audit_sees_uncited_conditions_and_keeps_them_in_fact_sheet(self):
        plan, pages, template = self.scenario()
        calls = []

        def ask(job, step, request, **kwargs):
            payload = json.loads(request['messages'][0]['content'])
            calls.append((step, payload))
            if step == 'tiered_coverage':
                claim = payload['decisions'][0]['claims'][1]
                self.assertEqual(claim['conditions'][1]['value'], '男性スタンダード以上')
                return {'coverage_sufficient': True, 'coverage_reason': 'fixture',
                        'coverage_issues': []}, SimpleNamespace(input_tokens=1, output_tokens=1)
            row = copy.deepcopy(template['items'][0])
            row.pop('id')
            # The screen cites only the overview. Its saved linked condition
            # pages must still reach the independent audit without expansion.
            sources = payload['sources']
            if step.endswith('_audit'):
                self.assertEqual({p['url'] for p in sources}, {p['url'] for p in pages[:3]})
                self.assertIn(pages[1]['text'], json.dumps(sources, ensure_ascii=False))
                self.assertIn('機能の存在と、読者がその機能を使える条件を分ける', request['system'])
                row['claims'][1].update(text=pages[1]['text'], evidence_indices=[1, 2], conditions=[
                    {'dimension': 'audience', 'value': '女性全員'},
                    {'dimension': 'plan', 'value': '男性スタンダード以上'}])
                cited = sources
            else:
                cited = [sources[0]]
            row['evidence'] = [dict(source_ref=p['excerpts'][0]['source_ref'], source_kind='primary',
                independence_group='A', expert_source_ref='', expert_name='', expert_scope_reason='') for p in cited]
            props = request['output_config']['format']['schema']['properties']['items']['properties']['q1']['properties']
            if 'additional_sources_needed' in props:
                row['additional_sources_needed'] = False
            return {'items': {'q1': row}}, SimpleNamespace(input_tokens=1, output_tokens=1)

        with patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER': 'tiered', 'QUALITY_RESEARCH_ROUTING': 'focused'}), \
                patch.object(tiered, 'get_optional_artifact', return_value=None), \
                patch.object(tiered, 'checked_request', side_effect=ask):
            result, _ = tiered.audit_matrix('offline', plan, pages, '', {})
            self.assertEqual([s for s, _ in calls], ['tiered_screen_1', 'tiered_focused_1_audit', 'tiered_coverage'])
            self.assertFalse(req.validate_matrix(result, plan, pages))
            facts = req.accepted_facts(result, plan)
            self.assertIn('男性スタンダード以上', facts)
            result.update(valid=True, policy_sha256=req.matrix_policy())
            packet = decision_bundle(plan, result, quotes=True)
            self.assertIn('男性スタンダード以上', packet)
            self.assertIn(pages[1]['url'], packet)

    def test_unresolved_optional_feature_does_not_erase_known_photo_feature(self):
        plan, pages, result = self.scenario()
        row = result['items'][0]
        row['claims'][1].update(required_for_answer=False, disposition='withhold', evidence_indices=[],
            conflict={'status': 'unresolved', 'evidence_indices': [0], 'resolution': '利用プランが未確認'})
        row['claim_limits'] = ['知人非表示が使える条件を断定せず、本稿の推薦理由に使わない']
        self.assertFalse(req.validate_matrix(result, plan, pages))
        self.assertEqual(row['answer'], '写真ぼかし機能があります。')
        self.assertFalse(row['claim_states'][1]['publishable'])
        # If the feature is essential to this answer, the same unknown fails.
        row['claims'][1]['required_for_answer'] = True
        self.assertTrue(req.validate_matrix(result, plan, pages))
        self.assertFalse(row['verified'])


if __name__ == '__main__':
    unittest.main()
