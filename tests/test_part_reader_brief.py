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
