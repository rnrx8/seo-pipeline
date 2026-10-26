import copy,json,os,unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline.scoped_edit_correction import delta,compose
from pipeline.content_quality import ContentQualityError

class ScopedEditCorrectionTests(unittest.TestCase):
    def fixture(self):
        a={'reader_cases':{'cases':[{'id':'a','text':'same'},{'id':'b','text':'old'}]},
            'heading_answer_entries':[{'id':'h','opening_quote':'old'}]}
        b=copy.deepcopy(a);b['reader_cases']['cases'][1]['text']='changed';b['heading_answer_entries'][0]['opening_quote']='new'
        return a,b

    def test_only_identical_actual_cases_inherit_previous_individual_review(self):
        old,new=self.fixture();v=delta(old,new)
        self.assertEqual([c['id'] for c in v['reader_cases']['cases']],['b'])
        previous={'reader_journeys':{'a':{'source':'first actual case'},'b':{'source':'old b'}},'heading_answer_reviews':{'h':{'source':'old h'}}}
        review={'checks':{'source':'new delta'},'reader_journeys':{'b':{'source':'new b'}},'heading_answer_reviews':{'h':{'source':'new h'}}}
        merged=compose(previous,old,review,v,new)
        self.assertEqual(merged['reader_journeys']['a'],previous['reader_journeys']['a'])
        self.assertEqual(merged['reader_journeys']['b'],review['reader_journeys']['b'])
        self.assertEqual(merged['heading_answer_reviews']['h'],review['heading_answer_reviews']['h'])

    def test_a_changed_case_cannot_be_removed_from_delta_to_reuse_its_old_pass(self):
        old,new=self.fixture();v=delta(old,new);v['reader_cases']['cases']=[]
        previous={'reader_journeys':{'a':{},'b':{}},'heading_answer_reviews':{'h':{}}}
        review={'reader_journeys':{},'heading_answer_reviews':{'h':{}}}
        with self.assertRaises(ContentQualityError):compose(previous,old,review,v,new)

    @patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'})
    def test_automatic_correction_crosses_real_request_metadata_boundary(self):
        from test_scoped_edit_completion import ScopedEditCompletionTests
        from pipeline import scoped_edit_correction as correction,scoped_edit_completion as finish,quality_budget as budget
        fixture=ScopedEditCompletionTests();fixture.setUp()
        article,outline,report,context,proposal=fixture.article,fixture.outline,fixture.report,fixture.ctx,fixture.proposal
        text,revised,binding=finish.apply(article,outline,report,context,proposal)
        old_view,_=finish.view(article,outline,report,context,proposal,text,revised,binding)
        verdict=fixture.verdict(old_view)
        target=finish.packet(article,outline,report,context)['mandatory_reader_block_ids'][0]
        verdict['checks']['evidence_preserved'].update(status='fail',reason=target+'の条件を保持して説明を整える。')
        observed=[]
        def provider(client,**request):
            meta=copy.deepcopy(budget.REQUEST.get());observed.append(meta)
            if meta['role']=='scoped_edit_correction':
                payload=json.loads(request['messages'][0]['content'])
                self.assertIsInstance(payload['failed_checks'],list)
                self.assertEqual(meta['reasons'],[target+'の条件を保持して説明を整える。'])
                original=next(b['text'] for b in finish.content_blocks(context['text']) if b['id']==target)
                value={'article_edits':[{'id':target,'new':original+'使う場面を具体的に整理します。'}],
                    'scope_promises':proposal['scope_promises']}
            else:
                self.assertEqual(meta['role'],'scoped_edit_recheck')
                value=fixture.verdict(json.loads(request['messages'][0]['content']))
            return SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(type='text',text=json.dumps(value,ensure_ascii=False))],
                usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch('pipeline.tiered_research.create_with_retry',side_effect=provider),patch('pipeline.db.get_optional_artifact',return_value=None),patch('pipeline.db.upsert_artifact'),patch('pipeline.tiered_research.get_optional_artifact',return_value=None),patch('pipeline.tiered_research.upsert_artifact'):
            result=correction.correct('job',None,article,outline,report,context,proposal,verdict,old_view,lambda *a:None)
        self.assertEqual([m['role'] for m in observed],['scoped_edit_correction','scoped_edit_recheck'])
        self.assertEqual(result[-1]['source'],'native_model')
