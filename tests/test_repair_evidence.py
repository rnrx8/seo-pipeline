import copy
import unittest
from pipeline.repair_evidence import VERSION, validate, original_span
from pipeline.review_scope import repair_packet
from pipeline.content_quality import ContentQualityError


class RepairEvidenceTests(unittest.TestCase):
    def test_whitespace_changes_restore_original_without_changing_words(self):
        self.assertEqual(original_span('特に、既婚者。結果として。','前。特に、既婚者。 結果として。後。'),
                         '特に、既婚者。 結果として。')

    def test_table_lines_restore_intervening_conditions(self):
        body='Basic 12 ヶ月 プラン （360日） ¥ 8,480 /月 一括 ¥ 101,760 Prime 12 ヶ月 プラン （360日） 1ヶ月プランから 約30%OFF ¥ 13,980 /月 一括 ¥ 167,760 次。'
        quote='12 ヶ月 プラン （360日）\n¥ 13,980 /月\n一括 ¥ 167,760'
        # Multiple possible starts are deliberately ambiguous: do not silently
        # use Basic as the subject for a Prime price.
        self.assertIsNone(original_span(quote,body))
        prime=body[body.index('Prime'):]
        self.assertEqual(original_span(quote,prime),'12 ヶ月 プラン （360日） 1ヶ月プランから 約30%OFF ¥ 13,980 /月 一括 ¥ 167,760')

    def test_wrong_numbers_words_and_unbounded_gaps_are_not_repaired(self):
        self.assertIsNone(original_span('料金は19,980円。','料金は14,980円。'))
        self.assertIsNone(original_span('登録だけで利用可能。','登録後は本人確認で利用可能。'))
        self.assertIsNone(original_span('Primeプランの条件\n月額料金は19,980円','Primeプランの条件'+'別'*201+'月額料金は19,980円'))

    def test_normalized_ambiguous_quote_is_rejected(self):
        self.assertIsNone(original_span('料金は\n19,980円','料金は 19,980円。他社料金は 19,980円'))

    def setUp(self):
        self.quote='男性Primeは30日14,980円。シークレット機能を含む。'
        self.pages=[{'url':'https://a.example/price','text':'無関係。'*10000+self.quote+'別条件。'*10000}]
        self.text='# 比較\n\n料金を比較する。'
        self.check={'key':'comparison_conditions','status':'fail','reason':'必要プランの条件を補う',
                    'affected_blocks':[{'id':'block-0002','reason':'条件不足'}],
                    'source_urls':[self.pages[0]['url']],'research_ids':['q1'],'requires_source_check':False,
                    'correction_evidence':[{'url':self.pages[0]['url'],'quote':self.quote}]}
        self.req={'research_decisions':{'items':[{'id':'q1','status':'confirmed','verified':True,'basis':'primary',
            'answer':'Basicは男性30日9,980円。','supports_current_conclusion':False,
            'applicable_at':'2025年の条件','omission_reason':'女性条件は未確認','evidence_sources':[{'url':self.pages[0]['url']}]}]}}
        self.report={'checks':[self.check],'evidence_pending':False,
                     'evidence_routing':[{'repair_evidence_version':VERSION,'review':{'needed':False}}]}

    def test_exact_quotes_and_all_conditions_survive_without_whole_bodies(self):
        before=copy.deepcopy(self.report)
        packet=repair_packet(self.text,self.req,self.pages,self.report)
        self.assertEqual(packet['source_documents'],[])
        self.assertNotIn('candidate_checks',packet)
        self.assertEqual(packet['correction_sources']['correction-1']['quote'],self.quote)
        self.assertEqual(packet['failed_checks'][0]['correction_source_ids'],['correction-1'])
        row=packet['requirements']['research_decisions']['items'][0]
        self.assertEqual(row['answer'],self.req['research_decisions']['items'][0]['answer'])
        self.assertFalse(row['supports_current_conclusion'])
        self.assertEqual(packet['requirements']['usage_constraints']['q1'],
                         {'applicable_at':'2025年の条件','omission_reason':'女性条件は未確認'})
        self.assertEqual(self.report,before)

    def test_same_quote_shared_across_checks_is_sent_once(self):
        self.report['checks'].append({**self.check,'key':'coverage'})
        packet=repair_packet(self.text,self.req,self.pages,self.report)
        self.assertEqual(len(packet['correction_sources']),1)
        self.assertEqual(packet['failed_checks'][0]['correction_source_ids'],packet['failed_checks'][1]['correction_source_ids'])

    def test_reconstructed_quote_does_not_mutate_saved_review(self):
        self.pages[0]['text']=self.quote.replace('。','。 ')
        before=copy.deepcopy(self.report)
        packet=repair_packet(self.text,self.req,self.pages,self.report)
        self.assertIn(packet['correction_sources']['correction-1']['quote'],self.pages[0]['text'])
        self.assertEqual(self.report,before)

    def test_changed_or_fabricated_quote_is_rejected(self):
        for ref in ({'url':'https://invented.example','quote':self.quote},
                    {'url':self.pages[0]['url'],'quote':'男性Primeは無料。'}):
            with self.subTest(ref=ref):
                self.check['correction_evidence']=[ref]
                with self.assertRaises(ContentQualityError):repair_packet(self.text,self.req,self.pages,self.report)

    def test_legacy_resolved_check_cannot_fall_back_to_full_sources(self):
        self.report['evidence_routing'][0].pop('repair_evidence_version')
        with self.assertRaises(ContentQualityError):repair_packet(self.text,self.req,self.pages,self.report)

    def test_article_phase_trace_is_checked_too(self):
        self.report['phases']={'evidence':{'evidence_routing':self.report.pop('evidence_routing')}}
        self.report['phases']['evidence']['evidence_routing'][0].pop('repair_evidence_version')
        with self.assertRaises(ContentQualityError):repair_packet(self.text,self.req,self.pages,self.report)

    def test_unresolved_fact_never_reaches_repair(self):
        self.report['evidence_pending']=True
        with self.assertRaises(ContentQualityError):repair_packet(self.text,self.req,self.pages,self.report)

    def test_source_review_must_supply_quote_for_resolved_failure(self):
        self.check['correction_evidence']=[]
        with self.assertRaises(ContentQualityError):validate([self.check],self.pages,unresolved=False)
        validate([self.check],self.pages,unresolved=True)

    def test_long_page_growth_does_not_grow_repair_context(self):
        first=repair_packet(self.text,self.req,self.pages,self.report)
        self.pages[0]['text']+='無関係な説明。'*100000
        self.assertEqual(first,repair_packet(self.text,self.req,self.pages,self.report))
