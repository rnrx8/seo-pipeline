"""Unused unknown research never replaces current five-role article acceptance."""
import copy
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import step_final_validate as final, content_quality as cq
import test_stage5_authoring as stage5


@patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
class PublicationEvidenceBoundaryTests(unittest.TestCase):
    def run_case(self,defect=None):
        report,_,context=stage5.Stage5AuthoringTests().audit_fixture('tiered','legacy')
        if defect=='missing_role':report['phases'].pop('evidence')
        elif defect=='failed_coverage':
            report['valid']=False
            next(c for c in report['checks'] if c['key']=='coverage')['status']='fail'
        elif defect=='stale_snapshot':report['snapshot']='different article'
        records={'article':{'content_text':context['text'],'meta':{}},
            'outline':{'content_text':context['outline']},'content_contract':{'content_text':json.dumps(context['contract'])},
            'fact_sheet':{'content_text':context['facts']},'fresh_sources':{'content_text':'[]'},
            'content_audit':{'content_text':json.dumps(report)}}
        with ExitStack() as st:
            for name in ('readability_issues','validate_structure','cta_placement_issues','validate_delivery','scope_issues'):
                st.enter_context(patch.object(final,name,return_value=[]))
            st.enter_context(patch.object(final,'get_job',return_value={}))
            st.enter_context(patch.object(final,'get_artifact',side_effect=lambda j,k:copy.deepcopy(records[k])))
            st.enter_context(patch.object(final,'get_optional_artifact',return_value=None))
            st.enter_context(patch.object(final,'upsert_artifact',side_effect=lambda **kw:kw))
            st.enter_context(patch.object(final,'audit_facts',return_value=context['facts']))
            st.enter_context(patch.object(final,'source_evidence',return_value=context['sources']))
            st.enter_context(patch.object(final,'final_review_requirements',return_value=context['requirements']))
            st.enter_context(patch('pipeline.research_requirements.require_authoring_matrix',
                side_effect=cq.ContentQualityError('stale sources') if defect=='stale_sources' else None,
                return_value={'valid':False,'items':[]}))
            old=st.enter_context(patch('pipeline.research_requirements._require_matrix_strict',side_effect=AssertionError('No completeness substitute')))
            seal=st.enter_context(patch('pipeline.article_revisions.seal'))
            if defect:
                with self.assertRaises(cq.ContentQualityError):final.run('job','kw')
                seal.assert_not_called()
            else:
                saved=final.run('job','kw');value=json.loads(saved['content_text'])
                self.assertTrue(value['valid']);self.assertFalse(value['research_status']['all_questions_complete'])
                seal.assert_called_once_with('job')
            old.assert_not_called()

    def test_complete_current_article_audit_is_required_even_for_unused_research_gaps(self):self.run_case()
    def test_currentness_and_all_roles_are_never_relaxed(self):
        for defect in ('missing_role','failed_coverage','stale_snapshot','stale_sources'):
            with self.subTest(defect=defect):self.run_case(defect)
