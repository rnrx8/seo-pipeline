"""Selection constraints must match the request schema before paid analysis."""
import copy
import json
import unittest
from unittest.mock import patch

from pipeline import reader_needs as rn, step_intent as intent
from pipeline.content_quality import ContentQualityError
from intent_exploration_fixtures import attach_exploration
from test_reader_value_flow import fixtures, response


class IntentSelectionAvailabilityTests(unittest.TestCase):
    def test_all_product_and_cta_combinations_match_the_validator(self):
        original = copy.deepcopy(rn.FIT)
        for has_product in (False, True):
            for has_cta in (False, True):
                with self.subTest(product=has_product, cta=has_cta):
                    schema = rn.output_schema(new_product=False, has_product=has_product,
                                              has_cta=has_cta)['format']['schema']
                    props = schema['properties']['product_fit']['properties']
                    for role in props['role']['enum']:
                        for cta_role in props['cta_role']['enum']:
                            # An unrelated product still cannot get an appropriate CTA;
                            # this semantic relation remains checked by the validator.
                            if cta_role == 'appropriate' and role not in ('useful', 'conditional'):
                                continue
                            _, material, sources, value = fixtures()
                            value['product_fit'].update(role=role, cta_role=cta_role)
                            rn.validate(value, sources, product_material_ids=[m['id'] for m in material['materials']],
                                        has_product=has_product, has_cta=has_cta)
        self.assertEqual(rn.FIT, original)

    def test_missing_selection_never_offers_rejected_roles_to_the_model(self):
        for has_cta in (False, True):
            props = rn.output_schema(new_product=False, has_product=False,
                                     has_cta=has_cta)['format']['schema']['properties']['product_fit']['properties']
            self.assertEqual(props['role']['enum'], ['none'])
            self.assertNotIn('appropriate', props['cta_role']['enum'])

    def test_selected_product_keeps_recommendation_instead_of_forced_neutrality(self):
        props = rn.output_schema(new_product=True, has_product=True,
                                 has_cta=True)['format']['schema']['properties']['product_fit']['properties']
        self.assertIn('useful', props['role']['enum'])
        self.assertIn('conditional', props['role']['enum'])
        self.assertIn('appropriate', props['cta_role']['enum'])

    def test_contradictory_product_flags_are_rejected_before_model_work(self):
        with self.assertRaises(ValueError):
            rn.output_schema(new_product=True, has_product=False, has_cta=False)

    def test_no_product_request_preserves_explicit_mention_and_saves_reader_analysis(self):
        brief, material, sources, value = fixtures()
        brief['product'] = None
        brief['request']['custom_prompt'] = '既婚者クラブは記事後半で自然に紹介する。'
        material['service'] = None
        material['materials'] = []
        value.pop('product_understanding')
        value['product_fit'].update(role='none', reason='専用商材は未選択。依頼の紹介方針は保持する。',
                                    need_ids=[], material_ids=[], cta_role='defer')
        value = attach_exploration(value, brief['keyword'])
        expected_needs = copy.deepcopy(value['selected'])
        records = {}

        def save(**data):
            records[data['step']] = data
            return data

        def generate(client, **request):
            props = request['output_config']['format']['schema']['properties']['product_fit']['properties']
            self.assertEqual(props['role']['enum'], ['none'])
            self.assertNotIn('appropriate', props['cta_role']['enum'])
            payload = json.loads(request['messages'][0]['content'])
            self.assertEqual(payload['production_brief']['request']['custom_prompt'],
                             brief['request']['custom_prompt'])
            self.assertIsNone(payload['production_brief']['product'])
            return response(value)

        with patch.object(intent, 'prepare', return_value=(brief, material, None, 'not_applicable')), \
             patch.object(intent, 'get_artifact', return_value={'content_text': sources['serp']}), \
             patch.object(intent, 'get_optional_artifact', return_value=None), \
             patch.object(intent, '_generate_query_attrs', return_value={}), \
             patch.object(intent.anthropic, 'Anthropic'), \
             patch.object(intent, 'upsert_artifact', side_effect=save), \
             patch.object(intent, 'create_with_retry', side_effect=generate) as model:
            intent.run('j', brief['keyword'])
        self.assertEqual(model.call_count, 1)
        self.assertIn('search_intent', records)
        saved = json.loads(records['reader_needs']['content_text'])
        self.assertEqual(saved['selected'], expected_needs)
        self.assertEqual(saved['product_fit']['role'], 'none')

    def test_validator_still_rejects_unselected_product_if_provider_violates_schema(self):
        _, _, sources, value = fixtures()
        with self.assertRaisesRegex(ContentQualityError, '未指定の商材'):
            rn.validate(value, sources, product_material_ids=['service:point:0'], has_product=False)
