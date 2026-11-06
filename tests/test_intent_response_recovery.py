import copy
import json
import unittest
from unittest.mock import patch
from pipeline import intent_response_recovery as recovery, step_intent as intent, reader_needs as rn
from pipeline.json_transport import decode
from pipeline.production_brief import fingerprint
from pipeline.content_quality import ContentQualityError
from intent_exploration_fixtures import attach_exploration
from test_reader_value_flow import fixtures


def saved_case():
    brief, material, sources, value = fixtures()
    brief['product'] = None
    brief['request']['custom_prompt'] = '既婚者クラブを記事後半で自然に紹介する。'
    sources['request'] = json.dumps(brief['request'], ensure_ascii=False)
    material['service'], material['materials'] = None, []
    value.pop('product_understanding')
    value['product_fit'].update(role='conditional', material_ids=[], cta_role='defer')
    value = attach_exploration(value, sources['keyword'])
    text = json.dumps(value['exploration'], ensure_ascii=False)
    # Same failure class as Miyazaki: an extra closing bracket before a field.
    value['exploration'] = text.replace('"concrete_material":', ']"concrete_material":', 1)
    request = {'messages': [{'role': 'user', 'content': json.dumps(
        {'input_sources': sources, 'production_brief': brief}, ensure_ascii=False)}]}
    attempt = {'model': intent.MODEL, 'stop_reason': 'end_turn',
               'source_sha256': fingerprint(sources), 'request': request,
               'request_sha256': fingerprint(request), 'raw_response': json.dumps(value, ensure_ascii=False),
               'input_tokens': 10, 'output_tokens': 20}
    return brief, material, sources, value, {'version': 1, 'attempts': [attempt]}


class JsonTransportTests(unittest.TestCase):
    def test_only_one_extra_closer_is_removed_without_editing_text(self):
        text = '{"message":"[閉じ括弧]と\\\"引用\\\"を維持",]"items":[]}'
        parsed, receipt = decode(text, lambda value: None)
        self.assertEqual(parsed, {'message':'[閉じ括弧]と"引用"を維持', 'items':[]})
        self.assertFalse(receipt['wording_changed'])
        self.assertEqual(receipt['removed'], ']')

    def test_valid_json_is_unchanged_and_has_no_repair_receipt(self):
        self.assertEqual(decode('{"a":["]"]}', lambda value: None), ({'a':[']']}, None))

    def test_missing_values_bad_quotes_multiple_errors_and_duplicate_keys_still_fail(self):
        for text in ('{"a":}', '{"a":"bad"quote"}', '{]"a":[]]}', '{"a":1,"a":2}'):
            with self.subTest(text=text), self.assertRaises((ValueError, ContentQualityError)):
                decode(text, lambda value: None)

    def test_canonical_shape_check_cannot_be_bypassed_by_syntax_repair(self):
        def reject(value):
            raise ContentQualityError('required field missing')
        with self.assertRaisesRegex(ContentQualityError, 'required field'):
            decode('{]"a":[]}', reject)


class SavedIntentRecoveryTests(unittest.TestCase):
    def test_complete_same_input_response_is_reused_without_rewriting_analysis(self):
        brief, material, sources, value, history = saved_case()
        original = copy.deepcopy(history)
        parsed, receipt, usage = recovery.recover(history, brief=brief, sources=sources,
                                                product_input=material, model=intent.MODEL)
        self.assertEqual(history, original)
        self.assertEqual(parsed['selected'], value['selected'])
        self.assertEqual(parsed['analysis'], value['analysis'])
        self.assertEqual(parsed['product_fit']['role'], 'none')
        self.assertEqual(receipt['original_product_fit']['role'], 'conditional')
        self.assertEqual(receipt['new_model_requests'], 0)
        self.assertEqual(len(receipt['transport_repairs']), 1)
        rn.validate(parsed, sources, has_product=False, has_cta=False, require_exploration=True)

    def test_changed_request_hash_and_brief_do_not_get_reused(self):
        for change in ('hash', 'brief', 'stop'):
            brief, material, sources, _, history = saved_case()
            if change == 'hash': history['attempts'][0]['request_sha256'] = 'tampered'
            if change == 'brief': brief['request']['custom_prompt'] = 'new instruction'
            if change == 'stop': history['attempts'][0]['stop_reason'] = 'max_tokens'
            with self.subTest(change=change), self.assertRaises(ContentQualityError):
                recovery.recover(history, brief=brief, sources=sources, product_input=material, model=intent.MODEL)

    def test_source_change_does_not_adopt_old_reader_hypotheses(self):
        brief, material, sources, _, history = saved_case()
        sources['serp'] = 'changed SERP'
        self.assertIsNone(recovery.recover(history, brief=brief, sources=sources,
                                          product_input=material, model=intent.MODEL))

    def test_jsonb_key_order_change_reuses_identical_values_and_retains_both_hashes(self):
        brief, material, sources, _, history = saved_case()
        sources['request'] = json.dumps(dict(reversed(list(brief['request'].items()))), ensure_ascii=False)
        _, receipt, _ = recovery.recover(history, brief=brief, sources=sources,
                                         product_input=material, model=intent.MODEL)
        self.assertTrue(receipt['request_serialization_only_changed'])
        self.assertNotEqual(receipt['source_sha256'], receipt['saved_source_sha256'])

    def test_actual_stage_reuses_saved_result_before_any_model_call(self):
        brief, material, sources, value, history = saved_case()
        records = {}
        def save(**data):
            records[data['step']] = data
            return data
        with patch.object(intent, 'prepare', return_value=(brief, material, None, 'not_applicable')), \
             patch.object(intent, 'get_artifact', return_value={'content_text':sources['serp']}), \
             patch.object(intent, 'get_optional_artifact', return_value={'content_text':json.dumps(history)}), \
             patch.object(intent, 'upsert_artifact', side_effect=save), \
             patch.object(intent.anthropic, 'Anthropic'), \
             patch.object(intent, '_generate_query_attrs') as attrs, \
             patch.object(intent, 'create_with_retry') as model:
            intent.run('j', brief['keyword'])
        model.assert_not_called()
        attrs.assert_not_called()
        self.assertEqual(records['search_intent']['content_text'], value['analysis'])
        self.assertIn('search_intent_response_reuse', records)
        self.assertIn('search_intent_transport', records)
        self.assertEqual(json.loads(records['reader_needs']['content_text'])['selected'], value['selected'])
