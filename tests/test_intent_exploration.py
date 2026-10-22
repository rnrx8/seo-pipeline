import copy
import json
import unittest
from unittest.mock import patch

from pipeline import intent_exploration as ie, reader_needs as rn, production_brief as pb
from pipeline.content_quality import ContentQualityError
from pipeline.generation_context import authoring_intent, publication_design, outline_planning_context, repair_requirements
from intent_exploration_fixtures import attach_exploration
from test_reader_value_flow import fixtures


class IntentExplorationTests(unittest.TestCase):
    def fixture(self):
        brief, material, sources, value = fixtures()
        return brief, material, sources, attach_exploration(value, sources['keyword'])

    def check(self, value, sources):
        return rn.validate(value, sources, product_material_ids=['service:point:0'],
                           has_product=True, require_exploration=True)

    def test_generation_requires_method_and_selected_branch_links(self):
        schema = rn.output_schema(new_product=False)['format']['schema']
        self.assertEqual(next(iter(schema['properties'])), 'exploration')
        self.assertIn('exploration', schema['required'])
        self.assertIn('hypothesis_ids', schema['properties']['selected']['items']['required'])
        _, _, sources, value = self.fixture()
        self.check(value, sources)
        del value['exploration']
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_wire_schema_uses_json_text_and_local_validation_restores_exact_shape(self):
        schema = ie.model_schema()
        self.assertEqual(schema['type'], 'string')
        self.assertNotIn('properties', schema)
        self.assertIn('hypothesis', schema['description'])
        self.assertIn('enum', ie.SCHEMA['properties']['mode'])
        _, _, sources, value = self.fixture()
        original = copy.deepcopy(value['exploration'])
        value['exploration'] = json.dumps(original)
        self.check(value, sources)
        self.assertEqual(value['exploration'], original)
        value['exploration']['branches'][0]['root_desire']['category'] = 'unsupported_value'
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_encoded_exploration_rejects_bad_json_or_shape_without_paid_repair(self):
        for bad in ('{bad json', '[]', '{}'):
            _, _, sources, value = self.fixture()
            value['exploration'] = bad
            with self.assertRaises(ContentQualityError): self.check(value, sources)
        _, _, sources, value = self.fixture()
        value['exploration']['branches'][0]['extra'] = 'untrusted extra'
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_new_method_does_not_relabel_legacy_saved_analysis(self):
        brief, _, sources, value = fixtures()
        legacy = rn.record(value, brief=brief, intent_sources=sources)
        self.assertNotIn('exploration', legacy)
        self.assertNotIn('analysis_method_sha256', legacy)
        self.assertNotIn('motivation_context', rn.editorial_context(brief, legacy)['reader_needs'][0])
        with patch('pipeline.db.get_optional_artifact', return_value={'content_text': json.dumps(legacy)}), \
             patch('pipeline.db.get_artifact', return_value={'content_text': sources['serp']}):
            self.assertEqual(rn.load('j', brief), legacy)

    def test_bad_source_quote_or_unlabelled_inference_is_rejected(self):
        for change in ({'kind': 'observed'}, {'source': 'serp', 'quote': '存在しない体験'},
                       {'source': 'invented-study'}):
            with self.subTest(change=change):
                _, _, sources, value = self.fixture()
                value['exploration']['branches'][0]['links'][0]['support'].update(change)
                with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_new_reader_relationship_stays_hypothesis_despite_exact_source_quote(self):
        _, _, sources, value = self.fixture()
        ref = value['exploration']['branches'][0]['links'][0]['support']
        ref.update(kind='observed', source='serp', quote=sources['serp'])
        with self.assertRaises(ContentQualityError): self.check(value, sources)
        # Historical records keep their original labels; they are not silently
        # rewritten or upgraded to fresh analysis under the tightened method.
        ie.validate(value, sources)
        ref['kind'] = 'hypothesis'
        self.check(value, sources)
        ref['quote'] = ''
        self.check(value, sources)

    def test_alternative_reader_preference_cannot_be_observed_in_new_analysis(self):
        _, _, sources, value = self.fixture()
        ref = value['exploration']['branches'][0]['alternative']['support']
        ref.update(kind='observed', source='serp', quote=sources['serp'])
        with self.assertRaises(ContentQualityError): self.check(value, sources)
        ref['kind'] = 'hypothesis'
        self.check(value, sources)

    def test_missing_links_alternative_material_or_usage_cannot_pass(self):
        for key, bad in (('links', []), ('alternative', {}), ('concrete_material', ''), ('article_effect', '')):
            with self.subTest(key=key):
                _, _, sources, value = self.fixture()
                value['exploration']['branches'][0][key] = bad
                with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_unknown_keyword_parts_and_branch_ids_are_rejected(self):
        for key in ('keyword_parts', 'hypothesis_ids'):
            _, _, sources, value = self.fixture()
            if key == 'keyword_parts': value['exploration'][key] = ['元KWにない商材']
            else: value['selected'][0][key] = ['h_missing']
            with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_decomposed_terms_must_reach_exploration_branches(self):
        _, _, sources, value = self.fixture()
        sources['keyword'] = '探し方 おすすめ'
        value['exploration']['keyword_parts'].append('おすすめ')
        with self.assertRaises(ContentQualityError): self.check(value, sources)
        value['exploration']['branches'][0]['keyword_parts'].append('おすすめ')
        self.check(value, sources)

    def test_duplicate_branch_ids_and_repeated_links_are_rejected(self):
        _, _, sources, value = self.fixture()
        value['exploration']['branches'].append(copy.deepcopy(value['exploration']['branches'][0]))
        with self.assertRaises(ContentQualityError): self.check(value, sources)
        value['exploration']['branches'].pop()
        link = value['exploration']['branches'][0]['links'][0]
        link['to'] = link['from']
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_category_label_alone_and_silent_unknown_context_are_rejected(self):
        _, _, sources, value = self.fixture()
        branch = value['exploration']['branches'][0]
        branch['root_desire']['meaning'] = ''
        with self.assertRaises(ContentQualityError): self.check(value, sources)
        branch['root_desire']['category'] = 'unknown'
        self.check(value, sources)
        branch['unknowns'] = []
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_direct_queries_need_no_fabricated_emotion_or_root_desire(self):
        _, _, sources, value = self.fixture()
        exploration = value['exploration']
        exploration.update(mode='direct', scope_reason='操作を直接案内すれば答えられ、心理で手順が変わらない')
        branch = exploration['branches'][0]
        branch['links'] = branch['links'][:1]
        branch['links'][0].update(**{'from': '必要な画面を探している', 'to': '操作の入口を案内する'})
        branch['root_desire'].update(category='not_applicable', meaning='', stop_reason='直接の操作案内に不要')
        value['selected'][0]['background_emotion'] = ''
        self.check(value, sources)
        exploration['mode'] = 'exploratory'
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_unselected_hypothesis_may_stop_early_without_inventing_more_depth(self):
        _, _, sources, value = self.fixture()
        excluded = copy.deepcopy(value['exploration']['branches'][0])
        excluded.update(id='h_excluded', links=excluded['links'][:1])
        excluded['root_desire']['stop_reason'] = '主対象外の候補なので採用せず、ここで探索を止める。'
        value['exploration']['branches'].append(excluded)
        self.check(value, sources)
        value['selected'][0]['hypothesis_ids'].append('h_excluded')
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_supporting_condition_needs_no_invented_second_motivation_link(self):
        _, _, sources, value = self.fixture()
        supporting = copy.deepcopy(value['exploration']['branches'][0])
        supporting.update(id='h_condition', links=supporting['links'][:1])
        supporting['links'][0].update(**{'from': '利用前に条件を確認したい', 'to': '対応範囲を知って方法を選びたい'})
        supporting['root_desire'].update(category='not_applicable', meaning='', stop_reason='利用条件の確認であり心理の深掘りは不要')
        value['exploration']['branches'].append(supporting)
        value['selected'][0]['hypothesis_ids'].append('h_condition')
        self.check(value, sources)
        supporting['links'] = []
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_selected_motivation_cannot_use_supporting_condition_exception(self):
        _, _, sources, value = self.fixture()
        branch = copy.deepcopy(value['exploration']['branches'][0])
        branch.update(id='h_motivation', links=branch['links'][:1])
        value['exploration']['branches'].append(branch)
        value['selected'][0]['hypothesis_ids'].append('h_motivation')
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_unselected_motivation_cannot_justify_a_shallow_selected_condition(self):
        _, _, sources, value = self.fixture()
        branch = copy.deepcopy(value['exploration']['branches'][0])
        branch.update(id='h_condition', links=branch['links'][:1])
        branch['root_desire'].update(category='not_applicable', meaning='', stop_reason='条件の確認')
        value['exploration']['branches'].append(branch)
        value['selected'][0]['hypothesis_ids'] = ['h_condition']
        with self.assertRaises(ContentQualityError): self.check(value, sources)

    def test_selected_material_survives_planning_writing_and_repair_without_raw_quotes(self):
        brief, _, sources, value = self.fixture()
        branch = value['exploration']['branches'][0]
        branch['links'][0]['support'].update(source='serp', quote=sources['serp'])
        other = copy.deepcopy(branch)
        other.update(id='h02', concrete_material='不採用の別の事情')
        value['exploration']['branches'].append(other)
        saved = rn.record(value, brief=brief, intent_sources=sources)
        context = rn.editorial_context(brief, saved)
        original = copy.deepcopy(saved)
        plan = {'reader_design': context, 'reader_decisions': [], 'content_gaps': [], 'items': []}
        projections = [context, publication_design(context), outline_planning_context(plan)['reader_design'],
                       repair_requirements({'research_plan': plan})['reader_design']]
        with patch.object(pb, 'load', return_value=brief), patch.object(rn, 'load', return_value=saved):
            projections.append(json.loads(authoring_intent('j', {'meta': {'reader_needs_version': rn.VERSION}})))
        for projected in projections:
            material = projected['reader_needs'][0]['motivation_context']
            self.assertEqual([m['hypothesis_id'] for m in material], ['h01'])
            self.assertEqual(material[0]['motivation_links'][0]['to'], branch['links'][0]['to'])
            self.assertEqual(material[0]['concrete_material'], branch['concrete_material'])
            self.assertEqual(material[0]['alternative']['difference'], branch['alternative']['difference'])
            self.assertEqual(material[0]['root_desire'], branch['root_desire'])
            self.assertNotIn('desired_experience', material[0])
            self.assertEqual(material[0]['unknowns'], branch['unknowns'])
            self.assertEqual(material[0]['status'], 'editorial_hypothesis_not_verified')
            self.assertNotIn('quote', json.dumps(material))
        self.assertEqual(saved, original)

    def test_root_classification_is_not_presented_as_a_concrete_desired_experience(self):
        _, _, _, value = self.fixture()
        branch = value['exploration']['branches'][0]
        branch['root_desire'].update(category='belonging', meaning='所属の欲求', stop_reason='分析上の分類')
        branch['concrete_material'] = '話しても受け止められず、相談をためらう。気持ちを聞いてほしい。'
        material = ie.for_need(value['exploration'], value['selected'][0])[0]
        self.assertEqual(material['root_desire']['meaning'], '所属の欲求')
        self.assertEqual(material['concrete_material'], branch['concrete_material'])
        self.assertNotIn('desired_experience', material)
        material['root_desire']['meaning'] = '変更'
        self.assertEqual(branch['root_desire']['meaning'], '所属の欲求')

    def test_record_keeps_full_exploration_with_method_identity(self):
        brief, _, sources, value = self.fixture()
        saved = rn.record(value, brief=brief, intent_sources=sources)
        self.assertEqual(saved['exploration'], value['exploration'])
        self.assertEqual(saved['analysis_method_sha256'], pb.fingerprint(ie.POLICY))
        saved['exploration']['branches'][0]['unknowns'].append('別の不明点')
        self.assertNotEqual(saved['exploration'], value['exploration'])

    def test_load_rejects_new_method_with_missing_link_targets(self):
        brief, _, sources, value = self.fixture()
        saved = rn.record(value, brief=brief, intent_sources=sources)
        saved['selected'][0]['hypothesis_ids'] = ['missing']
        with patch('pipeline.db.get_optional_artifact', return_value={'content_text': json.dumps(saved)}), \
             patch('pipeline.db.get_artifact', return_value={'content_text': sources['serp']}):
            with self.assertRaises(ContentQualityError): rn.load('j', brief)

    def test_normal_generation_without_exploration_stops_without_retry_or_completed_artifact(self):
        from pipeline import step_intent as step
        from test_reader_value_flow import response
        brief, material, sources, legacy = fixtures()
        records = {}
        def save(**data):
            records[data['step']] = data
            return data
        with patch.object(step, 'prepare', return_value=(brief, material, None, 'miss')), \
             patch.object(step, 'get_artifact', return_value={'content_text': sources['serp']}), \
             patch.object(step.anthropic, 'Anthropic'), patch.object(step, 'upsert_artifact', side_effect=save), \
             patch.object(step, 'create_with_retry', side_effect=[response({}), response(legacy)]) as calls:
            with self.assertRaises(ContentQualityError): step.run('j', brief['keyword'])
        self.assertEqual(calls.call_count, 2)
        self.assertNotIn('search_intent', records)
        self.assertNotIn('product_context', records)
        self.assertEqual(json.loads(records['reader_needs']['content_text'])['status'], 'analyzing')


if __name__ == '__main__': unittest.main()
