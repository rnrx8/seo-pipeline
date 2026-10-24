import copy
import json
import unittest
from itertools import product
from types import SimpleNamespace

from pipeline import blueprint_transport as transport, editorial_blueprint as bp
from pipeline.content_quality import ContentQualityError
from test_editorial_blueprint import fixture


class BlueprintTransportTests(unittest.TestCase):
    def test_section_and_lead_repair_avoid_grammar_compile_and_keep_strict_bounds(self):
        _, _, _, _, blueprint = fixture()
        for allow_lead,allow_omissions in product((False, True),repeat=2):
            with self.subTest(allow_lead=allow_lead,allow_omissions=allow_omissions):
                request = dict(model='claude-opus-5-5', system='Repair policy',
                               messages=[{'role':'user','content':'Reviewed findings and materials'}],
                               max_tokens=6000, output_config=bp.repair_config(allow_lead=allow_lead,allow_omissions=allow_omissions))
                before = copy.deepcopy(request)
                wire, schema = transport.prepare(request)
                self.assertEqual(request, before)
                self.assertNotIn('format', wire['output_config'])
                self.assertEqual(wire['messages'], request['messages'])
                self.assertEqual(schema, bp.repair_schema(allow_lead=allow_lead,allow_omissions=allow_omissions))
                value = dict(edits=[{'id':'s01','section':copy.deepcopy(blueprint['sections'][0])}],
                             delete_ids=[], section_order=[])
                if allow_lead:
                    value.update(lead_direction=blueprint['lead_direction'], reader_focus=blueprint['reader_focus'])
                if allow_omissions:value['omission_edits']=[{'research_id':'q1','reason':'確認済みだが今回の問いでは採用しない'}]
                def message(v):
                    return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(v))])
                transport.validate(message(value), schema)
                for mutate in (lambda v:v['edits'][0]['section']['budget'].update(chars=-1),
                               lambda v:v['edits'][0]['section'].update(level=7),
                               lambda v:v.update(unreviewed_extra='invalid')):
                    broken = copy.deepcopy(value)
                    mutate(broken)
                    with self.assertRaises(ContentQualityError):
                        transport.validate(message(broken), schema)

    def test_blueprint_wire_preserves_schema_and_validates_original_constraints(self):
        request = dict(model='claude-opus-5-5', system='Original policy', messages=[{'role':'user','content':'Materials'}],
                       max_tokens=6000, output_config=bp.OUTPUT_CONFIG)
        before = copy.deepcopy(request)
        wire, schema = transport.prepare(request)
        self.assertEqual(request, before)
        self.assertNotIn('format', wire['output_config'])
        self.assertEqual(wire['messages'], request['messages'])
        self.assertTrue(wire['system'].startswith('Original policy'))
        self.assertIn(json.dumps(bp.SCHEMA, ensure_ascii=False), wire['system'])
        _, _, _, _, value = fixture()
        def message(v):
            return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(v))])
        transport.validate(message(value), schema)
        for mutate in (lambda v:v.pop('reader_focus'), lambda v:v.update(titles=[]),
                       lambda v:v['sections'][0].update(level=7)):
            broken = copy.deepcopy(value)
            mutate(broken)
            with self.assertRaises(ContentQualityError):
                transport.validate(message(broken), schema)

    def test_small_schemas_and_non_json_responses_are_not_accepted_as_blueprints(self):
        request = dict(model='claude-opus-5-5', system='Policy', output_config={
            'format': {'type':'json_schema', 'schema': {'type':'object'}}})
        self.assertEqual(transport.prepare(request), (request, None))
        message = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='```json\n{}\n```')])
        with self.assertRaises(ContentQualityError):
            transport.validate(message, bp.SCHEMA)
