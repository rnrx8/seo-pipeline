import json
import unittest
from unittest.mock import patch

from pipeline import article_citations as citations
from pipeline.content_quality import ContentQualityError


class ArticleCitationTests(unittest.TestCase):
    def sources(self):
        return {'ref1':{'url':'https://example.test/a','quote':'alpha'},
                'ref2':{'url':'https://example.test/b','quote':'beta'},
                'ref3':{'url':'https://example.test/a','quote':'another excerpt'}}

    def test_three_parts_share_source_identity_and_footer_is_generated_once(self):
        registry = citations.build(self.sources())
        parts = ['# 記事\n\n## 最初\n\n情報A。[[cite:ref1]]',
                 '## 次\n\n情報B。[[cite:ref2]]',
                 '## まとめ\n\n同じ情報A。[[cite:ref3]]']
        text = citations.render('\n\n'.join(citations.render(p,registry,footer=False) for p in parts),registry)
        self.assertIn('情報B。※2',text)
        self.assertIn('同じ情報A。※1',text)
        self.assertEqual(text.count('※1: https://example.test/a'),1)
        self.assertEqual(text.count('※2: https://example.test/b'),1)
        self.assertEqual(citations.integrity_issues(text,registry),[])
        self.assertEqual(citations.render(text,registry),text)

    def test_scoped_source_subset_never_renumbers_existing_article(self):
        first = citations.build(self.sources())
        subset = citations.build({'scoped':{'url':'https://example.test/b'}},first)
        self.assertEqual(citations.tokens('説明[[cite:scoped]]',subset),'説明※2')
        changed = citations.build({'new':{'url':'https://example.test/c'}},first)
        self.assertEqual(changed['source_ids']['new'],3)
        self.assertEqual(changed['entries'][:2],first['entries'])

    def test_unknown_and_conflicting_references_are_never_silently_bound(self):
        registry = citations.build(self.sources())
        for text in ('説明[[cite:unknown]]','説明※8','説明[[cite:ref1',
                     '説明※1\n\n※1: https://example.test/b'):
            with self.subTest(text=text),self.assertRaises(ContentQualityError):
                citations.render(text,registry)
        self.assertTrue(citations.integrity_issues('説明※8\n\n※1: https://example.test/a'))

    def test_literal_code_does_not_create_citations_or_invalid_reference_errors(self):
        registry=citations.build(self.sources())
        text='# 書式例\n\n```text\n[[cite:unknown]]\n※99: https://literal.test/\n```\n\n本文[[cite:ref2]]'
        rendered=citations.render(text,registry)
        self.assertIn('[[cite:unknown]]',rendered)
        self.assertNotIn('※99: https://literal.test/\n※2:',rendered)
        self.assertEqual(citations.integrity_issues(rendered,registry),[])

    def test_legacy_definitions_keep_identity_but_do_not_assert_claim_support(self):
        evidence='採用済み資料\n'+json.dumps({'sources':self.sources()})
        article={'content_text':'# 記事\n\n説明※1\n\n※1: https://example.test/b'}
        registry=citations.for_article(article,evidence)
        self.assertEqual(registry['source_ids']['ref2'],1)
        self.assertEqual(registry['source_ids']['ref1'],2)
        with self.assertRaises(ContentQualityError):
            citations.for_article({'content_text':'※1: https://unreviewed.test/'},evidence)

    def test_audit_rejects_missing_definition_before_any_paid_check(self):
        from pipeline import step_content_audit as audit
        with patch.object(audit,'get_job',return_value={'citation_style':'inline_footnote'}), \
             patch.object(audit,'get_artifact',return_value={'content_text':'# 記事\n\n根拠※8','meta':{}}), \
             patch.object(audit,'audit') as send:
            with self.assertRaisesRegex(ContentQualityError,'有料QAの前'):
                audit.run('local','kw')
            send.assert_not_called()

    def test_part_writer_renders_fixed_ids_before_delivery(self):
        from pipeline import step_article as article
        outline='### H2：比較'
        registry=citations.build(self.sources())
        with patch.object(article,'_call',return_value=('# 記事\n\n## 比較\n\n情報Bです。[[cite:ref2]]【PART1_END】',10,10)):
            text,_,_=article._write_complete_part(None,[{'role':'user','content':'依頼'}],1000,outline,
                [('比較',1,10)],1,citation_registry=registry)
        self.assertIn('情報Bです。※2',text)
        self.assertNotIn('[[cite:',text)

    def test_invalid_citation_reply_is_preserved_without_retrying_the_part(self):
        from pipeline import step_article as article
        registry=citations.build(self.sources())
        raw='# 記事\n\n## 比較\n\n情報。[[cite:unknown]]【PART1_END】'
        with patch.object(article,'_call',return_value=(raw,10,10)) as send, \
             patch.object(article,'get_artifact',side_effect=KeyError('No saved response')), \
             patch.object(article,'upsert_artifact') as save:
            with self.assertRaises(ContentQualityError):
                article._write_complete_part(None,[{'role':'user','content':'依頼'}],1000,'### H2：比較',
                    [('比較',1,10)],1,job_id='local',citation_registry=registry)
            self.assertEqual(send.call_count,1)
            self.assertIn('[[cite:unknown]]',save.call_args.kwargs['content_text'])
            self.assertFalse(save.call_args.kwargs['meta']['valid'])

    def test_block_patch_preserves_identity_and_rebuilds_footer(self):
        from pipeline.step_content_audit import apply_reviewed_edits
        from pipeline.section_identity import bind_sections
        registry=citations.build(self.sources())
        text='# 記事\n\n## 比較\n\n元の情報です。※1\n\n※1: https://example.test/a\n'
        outline='### H2：比較'
        report={'checks':[], 'editorial_findings':[{'severity':'blocking','stage_to_fix':'article','block_ids':['block-0004']}]}
        raw=json.dumps({'edits':[{'id':'block-0004','new':'情報Bへ修正します。[[cite:ref2]]'}]})
        changed,binding=apply_reviewed_edits(text,raw,None,report,[],'',outline,None,{},bind_sections(text,outline),citation_registry=registry)
        self.assertIn('情報Bへ修正します。※2',changed)
        self.assertNotIn('※1:',changed)
        self.assertIn('※2: https://example.test/b',changed)
        self.assertFalse(citations.integrity_issues(changed,registry))
