import unittest
from pipeline.price_comparison import comparison_evidence, price_tables

CONTRACT = {'required_sections': [{'candidate_services': ['A', 'B']}]}
TABLES = '''まずAの男性料金（税込）は以下の通りです。

| プラン | 1ヶ月 | 3ヶ月 | 12ヶ月 |
|---|---|---|---|
| ミニ | 8,880円 | 6,880円/月（20,640円） | 3,280円/月（39,360円） |
| スタンダード | 10,880円 | 8,880円/月（26,640円） | 5,880円/月（70,560円） |

次にBの男性料金（税込）は以下の通りです。

| プラン | 1ヶ月 | 3ヶ月 | 12ヶ月 |
|---|---|---|---|
| スタンダード | 9,800円 | 7,800円/月（23,400円） | 3,800円/月（45,600円） |
| プレミアム | 11,800円 | 9,800円/月（29,400円） | 5,800円/月（69,600円） |
'''


class PriceComparisonTests(unittest.TestCase):
    def test_unqualified_long_term_conclusion_uses_minimum_not_matching_plan_name(self):
        _, issues = comparison_evidence('短期（1〜3ヶ月）ならAが安く、長期プランの月額はBが割安です。\n' + TABLES, CONTRACT)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['months'], 12)
        self.assertEqual(issues[0]['minimum_listed'], 3800)
        self.assertEqual(issues[0]['lower_priced_alternatives'][0]['monthly'], 3280)

    def test_explicit_plan_comparison_is_not_blocked(self):
        text = TABLES + '\n12ヶ月のスタンダード同士ではBが安いです。機能は別途比較が必要です。'
        self.assertEqual(comparison_evidence(text, CONTRACT)[1], [])

    def test_accurate_general_comparison_passes(self):
        self.assertEqual(comparison_evidence(TABLES + '\n長期プランの月額はAが割安です。', CONTRACT)[1], [])

    def test_total_only_values_are_not_assumed_monthly(self):
        tables = price_tables(TABLES.replace('3,280円/月（39,360円）', '39,360円'), ['A', 'B'])
        self.assertFalse(any(t['service'] == 'A' and t['months'] == 12 for t in tables))

    def test_different_tax_or_gender_conditions_are_not_ranked(self):
        for replacement in ('Bの女性料金（税込）', 'Bの男性料金（税別）'):
            text = TABLES.replace('Bの男性料金（税込）', replacement) + '\n長期プランの月額はBが割安です。'
            self.assertEqual(comparison_evidence(text, CONTRACT)[1], [])


if __name__ == '__main__':unittest.main()

class SavedOutputRegressionTests(unittest.TestCase):
    def test_actual_generated_fee_contradiction_is_detected(self):
        from pathlib import Path
        text = (Path(__file__).parent/'fixtures/pair-price-contradiction.md').read_text()
        contract = {'required_sections': [{'candidate_services': ['既婚者クラブ', 'ヒールメイト']}]}
        _, issues = comparison_evidence(text, contract)
        self.assertTrue(any(i['months'] == 12 and i['minimum_listed'] == 3800 and
                            i['lower_priced_alternatives'][0]['monthly'] == 3280 for i in issues))

    def test_actual_generated_eight_selection_article_is_rejected(self):
        from pathlib import Path
        from pipeline.article_quality import validate_promised_comparison_count
        text = (Path(__file__).parent/'fixtures/free-comparison-incomplete.md').read_text()
        issues = validate_promised_comparison_count(text)
        self.assertEqual(issues[0]['promised'], 8)
        self.assertEqual(issues[0]['table_count'], 3)
