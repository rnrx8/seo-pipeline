import json
import unittest
from pipeline.content_quality import source_body_limits,source_evidence

class SourceBudgetTests(unittest.TestCase):
    def test_short_pages_release_capacity_without_reducing_any_page(self):
        pages={str(i):{'text':'x'*n} for i,n in enumerate([10,10,10,400,500])}
        limits=source_body_limits(pages,500)
        self.assertEqual(sum(limits.values()),500)
        for key,page in pages.items():self.assertGreaterEqual(limits[key],min(len(page['text']),100))
        self.assertEqual(limits['3'],235)

    def test_middle_price_is_preserved_when_short_pages_free_capacity(self):
        text='前提'*80+'男性 1ヶ月契約 9,980円 税込。'+'後半'*80
        pages=[{'url':'https://a.test/price','status':'success','text':text}]
        pages += [{'url':f'https://a.test/{i}','status':'success','text':'短い説明'} for i in range(4)]
        result=json.loads(source_evidence({'content_text':json.dumps(pages)},max_chars=400))
        price=next(p for p in result if p['url'].endswith('/price'))
        self.assertIn('男性 1ヶ月契約 9,980円 税込。',price['text'])
        self.assertFalse(price['truncated'])
        self.assertEqual(price['text'],text)

    def test_small_budget_and_fetch_truncation_are_not_hidden(self):
        pages={'a':{'text':'a'*30},'b':{'text':'b'*40}}
        self.assertEqual(source_body_limits(pages,0),{'a':1,'b':1})
        data=[{'url':'a','status':'success','text':'既存の取得抜粋','truncated':True}]
        out=json.loads(source_evidence({'content_text':json.dumps(data)},max_chars=100))
        self.assertTrue(out[0]['truncated'])
