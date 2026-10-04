import unittest
import json
from bs4 import BeautifulSoup
from pipeline.fresh_sources import preserve_table_grid, reference_images

class SourceTableTests(unittest.TestCase):
    def test_price_image_is_exposed_without_inventing_a_transcript(self):
        soup=BeautifulSoup('''<main><h2>料金</h2>
<picture><img src="./assets/img/price_man@2x.png?202505" alt="男性料金プラン・詳細"></picture>
<img src="./assets/img/price_woman@2x.png?202505" alt="女性料金プラン・詳細">
<img src="logo.png" alt="logo"></main>''','lxml')
        rows=reference_images(soup,'https://official.example/',lambda u:None)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['url'],'https://official.example/assets/img/price_man@2x.png?202505')
        self.assertTrue(all(r['status']=='image_not_read' and 'text' not in r for r in rows))

    def test_image_discovery_keeps_blocklist_dedup_and_size_limit(self):
        soup=BeautifulSoup(''.join(f'<img src="https://example.org/{n}" alt="料金">' for n in range(30))+
                           '<img src="data:image/png;base64,price" alt="料金">','lxml')
        def allowed(url):
            if url.endswith('/0'):raise ValueError('blocked')
        rows=reference_images(soup,'https://official.example/',allowed)
        self.assertEqual(len(rows),20)
        self.assertFalse(any(r['url'].endswith('/0') for r in rows))

    def test_unread_image_metadata_reaches_verifier_but_is_not_a_quote(self):
        from pipeline.content_quality import source_evidence
        from pipeline.source_spans import indexed_sources
        from pipeline.research_collection import source_fingerprint
        page={'url':'https://official.example','status':'success','text':'取得した通常の本文。',
              'text_only':True,'image_sources':[{'url':'https://official.example/price.png',
                  'alt':'料金プランの画像','status':'image_not_read'}]}
        pages=json.loads(source_evidence({'content_text':json.dumps([page])}))
        visible,quotes=indexed_sources(pages)
        self.assertEqual(visible[0]['image_sources'],page['image_sources'])
        self.assertTrue(visible[0]['text_only'])
        self.assertFalse(any('料金プランの画像' in r['quote'] for r in quotes.values()))
        first=source_fingerprint({page['url']:page},[page['url']])
        page['image_sources'][0]['url']='https://official.example/new-price.png'
        self.assertNotEqual(first,source_fingerprint({page['url']:page},[page['url']]))

    def test_free_rowspan_remains_in_same_column_for_every_period(self):
        soup = BeautifulSoup('''<main><table>
<tr><td colspan="2">女性</td><td>スタンダード</td><td>プレミアム</td></tr>
<tr><td></td><td>1か月</td><td rowspan="3">無料</td><td>1,380円/月</td></tr>
<tr><td></td><td>3か月</td><td>980円/月</td></tr>
<tr><td>追加期間</td><td>6か月</td><td>680円/月</td></tr>
</table></main>''','lxml')
        preserve_table_grid(soup)
        text=soup.get_text(' ',strip=True)
        self.assertIn('女性 | 女性 | スタンダード | プレミアム',text)
        for period,price in [('1か月','1,380'),('3か月','980'),('6か月','680')]:
            self.assertIn(f'{period} | 無料 | {price}円/月',text)

    def test_multiple_header_rows_keep_sex_and_plan_alignment(self):
        soup=BeautifulSoup('<table><tr><th rowspan="2">期間</th><th colspan="2">男性</th><th>女性</th></tr><tr><th>通常</th><th>追加</th><th>通常</th></tr><tr><td>1月</td><td>100</td><td>200</td><td>無料</td></tr></table>','lxml')
        preserve_table_grid(soup)
        text=soup.get_text()
        self.assertIn('期間 | 男性 | 男性 | 女性',text)
        self.assertIn('期間 | 通常 | 追加 | 通常',text)
        self.assertIn('1月 | 100 | 200 | 無料',text)

if __name__=='__main__':unittest.main()
