import copy,json,unittest
from unittest.mock import patch
from pipeline import scoped_edit_suffix as suffix,editorial_blueprint as bp
from pipeline.content_edits import scoped_repair_output_config
from pipeline.content_quality import ContentQualityError,digest

class ScopedEditSuffixTests(unittest.TestCase):
    def fixture(self):
        schema=bp.obj({'article_edits':scoped_repair_output_config(['a','b'])['format']['schema']['properties']['edits'],
            'scope_promises':bp.obj({'p':bp.STRING})})
        request={'model':'claude-opus-5-5','max_tokens':6000,'system':'編集','messages':[{'role':'user','content':'資料'}],
            'output_config':bp.model_output_config(schema)}
        head={'id':'a','new':'完成した段落。'}
        partial='{"article_edits":['+json.dumps(head,ensure_ascii=False)+',{"id":"b","new":"途中'
        artifact={'content_text':partial,'meta':{'stop_reason':'max_tokens','request_sha256':digest(json.dumps(request,ensure_ascii=False,sort_keys=True))}}
        packet={'target_block_ids':['a','b'],'article_blocks':[],'mandatory_reader_block_ids':['a','b'],
            'paragraph_limits':{'a':80,'b':80},'scope_promises':{'p':'元の約束'},'article_design':{'reader_focus':{}},'findings':[]}
        return request,artifact,packet,head

    def test_complete_array_prefix_is_frozen_and_only_tail_is_bought(self):
        request,a,p,head=self.fixture();before=copy.deepcopy((a,p))
        def send(job,step,r,**kwargs):
            self.assertEqual(step,'tiered_scoped_edit_suffix');data=json.loads(r['messages'][0]['content'])
            self.assertEqual(data['completed_edits_readonly'],[head]);self.assertEqual(data['target_block_ids'],['b'])
            self.assertEqual(r['output_config']['format']['schema']['properties']['article_edits']['items']['properties']['id']['enum'],['b'])
            return {'article_edits':[{'id':'b','new':'不足分の完成。'}],'scope_promises':{'p':'修正後の約束'}},None
        with patch('pipeline.editorial_contract.enrich_request',side_effect=lambda r:r),patch('pipeline.tiered_research.checked_request',side_effect=send) as sent,patch('pipeline.db.upsert_artifact'):
            result=suffix.recover('job',request,a,p,None)
        self.assertEqual(result['article_edits'][0],head);self.assertEqual(sent.call_count,1);self.assertEqual((a,p),before)

    def test_bad_binding_duplicate_and_no_complete_edit_prevent_paid_request(self):
        for kind in ('binding','duplicate','empty','stop'):
            request,a,p,head=self.fixture()
            if kind=='binding':a['meta']['request_sha256']='other'
            elif kind=='stop':a['meta']['stop_reason']='error'
            elif kind=='empty':a['content_text']='{"article_edits":[{"id":"a","new":"truncated'
            else:a['content_text']='{"article_edits":['+json.dumps(head)+','+json.dumps(head)+',{"id":"b","new":"truncated'
            with self.subTest(kind=kind),patch('pipeline.editorial_contract.enrich_request',side_effect=lambda r:r),patch('pipeline.tiered_research.checked_request') as sent:
                with self.assertRaises(ContentQualityError):suffix.recover('job',request,a,p,None)
                sent.assert_not_called()

    def test_suffix_cannot_return_a_completed_edit_again(self):
        request,a,p,head=self.fixture()
        with patch('pipeline.editorial_contract.enrich_request',side_effect=lambda r:r),patch('pipeline.tiered_research.checked_request',return_value=({'article_edits':[head],'scope_promises':{'p':'説明'}},None)),patch('pipeline.db.upsert_artifact'):
            with self.assertRaises(ContentQualityError):suffix.recover('job',request,a,p,None)
