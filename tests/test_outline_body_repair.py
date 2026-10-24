"""Prevent unrelated prose/conditions from being regenerated during recovery."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import outline_body_repair as repair, editorial_recovery, step_article
from pipeline.content_quality import ContentQualityError
from pipeline.section_identity import bind_sections, valid_binding
from test_editorial_recovery import materials
from test_editorial_blueprint import artifact


def fixture():
    article, before, report = materials()
    value = copy.deepcopy(before['meta']['editorial_blueprint'])
    for sid, title in [('s04', '料金と対象'), ('s05', '最後の判断')]:
        section = copy.deepcopy(value['sections'][0]); section.update(id=sid, title=title)
        value['sections'].append(section)
    before = artifact(value, before['meta']['blueprint_context'])
    article['content_text'] += ('\n\n## 料金と対象\n\n女性は無料、男性は先に有料の手続きが必要です。\n\n'
                               '## 最後の判断\n\n未確認を、存在しないと断定しません。')
    article['meta']['section_map'] = bind_sections(article['content_text'], before['content_text'])
    revised = copy.deepcopy(value)
    revised['sections'][1].update(level=2, parent_id='',
        budget=copy.deepcopy(value['sections'][0]['budget']),
        reader_progression=copy.deepcopy(value['sections'][0]['reader_progression']))
    after = artifact(revised, before['meta']['blueprint_context'])
    return article, before, after, report


class SectionReconciliationTests(unittest.TestCase):
    def test_move_preserves_lead_and_remote_conditions_including_negation(self):
        source, before, after, report = fixture()
        p = repair.plan(source, before, after, report)
        self.assertEqual(p['target_section_ids'], ['s01', 's02', 's03'])
        generated = {sid: p['source_sections'][sid]['body'] for sid in p['target_section_ids']}
        generated['s02'] = '準備は求める関係を言葉にすることです。'
        candidate, binding = repair.assemble(p, generated, after['content_text'])
        self.assertTrue(valid_binding(candidate, after['content_text'], binding))
        self.assertTrue(candidate.startswith(p['lead']))
        self.assertIn('## 始める前の準備\n\n準備は求める関係を言葉にすることです。', candidate)
        _, rows = repair.sections({'content_text': candidate, 'meta': {'section_map': binding}}, after)
        for sid in ('s04', 's05'):
            self.assertEqual(rows[sid]['text'].rstrip('\n'), p['source_sections'][sid]['text'].rstrip('\n'))

    def test_new_h4s_also_make_the_existing_parent_writable_to_remove_duplicate_prose(self):
        source, before, _, report = fixture()
        value = copy.deepcopy(before['meta']['editorial_blueprint'])
        for sid in ('s06', 's07'):
            child = copy.deepcopy(value['sections'][1])
            child.update(id=sid, parent_id='s02', level=4, title=sid+'の独立した回答')
            value['sections'].insert(2 if sid == 's06' else 3, child)
        after = artifact(value, before['meta']['blueprint_context'])
        p = repair.plan(source, before, after, {'editorial_findings': []})
        self.assertIn('s02', p['target_section_ids'])
        generated = {sid: ('親の短い結論です。' if sid=='s02' else '独立した回答です。') for sid in p['target_section_ids']}
        candidate, binding = repair.assemble(p, generated, after['content_text'])
        self.assertNotIn('希望を整理します。', candidate)
        self.assertEqual([e['level'] for e in binding['entries']].count(4), 2)

    def test_budget_only_changes_do_not_authorize_a_full_rewrite(self):
        source, before, _, _ = fixture()
        value = copy.deepcopy(before['meta']['editorial_blueprint'])
        value['sections'][0]['budget']['reason'] = '配分だけを修正'
        after = artifact(value, before['meta']['blueprint_context'])
        with self.assertRaisesRegex(ContentQualityError, '修正対象'):
            repair.plan(source, before, after, {'editorial_findings': []})

    def test_neighbor_does_not_restore_an_old_heading_already_corrected_by_qa(self):
        source, before, after, report = fixture()
        source['content_text'] = source['content_text'].replace('## 別の判断','## 校正して意味を明確にした見出し')
        entry = next(e for e in source['meta']['section_map']['entries'] if e['blueprint_id']=='s03')
        entry['title']='校正して意味を明確にした見出し'
        p=repair.plan(source,before,after,report)
        generated={sid:p['source_sections'][sid]['body'] for sid in p['target_section_ids']}
        candidate,binding=repair.assemble(p,generated,after['content_text'])
        self.assertIn('## 校正して意味を明確にした見出し',candidate)
        self.assertNotIn('## 別の判断',candidate)
        self.assertTrue(valid_binding(candidate,after['content_text'],binding))

    def test_readonly_or_missing_sections_and_heading_injection_are_rejected(self):
        source, before, after, report = fixture()
        p = repair.plan(source, before, after, report)
        good = {sid: '必要な説明です。' for sid in p['target_section_ids']}
        for kind in ('extra', 'missing', 'heading'):
            bad = copy.deepcopy(good)
            if kind == 'extra': bad['s05'] = '無断で重要条件を書き換え'
            elif kind == 'missing': bad.pop('s02')
            else: bad['s02'] = '回答です。\n\n### 許可されていない子見出し'
            with self.subTest(kind=kind), self.assertRaises(ContentQualityError):
                repair.assemble(p, bad, after['content_text'])

    def test_stale_or_unresolved_identity_stops_instead_of_falling_back_to_full_generation(self):
        source, before, after, report = fixture()
        source['meta']['section_map']['entries'][0]['blueprint_id'] = 'unknown'
        with self.assertRaises(ContentQualityError): repair.plan(source, before, after, report)

    def test_paragraph_ending_in_the_next_heading_does_not_make_that_next_chapter_the_target(self):
        source, before, after, report = fixture()
        p = repair.plan(source, before, after, report)
        self.assertIn('s02', p['changed_section_ids'])
        self.assertNotIn('s03', p['changed_section_ids'])
        self.assertIn('s03', p['neighbor_section_ids'])

    def test_legacy_job_still_uses_existing_writer_and_blueprint_jobs_use_new_route(self):
        records={'outline_before_editorial_recovery': {'content_text':'旧構成'}, 'outline': {'content_text':'旧構成'}}
        with patch.object(repair, 'get_artifact', side_effect=lambda j,k: records[k]), \
             patch.object(step_article, 'run', return_value='legacy') as full:
            self.assertEqual(repair.run('j','kw'), 'legacy'); full.assert_called_once()
        with patch('pipeline.ai.astra_review_enabled', return_value=True):
            self.assertIs(dict(editorial_recovery.recovery_steps({}))['article'], repair.run)

    def test_invalid_generated_patch_never_overwrites_source_article(self):
        source, before, after, report = fixture()
        records={'article':source,'article_before_editorial_recovery':source,
                 'outline_before_editorial_recovery':before,'outline':after,
                 'content_audit_before_editorial_recovery':{'content_text':json.dumps(report)},
                 'content_contract':{'content_text':'{}'}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(repair,'get_artifact', side_effect=lambda j,k: copy.deepcopy(records[k])))
            stack.enter_context(patch.object(repair,'get_job', return_value={}))
            stack.enter_context(patch('pipeline.generation_context.generation_evidence',return_value='確認済み条件'))
            stack.enter_context(patch('pipeline.ai.get_step_config',return_value=('gpt-6.1-sol',1000)))
            check=stack.enter_context(patch('pipeline.tiered_research.checked_request',return_value=({'sections':{'s05':'無断の修正'}},None)))
            save=stack.enter_context(patch.object(repair,'upsert_artifact'))
            with self.assertRaises(ContentQualityError): repair.run('j','kw')
            self.assertEqual(check.call_count,2); save.assert_not_called()

    def test_native_run_preserves_remote_conditions_and_defers_exact_cta_to_configured_step(self):
        from pipeline.step_cta_inject import _format_cta_block
        source, before, after, report = fixture()
        cta={'body':'案内です。','button_text':'登録','url':'https://example.test/register'}
        source['content_text'] += '\n\n'+_format_cta_block(cta)
        source['meta'].update(content_audited=True,content_audit_snapshot='old',
                              cta_placement={'count':1})
        records={'article':source,'article_before_editorial_recovery':source,
                 'outline_before_editorial_recovery':before,'outline':after,
                 'content_audit_before_editorial_recovery':{'content_text':json.dumps(report)},
                 'content_contract':{'content_text':'{}'}}
        without_cta=copy.deepcopy(source)
        without_cta['content_text']=without_cta['content_text'].replace(_format_cta_block(cta),'')
        packet=repair.plan(without_cta,before,after,report)
        result={'sections':{sid:packet['source_sections'][sid]['body'] for sid in packet['target_section_ids']}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(repair,'get_artifact',side_effect=lambda j,k:copy.deepcopy(records[k])))
            stack.enter_context(patch.object(repair,'get_job',return_value={'cta_id':'cta'}))
            stack.enter_context(patch.object(repair,'get_cta_by_id',return_value=cta))
            stack.enter_context(patch('pipeline.generation_context.generation_evidence',return_value='確認済み条件'))
            stack.enter_context(patch('pipeline.ai.get_step_config',return_value=('gpt-6.1-sol',1000)))
            stack.enter_context(patch('pipeline.tiered_research.checked_request',return_value=(result,None)))
            save=stack.enter_context(patch.object(repair,'upsert_artifact',side_effect=lambda **kw:kw))
            saved=repair.run('j','kw')
            self.assertIn('女性は無料、男性は先に有料',saved['content_text'])
            self.assertIn('未確認を、存在しないと断定しません。',saved['content_text'])
            self.assertNotIn(cta['url'],saved['content_text'])
            self.assertFalse(saved['meta']['content_audited'])
            self.assertIsNone(saved['meta']['content_audit_snapshot'])
            self.assertNotIn('cta_placement',saved['meta'])
            self.assertEqual(set(saved['meta']['outline_body_repair']['preserved_section_sha256']),{'s04','s05'})
            self.assertTrue(saved['meta']['outline_body_repair']['lead_preserved'])
            save.assert_called_once()


if __name__ == '__main__': unittest.main()
