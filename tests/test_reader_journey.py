import copy
import unittest

from pipeline import article_design as ad,article_progression as ap,content_quality as cq,editorial_blueprint as bp
from test_article_progression import fixture
from test_reader_progression import chain
from test_editorial_blueprint import artifact,verdict
from pipeline.reader_journey import checked


class ReaderJourneyTests(unittest.TestCase):
    def test_practical_answer_cannot_cancel_missing_attunement(self):
        _,design,text,mapping,value=fixture()
        row=value['reader_progression_reviews']['s01']
        row['journey']['attunement'].update(status='fail',reason='方法はあるが何に迷う人かが本文に表れていない',evidence=[])
        self.assertEqual(row['status'],'pass');self.assertEqual(row['journey']['guidance']['status'],'pass')
        issues=ap.checked(value,design,mapping,text)
        self.assertEqual(issues[0]['section_id'],'s01')
        self.assertIn('気持ちの受け止め',issues[0]['reason'])
        self.assertTrue(issues[0]['affected_blocks'])

    def test_empathy_cannot_cancel_unclear_next_step(self):
        _,design,text,mapping,value=fixture()
        value['reader_progression_reviews']['s02']['journey']['guidance'].update(status='fail',reason='注意が続き何を選ぶか分からない',evidence=[])
        self.assertEqual(ap.checked(value,design,mapping,text)[0]['section_id'],'s02')

    def test_same_separation_rejects_weak_outline_despite_overall_pass(self):
        *_,ctx,blueprint=chain();rctx=bp.review_context(artifact(blueprint,ctx));value=verdict(rctx)
        value['reader_progression_reviews']['s02']['journey']['guidance'].update(status='fail',reason='結論が留保だけで進め方がない',evidence=[])
        issues=bp.checked_progression_reviews(value,rctx)
        self.assertEqual(issues[0]['section_ids'],['s02']);self.assertEqual(issues[0]['repair_kind'],'editorial')

    def test_quote_must_exist_in_own_chapter_not_another_chapter_or_design(self):
        _,design,text,mapping,value=fixture()
        row=value['reader_progression_reviews']['s01']['journey']
        for quote in ['記事には存在しない気持ちの表現',design['sections'][0]['reader_progression']['after']['emotion']]:
            broken=copy.deepcopy(value);broken['reader_progression_reviews']['s01']['journey']['attunement']['evidence'][0]['quote']=quote
            with self.assertRaises(cq.ContentQualityError):ap.checked(broken,design,mapping,text)
        broken=copy.deepcopy(value)
        broken['reader_progression_reviews']['s01']['journey']=copy.deepcopy(value['reader_progression_reviews']['s02']['journey'])
        with self.assertRaises(cq.ContentQualityError):ap.checked(broken,design,mapping,text)

    def test_not_needed_requires_reason_without_forcing_emotional_prose(self):
        _,_,_,_,value=fixture();journey=value['reader_progression_reviews']['s01']['journey']
        quote=journey['guidance']['evidence'][0];evidence={quote['id']:[quote['quote']]}
        journey['reader_state']='適用プランという直接の答えを知りたい状態'
        journey['attunement'].update(status='not_needed',reason='定義への直接回答で次の確認へ進めるため',evidence=[])
        self.assertEqual(checked(journey,evidence),[])
        journey['attunement']['reason']=''
        with self.assertRaises(cq.ContentQualityError):checked(journey,evidence)

    def test_old_overall_only_verdict_or_empty_reader_state_cannot_pass(self):
        _,design,text,mapping,value=fixture()
        for change in ['missing','empty']:
            broken=copy.deepcopy(value);row=broken['reader_progression_reviews']['s01']
            if change=='missing':row.pop('journey')
            else:row['journey']['reader_state']=''
            with self.assertRaises(cq.ContentQualityError):ap.checked(broken,design,mapping,text)
