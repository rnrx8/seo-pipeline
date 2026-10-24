import copy
import unittest

from pipeline import article_design as ad,article_progression as ap,content_quality as cq,editorial_blueprint as bp
from test_article_progression import fixture
from test_reader_progression import chain
from test_editorial_blueprint import artifact,verdict
from pipeline.reader_journey import checked


class ReaderJourneyTests(unittest.TestCase):
    def rendered_quote(self,quote):
        return {'reader_state':'方法を選ぶ段階',
            'attunement':{'status':'not_needed','reason':'直接回答で足りる','evidence':[]},
            'lived_value':{'status':'not_needed','reason':'直接回答で足りる','evidence':[]},
            'guidance':{'status':'pass','reason':'本文の判断に対応','evidence':[{'id':'b01','quote':quote}]}}

    def test_rendered_strong_quote_preserves_exact_words(self):
        quote='まず顔を合わせて雰囲気を確かめたい人 → 交流会。'
        text='- **まず顔を合わせて雰囲気を確かめたい人** → 交流会。開催場所も確かめます。'
        self.assertEqual(checked(self.rendered_quote(quote),{'b01':[text]}),[])

    def test_strong_normalization_does_not_accept_changed_words_or_punctuation(self):
        text='**まず顔を合わせたい人** → 交流会。'
        for quote in ['必ず顔を合わせたい人 → 交流会。','まず顔を合わせたい人 は 交流会。']:
            with self.subTest(quote=quote),self.assertRaises(cq.ContentQualityError):
                checked(self.rendered_quote(quote),{'b01':[text]})

    def test_escaped_literal_stars_are_not_silently_removed(self):
        with self.assertRaises(cq.ContentQualityError):
            checked(self.rendered_quote('大事な 文字 を残す。'),{'b01':[r'大事な \**文字\** を残す。']})

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

    def test_attunement_and_guidance_cannot_cancel_missing_lived_value(self):
        _,design,text,mapping,value=fixture()
        row=value['reader_progression_reviews']['s01']
        row['journey']['lived_value'].update(status='fail',reason='確認手順はあるが選択の先の望みが失われている',evidence=[])
        self.assertEqual(row['status'],'pass')
        self.assertEqual(row['journey']['attunement']['status'],'pass')
        self.assertEqual(row['journey']['guidance']['status'],'pass')
        issues=ap.checked(value,design,mapping,text)
        self.assertEqual(issues[0]['section_id'],'s01')
        self.assertIn('選択の先に望む経験',issues[0]['reason'])
        self.assertTrue(issues[0]['affected_blocks'])

    def test_outline_lived_value_failure_routes_back_to_editorial_design(self):
        *_,ctx,blueprint=chain();rctx=bp.review_context(artifact(blueprint,ctx));value=verdict(rctx)
        value['reader_progression_reviews']['s02']['journey']['lived_value'].update(status='fail',reason='作業の成功で止まり望む過ごし方へつながらない',evidence=[])
        issues=bp.checked_progression_reviews(value,rctx)
        self.assertEqual(issues[0]['section_ids'],['s02'])
        self.assertEqual(issues[0]['repair_kind'],'editorial')
        self.assertIn('選択の先に望む経験',issues[0]['reason'])

    def test_combined_old_verdict_without_lived_value_is_not_current_acceptance(self):
        _,design,text,mapping,value=fixture()
        value['reader_progression_reviews']['s01']['journey'].pop('lived_value')
        with self.assertRaises(cq.ContentQualityError):ap.checked(value,design,mapping,text)

    def test_lived_value_quote_cannot_come_from_another_chapter(self):
        _,design,text,mapping,value=fixture()
        value['reader_progression_reviews']['s01']['journey']['lived_value']=copy.deepcopy(
            value['reader_progression_reviews']['s02']['journey']['lived_value'])
        with self.assertRaises(cq.ContentQualityError):ap.checked(value,design,mapping,text)

    def test_review_output_reserves_the_additional_independent_verdict(self):
        from pipeline.tiered_research import structured_review_output_limit
        _,design,_,_,_=fixture()
        config=ad.review_config(cq.audit_output_config(('coverage',),locations=True),design)
        request={'model':'gpt-6.1-sol','max_tokens':1000,'output_config':config}
        current=structured_review_output_limit(request)
        old=copy.deepcopy(request)
        rows=old['output_config']['format']['schema']['properties']['reader_progression_reviews']['properties']
        for row in rows.values():row['properties']['journey']['properties'].pop('lived_value')
        self.assertEqual(current-structured_review_output_limit(old),400*len(rows))

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
        journey['lived_value'].update(status='not_needed',reason='定義の回答は過ごし方の説明で変わらないため',evidence=[])
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
