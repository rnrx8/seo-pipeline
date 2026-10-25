import unittest
from pipeline.readability import readability_issues, long_sentence_candidates, reference_entry


class ReferenceProseTests(unittest.TestCase):
    def test_source_definitions_do_not_extend_summary_prose(self):
        text='## まとめ\n\n'+'暮らしに合わせて確認します。'*12
        refs='\n\n'+'\n'.join(f'※{i}: https://example.test/'+('source/'*15)+str(i) for i in range(1,7))
        self.assertFalse(readability_issues(text))
        self.assertEqual(readability_issues(text+refs),readability_issues(text))
        self.assertEqual(long_sentence_candidates(text+refs),long_sentence_candidates(text))

    def test_regular_prose_cannot_hide_behind_reference_label_or_url(self):
        for line in ('※1: https://example.test ここから通常の長い説明です。',
                     '参考に https://example.test を使います。', '※1: '+'通常の文章。'*60):
            self.assertFalse(reference_entry(line))
        self.assertTrue(readability_issues('## 説明\n\n'+'通常の文章。'*70))

    def test_supported_reference_notations_and_dates(self):
        for line in ('※1: https://example.test', '[^a]: https://example.test',
                     '[1]: https://example.test （確認日2026-10-26）',
                     '※2: https://example.test/a https://example.test/b'):
            self.assertTrue(reference_entry(line),line)
