import unittest
from bs4 import BeautifulSoup
from pipeline.fresh_sources import preserve_table_grid

class SourceTableTests(unittest.TestCase):
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
