import copy
import json
import unittest
from pipeline.content_quality import source_evidence
from pipeline.source_spans import indexed_sources, expand_references, span_schema
from pipeline.research_requirements import MATRIX_SCHEMA, validate_matrix
from test_evidence_policy import fixture

class SourceSpanTests(unittest.TestCase):
    def test_metadata_and_omission_boundaries_survive(self):
        pages=[{'url':'https://example.com','status':'success','fetched_at':'2026-10-02T12:00:00Z',
            'text':'前半原文\n[中略：取得本文の抜粋]\n後半原文','truncated':True}]
        evidence=json.loads(source_evidence({'content_text':json.dumps(pages)}))
        visible,index=indexed_sources(evidence)
        self.assertEqual(visible[0]['fetched_at'],pages[0]['fetched_at'])
        self.assertTrue(visible[0]['truncated'])
        self.assertEqual({r['quote'] for r in index.values()},{'前半原文','後半原文'})
        self.assertNotIn('text',visible[0])
    def test_every_long_span_is_an_exact_substring_and_url_specific(self):
        text='文字列料金と条件。'*130
        pages=[{'url':url,'text':text} for url in ['https://a.example','https://b.example']]
        _,index=indexed_sources(pages)
        self.assertTrue(all(r['quote'] in text and len(r['quote'])<=256 for r in index.values()))
        self.assertTrue(set(r['url'] for r in index.values())==set(p['url'] for p in pages))
        self.assertGreater(len(index),2)
    def test_expansion_preserves_exact_source_without_trusting_model_quote_or_url(self):
        plan,pages,value=fixture();visible,index=indexed_sources(pages)
        expected=[r['quote'] for r in value['items'][0]['evidence']]
        for ref,page in zip(value['items'][0]['evidence'],visible[1:]):
            ref['source_ref']=page['excerpts'][0]['source_ref'];ref['expert_source_ref']=''
            ref.update(quote='モデルによる書き換え',url='https://wrong.example')
        original=copy.deepcopy(value);expanded=expand_references(value,index)
        self.assertEqual([r['quote'] for r in expanded['items'][0]['evidence']],expected)
        self.assertFalse(validate_matrix(expanded,plan,pages))
        self.assertEqual(value,original)
    def test_unknown_id_or_expert_on_another_url_cannot_pass(self):
        plan,pages,value=fixture();visible,index=indexed_sources(pages)
        for ref,page in zip(value['items'][0]['evidence'],visible[1:]):
            ref.update(source_ref=page['excerpts'][0]['source_ref'],expert_source_ref='')
        for mutate in [lambda r:r.update(source_ref='invented'),
                       lambda r:r.update(expert_source_ref=visible[2]['excerpts'][0]['source_ref'])]:
            bad=copy.deepcopy(value);mutate(bad['items'][0]['evidence'][0])
            expanded=expand_references(bad,index)
            self.assertTrue(validate_matrix(expanded,plan,pages))
            self.assertFalse(expanded['items'][0]['verified'])
    def test_model_schema_requires_references_and_leaves_original_schema_unchanged(self):
        original=copy.deepcopy(MATRIX_SCHEMA);converted=span_schema(MATRIX_SCHEMA)
        evidence=converted['format']['schema']['properties']['items']['items']['properties']['evidence']['items']
        self.assertIn('source_ref',evidence['required']);self.assertIn('expert_source_ref',evidence['required'])
        self.assertNotIn('quote',evidence['properties']);self.assertNotIn('url',evidence['properties'])
        self.assertEqual(MATRIX_SCHEMA,original)

if __name__=='__main__':unittest.main()

class CompactSpanTests(unittest.TestCase):
    def test_original_text_is_preserved_without_overlap(self):
        from pipeline.source_spans import indexed_sources
        body=('料金条件と例外\n'*120)+'最終行'
        visible,index=indexed_sources([{'url':'https://a.example','text':body}])
        self.assertEqual(''.join(x['text'] for x in visible[0]['excerpts']),body)
        self.assertEqual(sum(len(r['quote']) for r in index.values()),len(body))
        self.assertEqual(len(index),len(set(index)))
    def test_identical_text_on_different_pages_keeps_distinct_references(self):
        from pipeline.source_spans import indexed_sources
        pages=[{'url':'https://a.example','text':'同じ本文です。'},{'url':'https://b.example','text':'同じ本文です。'}]
        visible,index=indexed_sources(pages)
        self.assertEqual(len(index),2)
        for page in visible:
            self.assertEqual(index[page['excerpts'][0]['source_ref']]['url'],page['url'])
