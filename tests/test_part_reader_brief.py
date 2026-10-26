"""Section-local writing handoff, without manufacturing child emotions."""
import copy
import json
import unittest

from pipeline.article_design import part_reader_brief
from test_reader_progression import chain


class PartReaderBriefTests(unittest.TestCase):
    def test_selects_current_h2_and_own_child_state_without_global_leakage(self):
        *_,value=chain();first,second,third=value['sections']
        first['reader_progression']['before']['emotion']='最初の章固有の不安'
        second['reader_progression']['before']['emotion']='別の章固有の焦り'
        child=copy.deepcopy(first)
        child.update(id='s04',level=3,parent_id=first['id'],title='具体例')
        child['reader_progression']['before']['emotion']='具体例での戸惑い'
        empty=copy.deepcopy(child);empty.update(id='s05',title='補足',reader_progression=None)
        value['sections']=[first,child,empty,second,third]
        original=copy.deepcopy(value)
        brief=part_reader_brief(value,[first['title']])
        rows=json.loads(brief.split('\n')[-1])
        self.assertEqual([r['section_id'] for r in rows],['s01','s04'])
        self.assertEqual([r['reader_feeling'] for r in rows],['最初の章固有の不安','具体例での戸惑い'])
        self.assertNotIn('別の章固有の焦り',brief)
        self.assertEqual(value,original)

    def test_no_emotion_is_inferred_from_after_goal_or_parent(self):
        *_,value=chain();section=value['sections'][1]
        self.assertTrue(section['reader_progression']['after']['emotion'])
        rows=json.loads(part_reader_brief(value,[section['title']]).split('\n')[-1])
        self.assertEqual(rows[0]['reader_feeling'],'')
        self.assertTrue(rows[0]['outcome']['emotion'])

    def test_legacy_missing_or_unselected_design_has_no_extra_instruction(self):
        *_,value=chain()
        for blueprint,titles in ((None,['章']),(value,[]),(value,['対象外'])):
            self.assertEqual(part_reader_brief(blueprint,titles),'')

    def test_typed_claim_and_scope_survive_beside_a_conflicting_editorial_summary(self):
        *_,ctx,value=chain();first=value['sections'][0]
        first['conclusion']='編集案が根拠より広い結論を提案している'
        ref=first['answer_elements'][0]['claim_ids'][0]
        ctx['accepted_claims'][ref].update(answer_type='inference',supports_current_conclusion=False,
            claim_limits=['解説上の関連づけであり、同じ理由を集計した結果ではない'])
        before=copy.deepcopy((ctx,value))
        rows=json.loads(part_reader_brief(value,[first['title']],ctx).split('\n')[-1])
        claim=next(c for c in rows[0]['accepted_claims'] if c['id']==ref)
        self.assertEqual(claim,ctx['accepted_claims'][ref])
        self.assertEqual(claim['answer_type'],'inference')
        self.assertFalse(claim['supports_current_conclusion'])
        self.assertEqual((ctx,value),before)

    def test_null_child_claims_are_kept_without_inventing_child_feelings(self):
        *_,ctx,value=chain();first=value['sections'][0]
        child=copy.deepcopy(first);child.update(id='s10',level=3,parent_id=first['id'],title='子の回答',reader_progression=None)
        ref=next(iter(ctx['accepted_claims']));unit=copy.deepcopy(ctx['accepted_claims'][ref]);unit.update(id='q-child.c01',text='子節固有の適用条件')
        ctx['accepted_claims'][unit['id']]=unit
        child['answer_elements']=[{'instruction':'子の回答','claim_ids':[unit['id']]}]
        value['sections'].insert(1,child)
        rows=json.loads(part_reader_brief(value,[first['title']],ctx).split('\n')[-1])
        self.assertEqual([r['section_id'] for r in rows],[first['id']])
        self.assertIn(unit,rows[0]['accepted_claims'])
        self.assertNotIn('子節固有の適用条件',part_reader_brief(value,[value['sections'][-1]['title']],ctx))

    def test_only_related_existing_life_goals_are_carried(self):
        *_,ctx,value=chain();first=value['sections'][0]
        n=ctx['reader_design']['reader_needs'][0];n['desired_state']='家族と落ち着いて過ごす時間'
        ctx['reader_design']['reader_needs'].append({'id':'unrelated','desired_state':'別の章の目的'})
        rows=json.loads(part_reader_brief(value,[first['title']],ctx).split('\n')[-1])
        self.assertEqual(rows[0]['reader_goals'],[{'need_id':n['id'],'desired_state':'家族と落ち着いて過ごす時間'}])
        self.assertNotIn('別の章の目的',json.dumps(rows,ensure_ascii=False))
