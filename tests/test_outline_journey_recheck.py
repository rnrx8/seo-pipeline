import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import editorial_blueprint as bp, outline_journey_recheck as recheck
from pipeline.content_quality import ContentQualityError
from test_editorial_blueprint import fixture, artifact, verdict


class OutlineJourneyRecheckTests(unittest.TestCase):
    def inputs(self):
        *_,ctx,v=fixture();a=artifact(v,ctx);review=bp.review_context(a);value=verdict(review)
        value['checks']=[{'key':'coverage','status':'fail','reason':'別の具体的な内容不足'}]
        row=value['reader_progression_reviews']['s01']
        row['journey']['guidance']['evidence']=[{'id':'s01','quote':v['sections'][0]['reader_progression']['after']['decision']}]
        return a,review,value

    def test_only_invalid_rows_are_rejudged_from_real_semantic_spans(self):
        a,ctx,value=self.inputs();rows=recheck.invalid_rows(value,ctx,a['content_text'])
        self.assertEqual(list(rows),['s01'])
        texts=[span['quote'] for span in rows['s01']['spans'].values()]
        self.assertNotIn('自分の用途に必要なプランを選べる',texts)
        response={'s01':copy.deepcopy(value['reader_progression_reviews']['s01'])}
        for key in ('attunement','lived_value','guidance'):
            response['s01']['journey'][key]['evidence']=['s01.text001'] if response['s01']['journey'][key]['status']=='pass' else []
        fixed=recheck.apply(value,response,rows,ctx,a['content_text'])
        self.assertEqual(fixed['checks'],value['checks'])
        self.assertEqual(fixed['blueprint_reviews'],value['blueprint_reviews'])
        self.assertEqual(recheck.invalid_rows(fixed,ctx,a['content_text']),{})
        self.assertEqual(value['reader_progression_reviews']['s01']['journey']['guidance']['evidence'][0]['quote'],'自分の用途に必要なプランを選べる')

    def test_missing_content_cannot_be_promoted_by_citation_recheck(self):
        a,ctx,value=self.inputs();old=value['reader_progression_reviews']['s01'];old['status']='answer_missing';old['journey']['guidance']['status']='fail'
        rows=recheck.invalid_rows(value,ctx,a['content_text']);response={'s01':copy.deepcopy(old)}
        for part in response['s01']['journey'].values():
            if isinstance(part,dict):part['evidence']=['s01.text001'] if part['status']=='pass' else []
        response['s01']['status']='pass'
        with self.assertRaisesRegex(ContentQualityError,'不合格'):recheck.apply(value,response,rows,ctx,a['content_text'])
        response['s01']['status']='answer_missing';response['s01']['journey']['guidance']['status']='pass';response['s01']['journey']['guidance']['evidence']=['s01.text001']
        with self.assertRaisesRegex(ContentQualityError,'不足'):recheck.apply(value,response,rows,ctx,a['content_text'])
        response['s01']['journey']['guidance']['status']='fail'
        fixed=recheck.apply(value,response,rows,ctx,a['content_text'])
        self.assertEqual(fixed['reader_progression_reviews']['s01']['status'],'answer_missing')

    def test_unknown_or_other_chapter_span_is_rejected(self):
        a,ctx,value=self.inputs();rows=recheck.invalid_rows(value,ctx,a['content_text']);response={'s01':copy.deepcopy(value['reader_progression_reviews']['s01'])}
        for part in response['s01']['journey'].values():
            if isinstance(part,dict):part['evidence']=['s99.text001'] if part['status']=='pass' else []
        with self.assertRaises(ContentQualityError):recheck.apply(value,response,rows,ctx,a['content_text'])

    def test_valid_quotes_do_not_send_a_recheck(self):
        *_,ctx,v=fixture();a=artifact(v,ctx);review=bp.review_context(a);value=verdict(review)
        with patch('pipeline.tiered_research.checked_request') as send:
            got,usage,trace=recheck.recheck('j',value,review,a['content_text'])
        self.assertEqual(got,value);self.assertEqual(usage.input_tokens,0);self.assertIsNone(trace);send.assert_not_called()
