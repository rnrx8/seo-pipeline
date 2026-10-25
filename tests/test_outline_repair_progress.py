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
        with self.assertRaisesRegex(ContentQualityError, '通算3回'):
            progress.prepare(copy.deepcopy(a), report('new'), lambda: None)

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
            st.enter_context(patch('pipeline.research_requirements.require_matrix', return_value={}))
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
