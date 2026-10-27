import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline import editorial_blueprint as bp,outline_candidate_reuse as reuse,quality_budget as qb
from pipeline.ai import prepare_provider_request
from pipeline.content_quality import ContentQualityError,digest
from test_editorial_blueprint import fixture,artifact


class DocumentedOmissionTests(unittest.TestCase):
    def value(self):
        *_,ctx,value=fixture()
        value['sections'][0]['research_ids']=[]
        value['sections'][0]['answer_elements'][0]['claim_ids']=[]
        value['omissions']=[{'research_id':'q1','reason':'根拠未確認のため掲載保留。確認済みの別の判断材料を先に示す。'}]
        return ctx,value

    def test_delivery_keeps_recorded_omission_but_strict_validation_still_fails(self):
        ctx,value=self.value()
        with self.assertRaises(ContentQualityError):bp.validate(value,ctx)
        with patch('pipeline.quality_warnings.enabled',return_value=True):
            self.assertIs(bp.validate(value,ctx),value)
            saved=artifact(value,ctx)
            self.assertEqual(bp.require_projection(saved),value)
        self.assertEqual(saved['meta']['unresolved_required_omissions'],value['omissions'])
        self.assertEqual(bp.require_projection(saved),value)
        with self.assertRaises(ContentQualityError):bp.validate(value,ctx)
        self.assertNotIn('publication_approved',saved['meta'])

    def test_unrecorded_omission_and_broken_references_still_reject_in_delivery(self):
        ctx,value=self.value()
        with patch('pipeline.quality_warnings.enabled',return_value=True):
            value['omissions']=[]
            with self.assertRaises(ContentQualityError):bp.validate(value,ctx)
            value['omissions']=[{'research_id':'q1','reason':''}]
            with self.assertRaises(ContentQualityError):bp.validate(value,ctx)
            value['omissions']=[{'research_id':'unknown','reason':'未確認'}]
            with self.assertRaises(ContentQualityError):bp.validate(value,ctx)


class SavedOutlineReuseTests(unittest.TestCase):
    def setUp(self):
        *_,self.ctx,self.value=fixture()
        self.request={'model':'claude-opus-5-5','max_tokens':16000,'system':'design',
            'messages':[{'role':'user','content':'same current evidence'}],
            'output_config':bp.output_config()}
        wire,_=prepare_provider_request(self.request)
        payload=json.dumps(wire,ensure_ascii=False,sort_keys=True)
        self.raw=json.dumps(self.value,ensure_ascii=False)
        self.artifacts={'outline_design_incomplete':{'content_text':self.raw},
            'authoring_request_'+digest(payload):{'content_text':json.dumps({'stage':'outline','request':wire})}}
        self.call={'stage':'outline','provider':'anthropic','model':self.request['model'],
            'status':'accounted','cost_usd':.5,'reserved_usd':1,
            'usage':{'input_tokens':100,'output_tokens':200,'stop_reason':'end_turn'}}

    def run_saved(self,calls=None,request=None):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder}):
            value={'job_id':'j','limit_usd':qb.LIMIT,'calls':copy.deepcopy(calls if calls is not None else [self.call])}
            path=Path(folder)/(hashlib.sha256(b'j').hexdigest()+'.json')
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2))
            with qb.scope('j','outline'),patch('pipeline.db.get_optional_artifact',side_effect=lambda j,s:self.artifacts.get(s)),patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
                result=reuse.saved('j',request or self.request)
            self.assertEqual(json.loads(path.read_text()),value)
            return result

    def test_exact_saved_response_reused_without_network_or_new_charge(self):
        before=copy.deepcopy(self.request)
        result=self.run_saved()
        self.assertEqual(result.content[0].text,self.raw)
        self.assertEqual(result.usage.output_tokens,200)
        self.assertEqual(result.stop_reason,'end_turn')
        self.assertEqual(self.request,before)
        bp.validate(json.loads(result.content[0].text),self.ctx)

    def test_changed_input_never_falls_through_to_paid_generation(self):
        request=copy.deepcopy(self.request);request['messages'][0]['content']='different sources'
        with self.assertRaises(ContentQualityError):self.run_saved(request=request)

    def test_uncertain_duplicate_or_truncated_receipt_is_not_adopted(self):
        for calls in ([{**self.call,'status':'pending'}],[self.call,self.call],
                      [{**self.call,'usage':{**self.call['usage'],'stop_reason':'max_tokens'}}]):
            with self.subTest(calls=calls),self.assertRaises(ContentQualityError):self.run_saved(calls=calls)

    def test_wire_normalization_is_pure_and_stable(self):
        original=copy.deepcopy(self.request)
        one,_=prepare_provider_request(self.request);two,_=prepare_provider_request(self.request)
        self.assertEqual(one,two);self.assertEqual(self.request,original)
        self.assertEqual(one['max_tokens'],24000)
        self.assertNotIn('format',one['output_config'])
