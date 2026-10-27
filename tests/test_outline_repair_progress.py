import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import outline_repair_progress as progress, step_research_guard as guard
from pipeline.content_quality import ContentQualityError
from pipeline.outline_policy import current_policy


def report(reason, valid=False):
    return {'valid': valid, 'checks': [{'key': 'coverage', 'status': 'pass' if valid else 'fail',
                                      'reason': reason}], 'snapshot': reason}


class OutlineRepairProgressTests(unittest.TestCase):
    def test_changed_findings_continue_and_restart_keeps_total(self):
        a = {'content_text': 'original', 'meta': {}}
        for n in range(1, 4):
            p = progress.prepare(a, report(str(n)), lambda: self.fail('legacy load'))
            self.assertEqual(p['next']['round'], n)
            a = {'content_text': f'revision{n}', 'meta': progress.completed(p, f'revision{n}')}
        with patch('pipeline.quality_budget.spending_limits_enabled',return_value=True):
            with self.assertRaisesRegex(ContentQualityError, '通算3回'):
                progress.prepare(copy.deepcopy(a), report('new'), lambda: None)
        with self.assertRaisesRegex(ContentQualityError,'同じ回答要件'):
            progress.prepare(copy.deepcopy(a), report('rephrased'), lambda: None)

    def test_production_continues_new_obligation_but_limits_rephrased_old_one(self):
        a={'content_text':'original','meta':{}}
        def finding(n,decision):return {**report(str(n)), 'blueprint_issues':[{'decision_id':decision,'requirement_index':'0'}]}
        with patch('pipeline.quality_budget.spending_limits_enabled',return_value=False):
            for n in range(1,4):
                p=progress.prepare(a,finding(n,'i01'),lambda:None)
                a={'content_text':str(n),'meta':progress.completed(p,str(n))}
            new=progress.prepare(a,finding(4,'i03'),lambda:None)
            self.assertEqual(new['next']['round'],4)
            with self.assertRaisesRegex(ContentQualityError,'同じ回答要件'):
                progress.prepare(a,finding(5,'i01'),lambda:None)

    def test_legacy_findings_migrate_only_from_matching_archived_revision(self):
        a={'content_text':'before','meta':{}}
        old=report('old');p=progress.prepare(a,old,lambda:None)
        a={'content_text':'after','meta':progress.completed(p,'after')}
        del a['meta']['outline_repair_progress']['history'][0]['obligation_ids']
        archive={'content_text':'before'}
        next_report={**report('new'),'blueprint_issues':[{'decision_id':'i03','requirement_index':'0'}]}
        got=progress.prepare(a,next_report,lambda:None,lambda n:(old,archive))
        self.assertIn('check:coverage',got['history'][0]['obligation_ids'])
        with self.assertRaisesRegex(ContentQualityError,'過去指摘'):
            progress.prepare(a,next_report,lambda:None,lambda n:(old,{'content_text':'different'}))

    def test_unlocalized_evidence_failure_stays_counted_beside_new_decision(self):
        r={**report('evidence'),'checks':[{'key':'evidence_support','status':'fail','reason':'根拠不足'}],
           'blueprint_issues':[{'decision_id':'i03','requirement_index':'0'}]}
        self.assertEqual(progress.obligations(r,{'content_text':'outline'}),['check:evidence_support','decision:i03:0'])

    def test_repeated_findings_no_change_and_external_edit_cannot_reset(self):
        a = {'content_text': 'before', 'meta': {}}
        p = progress.prepare(a, report('same'), lambda: None)
        with self.assertRaisesRegex(ContentQualityError, '変わって'):
            progress.completed(p, 'before')
        a = {'content_text': 'after', 'meta': progress.completed(p, 'after')}
        with self.assertRaisesRegex(ContentQualityError, '繰り返'):
            progress.prepare(a, report('same'), lambda: None)
        a['content_text'] = 'outside edit'
        with self.assertRaisesRegex(ContentQualityError, '現在の版'):
            progress.history(a, lambda: None)

    def test_legacy_repair_consumes_round_without_inventing_old_qa(self):
        a = {'content_text': 'after', 'meta': {'local_repaired': True}}
        p = progress.prepare(a, report('new'), lambda: {'content_text': 'before'})
        self.assertEqual(p['next']['round'], 2)
        self.assertIsNone(p['history'][0]['findings_sha256'])
        self.assertTrue(p['history'][0]['legacy_repair_counted'])
        for r in (report('pass', True), {**report('pending'), 'evidence_pending': True}):
            with self.assertRaises(ContentQualityError):progress.prepare(a, r, lambda: {'content_text':'before'})

    def test_native_guard_reviews_each_changed_revision_until_pass(self):
        artifacts = {'outline': {'content_text': 'original', 'meta': {'editorial_policy': current_policy()}},
                     'fact_sheet': {'content_text': 'facts'}, 'content_contract': {'content_text': '{}'},
                     'fresh_sources': {'content_text': '[]'}}
        audited = []
        def get(j, s):return copy.deepcopy(artifacts[s])
        def save(**v):artifacts[v['step']] = copy.deepcopy(v);return v
        def audit(*a, **kw):
            audited.append(kw['text'])
            return report(f'finding{len(audited)}', len(audited) == 3)
        def repair(j, keyword, r, client):
            a = get(j, 'outline')
            p = progress.prepare(a, r, lambda: get(j, 'outline_before_local_repair'))
            candidate = f'changed{len(audited)}'
            save(job_id=j, step='outline', content_text=candidate,
                 meta={**a['meta'], **progress.completed(p, candidate)})
        with ExitStack() as st:
            for name, side in [('get_artifact',get),('upsert_artifact',save),('audit',audit),('repair_outline',repair)]:
                st.enter_context(patch.object(guard, name, side_effect=side))
            st.enter_context(patch.object(guard, 'get_job', return_value={}))
            st.enter_context(patch.object(guard, 'intent_review_requirements', return_value={}))
            st.enter_context(patch.object(guard, 'assignment_issues', return_value=[]))
            st.enter_context(patch.object(guard, 'source_evidence', return_value='sources'))
            st.enter_context(patch('pipeline.research_requirements.require_authoring_matrix', return_value={}))
            st.enter_context(patch('pipeline.research_requirements.load_plan', return_value={}))
            st.enter_context(patch('pipeline.step_outline.ensure_complete_volume_design', side_effect=lambda t,*a,**k:(t,False)))
            st.enter_context(patch('pipeline.step_structure_guard.validate_structure', return_value=[]))
            structure = st.enter_context(patch('pipeline.step_structure_guard.run_before_research'))
            st.enter_context(patch('pipeline.editorial_blueprint.review_context', return_value=None))
            st.enter_context(patch.object(guard.anthropic, 'Anthropic'))
            result = guard.run('j','kw')
        self.assertTrue(json.loads(result['content_text'])['valid'])
        self.assertEqual(audited, ['original','changed1','changed2'])
        self.assertEqual(structure.call_count, 2)
        self.assertEqual(len(progress.history(artifacts['outline'],lambda:None)),2)
        self.assertEqual(json.loads(artifacts['research_validation_3']['content_text'])['snapshot'],'finding3')
