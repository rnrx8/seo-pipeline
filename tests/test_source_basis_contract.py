import copy
import json
import unittest
from unittest.mock import patch

from pipeline import research_requirements as req
from pipeline.content_quality import ContentQualityError
from pipeline.evidence_policy import SOURCE_BASES
from pipeline.source_spans import span_schema
from pipeline.tiered_research import validate_visible_matrix
from test_evidence_policy import fixture


def expert_case(requirement='standard'):
    plan, _, value = fixture()
    plan['items'][0].update(question='代表的なトラブルの手口', source_requirement=requirement)
    text = '代表的な手口は投資への勧誘です。執筆：弁護士 山田一郎。'
    pages = [{'url':'https://expert.example/advice','text':text,'status':'success'}]
    value['items'][0].update(answer='代表的な手口には投資への勧誘がある。', basis='expert',
        reason='専門解説が直接説明している。', additional_sources_needed=False,
        evidence=[{'url':pages[0]['url'],'quote':'代表的な手口は投資への勧誘です。',
            'source_kind':'secondary','independence_group':'expert.example',
            'expert_name':'山田一郎','expert_scope_reason':'法律の解説',
            'expert_qualification_quote':'執筆：弁護士 山田一郎。'}])
    return plan,pages,value


class SourceBasisContractTests(unittest.TestCase):
    def test_question_specific_schemas_prevent_cross_route_answers(self):
        questions=[{'id':name,'source_requirement':name} for name in SOURCE_BASES]
        before=copy.deepcopy(req.MATRIX_SCHEMA)
        schema=span_schema(req.MATRIX_SCHEMA,[q['id'] for q in questions],questions=questions)
        rows=schema['format']['schema']['properties']['items']['properties']
        for name in SOURCE_BASES:
            self.assertEqual(rows[name]['properties']['basis']['enum'],list(SOURCE_BASES[name]))
        self.assertNotIn('expert',rows['standard']['properties']['basis']['enum'])
        self.assertNotIn('corroborated',rows['primary_only']['properties']['basis']['enum'])
        self.assertNotIn('guidance',rows['expert_allowed']['properties']['basis']['enum'])
        self.assertEqual(before,req.MATRIX_SCHEMA)
        with self.assertRaises(ValueError):
            span_schema(req.MATRIX_SCHEMA,['q1'],questions=questions)

    def test_expert_response_for_standard_is_diagnosed_not_auto_accepted(self):
        plan,pages,value=expert_case()
        validate_visible_matrix(value,plan,pages)
        item=value['items'][0]
        self.assertFalse(item['verified'])
        self.assertEqual(item['status'],'unresearched')
        self.assertEqual(item['validation_issue']['code'],'source_basis_mismatch')
        gaps=req.validate_matrix(value,plan,pages)
        self.assertIn('根拠区分の不整合',gaps[0]['reason'])
        self.assertEqual(gaps[0]['validation_issue']['basis'],'expert')
        self.assertEqual(item['reason'],'専門解説が直接説明している。')

    def test_conflict_never_spends_supplement_slot_or_recollects(self):
        for legacy in (False,True):
            plan,pages,value=expert_case();gaps=req.validate_matrix(value,plan,pages)
            if legacy:
                value['items'][0].pop('validation_issue');gaps[0].pop('validation_issue')
            with patch.object(req,'get_optional_artifact') as get, patch.object(req,'upsert_artifact') as save, \
                 patch('pipeline.step_fact_sheet.run') as collect:
                with self.assertRaisesRegex(ContentQualityError,'判定が不整合'):
                    req.supplement_once('job','keyword',plan,gaps,matrix=value)
            get.assert_not_called();save.assert_not_called();collect.assert_not_called()
            self.assertFalse(value['items'][0]['verified'])

    def test_permitted_expert_still_requires_qualification_and_exact_quote(self):
        plan,pages,value=expert_case('expert_allowed')
        self.assertEqual(req.validate_matrix(value,plan,pages),[])
        for key in ('expert_qualification_quote','quote'):
            bad=copy.deepcopy(value);bad['items'][0]['evidence'][0][key]='not in source'
            self.assertTrue(req.validate_matrix(bad,plan,pages))
            self.assertNotIn('validation_issue',bad['items'][0])

    def test_corrected_basis_drops_stale_error_without_changing_plan(self):
        plan,pages,value=expert_case();req.validate_matrix(value,plan,pages)
        item=value['items'][0];item.update(basis='corroborated',official_checked_urls=[pages[0]['url']])
        other={**pages[0],'url':'https://independent.example/advice'};pages.append(other)
        item['evidence'].append({**item['evidence'][0],'url':other['url'],'independence_group':'independent'})
        self.assertEqual(req.validate_matrix(value,plan,pages),[])
        self.assertNotIn('validation_issue',item)
        self.assertEqual(plan['items'][0]['source_requirement'],'standard')

    def test_real_missing_sources_still_use_existing_supplement(self):
        plan,pages,value=fixture();item=value['items'][0]
        item.update(status='unresearched',basis='unresolved',answer='',evidence=[],additional_sources_needed=True)
        gaps=req.validate_matrix(value,plan,pages)
        with patch.object(req,'get_optional_artifact',return_value=None), \
             patch.object(req,'get_artifact',return_value={'content_text':json.dumps(pages)}), \
             patch.object(req,'upsert_artifact'), patch('pipeline.step_fact_sheet.run') as collect:
            req.supplement_once('job','keyword',plan,gaps,matrix=value)
        self.assertEqual(collect.call_count,1)
        self.assertEqual(collect.call_args.kwargs['research_question_ids'],['q1'])

class OfficialExplorationScopeTests(unittest.TestCase):
    def case(self):
        plan,pages,value=fixture()
        plan['items'][0].update(question='代表的な手口の説明',official_check_required=False)
        value['items'][0].update(official_checked_urls=[])
        return plan,pages,value

    def test_general_corroboration_does_not_need_unrelated_official_source(self):
        plan,pages,value=self.case()
        self.assertEqual(req.validate_matrix(value,plan,pages),[])
        self.assertTrue(value['items'][0]['verified'])

    def test_commercial_and_legacy_questions_keep_official_first_condition(self):
        for explicit in (True,False):
            plan,pages,value=self.case()
            if explicit:plan['items'][0].update(question='料金',official_check_required=True)
            else:plan['items'][0].pop('official_check_required')
            self.assertTrue(req.validate_matrix(value,plan,pages))
            self.assertTrue(value['items'][0]['validation_issue']['official_check_required'])
            value['items'][0]['official_checked_urls']=[pages[0]['url']]
            self.assertEqual(req.validate_matrix(value,plan,pages),[])

    def test_general_scope_keeps_independence_quotes_exploration_and_legal_rules(self):
        for change in (
            lambda p,v:v['items'][0]['evidence'].pop(),
            lambda p,v:v['items'][0]['evidence'][1].update(independence_group=v['items'][0]['evidence'][0]['independence_group']),
            lambda p,v:v['items'][0]['evidence'][0].update(quote='not in source'),
            lambda p,v:v['items'][0].update(exploration_complete=False),
            lambda p,v:v['items'][0].update(official_checked_urls=['https://not-fetched.example']),
            lambda p,v:p['items'][0].update(source_requirement='expert_allowed'),
            lambda p,v:p['items'][0].update(source_requirement='primary_only')):
            plan,pages,value=self.case();change(plan,value)
            self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_new_plan_schema_requires_classification_and_rejects_non_boolean(self):
        row=req.PLAN_SCHEMA['format']['schema']['properties']['items']['items']
        self.assertIn('official_check_required',row['required'])
        plan,_,_=self.case();plan['items'][0]['official_check_required']='false'
        with self.assertRaises(ContentQualityError):req.validate_plan(plan)
