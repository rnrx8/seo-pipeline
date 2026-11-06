import copy,json,unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import patch
from pipeline import outline_completion as oc, editorial_blueprint as bp, quality_budget as qb
from pipeline.content_quality import ContentQualityError
from test_editorial_blueprint import fixture


class OutlineSuffixTests(unittest.TestCase):
    def setUp(self):
        for name in ('pipeline.outline_completion.get_optional_artifact','pipeline.tiered_research.get_optional_artifact'):
            p=patch(name,return_value=None);p.start();self.addCleanup(p.stop)

    def setup_value(self):
        *_,ctx,value=fixture()
        request=dict(model='claude-opus-5-5',max_tokens=16000,system='design',messages=[{'role':'user','content':'current materials'}])
        partial=json.dumps({k:v for k,v in value.items() if k!='omissions'},ensure_ascii=False)[:-1]+',"omissions":[{"research_id":"q1","reason":"unfinished'
        artifact={'content_text':partial,'meta':{'request_sha256':oc.fingerprint(request),'stop_reason':'max_tokens','input_tokens':10,'output_tokens':20}}
        return ctx,value,request,artifact

    def logical_value(self):
        ctx, value, request, _ = self.setup_value()
        blank = copy.deepcopy(value['sections'][0])
        blank.update(id='s02', parent_id='s01', level=3, title='', question='', conclusion='',
                     reader_progression=None, budget={'chars':0,'importance':0,'reason':''})
        raw = copy.deepcopy(value); raw['sections'].append(blank)
        artifact = oc.logical_artifact(json.dumps(raw, ensure_ascii=False), request, NS(input_tokens=10,output_tokens=20))
        return ctx, value, request, artifact

    def test_end_turn_with_blank_suffix_requests_only_unfinished_sections(self):
        ctx,value,request,artifact=self.logical_value()
        completed=copy.deepcopy(value['sections'][0]);completed.update(id='s02',parent_id='s01',level=3,
            title='利用する動作で選ぶ',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        with patch('pipeline.tiered_research.checked_request',return_value=({'sections':[completed],'omissions':[]},NS(input_tokens=2,output_tokens=3))) as send:
            result,_=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result['sections'][0],value['sections'][0])
        self.assertEqual(result['sections'][1],completed)
        bp.validate(result,ctx)
        payload=json.loads(send.call_args.args[2]['messages'][0]['content'])
        self.assertEqual(payload['array_prefix']['sections'],value['sections'])
        self.assertEqual(set(payload['missing_schema']['properties']),{'sections','omissions'})
        self.assertEqual(artifact['meta']['stop_reason'],'end_turn')

    def test_logical_completion_cannot_erase_previous_omission_decisions(self):
        ctx,value,request,artifact=self.logical_value()
        raw=json.loads(artifact['content_text']);raw['omissions']=[{'research_id':'q1','reason':'保存済みの掲載判断'}]
        artifact['content_text']=json.dumps(raw,ensure_ascii=False)
        completed=copy.deepcopy(value['sections'][0]);completed['id']='s02'
        with patch('pipeline.tiered_research.checked_request',return_value=({'sections':[completed],'omissions':[]},NS(input_tokens=1,output_tokens=1))):
            with self.assertRaises(ContentQualityError):oc.complete('j',artifact,request,ctx,None)

    def test_complete_or_interior_incomplete_design_is_not_mislabelled_as_suffix(self):
        _,value,request,artifact=self.logical_value()
        self.assertIsNone(oc.logical_artifact(json.dumps(value),request,NS(input_tokens=1,output_tokens=1)))
        raw=json.loads(artifact['content_text']);raw['sections'].append(copy.deepcopy(value['sections'][0]))
        self.assertIsNone(oc.logical_artifact(json.dumps(raw),request,NS(input_tokens=1,output_tokens=1)))

    def test_logical_detector_keeps_the_saved_separated_schema_outside_running_scope(self):
        _,_,request,artifact=self.logical_value()
        raw=json.loads(artifact['content_text'])
        for row in raw['sections']:row['internal_checks']=[]
        result=oc.logical_artifact(json.dumps(raw),request,NS(input_tokens=1,output_tokens=1))
        self.assertIsNotNone(result)
        plan=oc.logical_prefix(result['content_text'],bp.schema(raw))
        self.assertEqual(plan[2],raw['sections'][:1])
        self.assertIn('internal_checks',plan[3]['properties']['sections']['items']['properties'])

    def test_logical_completion_restart_uses_saved_suffix_without_another_request(self):
        ctx,value,request,artifact=self.logical_value()
        completed=copy.deepcopy(value['sections'][0]);completed.update(id='s02',parent_id='s01',level=3,
            title='利用する動作で選ぶ',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        store={'outline_unfinished_design':artifact};calls=[]
        def send(job,step,r,client=None):
            calls.append(step);tail={'sections':[completed],'omissions':[]}
            text=json.dumps(tail,ensure_ascii=False)
            store[step]={'content_text':text,'meta':{'request_sha256':oc.fingerprint(r),'result_sha256':oc.digest(text)}}
            return tail,NS(input_tokens=2,output_tokens=3)
        with patch.object(oc,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))), \
             patch('pipeline.tiered_research.get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))), \
             patch('pipeline.tiered_research.checked_request',side_effect=send):
            first,_=oc.complete('j',oc.saved('j',request),request,ctx,None)
            second,_=oc.complete('j',oc.saved('j',request),request,ctx,None)
        self.assertEqual(first,second);self.assertEqual(calls,['outline_suffix_completion'])
        self.assertEqual(first['sections'][0],value['sections'][0])

    def test_actual_outline_stage_reuses_paid_candidate_and_continues_from_its_suffix(self):
        from pipeline import step_outline
        from test_reader_value_flow import response
        plan,pages,matrix,ctx,value=fixture()
        _,_,_,artifact=self.logical_value()
        raw=json.loads(artifact['content_text'])
        completed=copy.deepcopy(value['sections'][0]);completed.update(id='s02',parent_id='s01',level=3,
            title='利用する動作で選ぶ',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        artifacts={'serp':{'content_text':'{}'},'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}},
            'fact_sheet':{'content_text':'facts'},'fresh_sources':{'content_text':json.dumps(pages)},
            'content_contract':{'content_text':'{"required_sections":[]}'}}
        writes={}
        def save(**kw): writes[kw['step']]=copy.deepcopy(kw);return {'id':'saved',**kw}
        with ExitStack() as stack:
            stack.enter_context(patch('socket.socket.connect',side_effect=AssertionError('offline only')))
            stack.enter_context(patch.object(step_outline,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_outline,'get_job',return_value={}))
            stack.enter_context(patch.object(step_outline.anthropic,'Anthropic'))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            original=stack.enter_context(patch.object(step_outline,'create_with_retry',side_effect=AssertionError('do not regenerate outline')))
            suffix=stack.enter_context(patch('pipeline.tiered_research.checked_request',return_value=({'sections':[completed],'omissions':[]},NS(input_tokens=2,output_tokens=3))))
            stack.enter_context(patch.object(step_outline,'upsert_artifact',side_effect=save))
            # The reuse helper also records these fields on real paid responses.
            candidate=stack.enter_context(patch('pipeline.outline_candidate_reuse.saved')).return_value
            candidate.content=response(raw).content;candidate.usage=NS(input_tokens=10,output_tokens=20)
            candidate.stop_reason='end_turn';candidate.saved_candidate_sha256=oc.digest(json.dumps(raw));candidate.saved_request_sha256='saved'
            saved=step_outline.run('job','条件')
        original.assert_not_called();self.assertEqual(suffix.call_count,1)
        self.assertNotIn('outline_design_incomplete',writes)
        self.assertEqual(json.loads(writes['outline_unfinished_design']['content_text']),raw)
        self.assertTrue(saved['meta']['suffix_completed'])
        self.assertEqual(saved['meta']['editorial_blueprint']['sections'][0],value['sections'][0])
        bp.require_projection(saved,ctx)

    def test_suffix_preserves_every_complete_section_and_passes_original_validator(self):
        ctx,value,request,artifact=self.setup_value()
        with patch('pipeline.tiered_research.checked_request',return_value=({'omissions':[]},NS(input_tokens=2,output_tokens=3))) as send:
            result,_=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result,value)
        bp.validate(result,ctx)
        payload=json.loads(send.call_args.args[2]['messages'][0]['content'])
        self.assertEqual(list(payload['missing_schema']['properties']),['omissions'])
        self.assertEqual(payload['completed_fields']['sections'],value['sections'])
        self.assertEqual(send.call_args.args[2]['max_tokens'],8000)
        self.assertEqual(qb.review_operation('outline_suffix_completion'),('outline','suffix_completion',1))

    def test_complete_array_members_survive_truncation_and_invalid_member_is_not_adopted(self):
        _,value,_,artifact=self.setup_value()
        text=artifact['content_text'].split(',"omissions":')[0]+',"omissions":[{"research_id":"q1","reason":"kept"},{"research_id":"q2","reason":"cut'
        fixed,key,head,remaining,_=oc.prefix(text,bp.SCHEMA)
        self.assertEqual(key,'omissions');self.assertEqual(head,[{'research_id':'q1','reason':'kept'}])
        self.assertNotIn('omissions',fixed);self.assertEqual(fixed['sections'],value['sections'])
        self.assertEqual(list(remaining['properties']),['omissions'])

    def test_remaining_sections_can_be_appended_without_changing_completed_prefix(self):
        _,value,_,_=self.setup_value()
        fields={k:value[k] for k in ('titles','reader_focus','lead_direction')}
        text=json.dumps(fields,ensure_ascii=False)[:-1]+',"sections":['+json.dumps(value['sections'][0],ensure_ascii=False)+',{"id":"cut'
        fixed,key,head,schema,_=oc.prefix(text,bp.SCHEMA)
        self.assertEqual(key,'sections');self.assertEqual(head,value['sections'][:1])
        self.assertEqual(set(schema['properties']),{'sections','omissions'})
        self.assertEqual(schema['properties']['sections']['minItems'],0)

    def test_no_full_rewrite_without_complete_sections_and_no_stale_inputs(self):
        ctx,_,request,artifact=self.setup_value()
        with self.assertRaises(ContentQualityError):oc.prefix('{"titles":["unfinished',bp.SCHEMA)
        artifact['meta']['request_sha256']='stale'
        with patch('pipeline.tiered_research.checked_request') as send:
            with self.assertRaises(ContentQualityError):oc.complete('j',artifact,request,ctx,None)
        send.assert_not_called()

    def test_rejects_completion_that_rewrites_finished_fields(self):
        ctx,_,request,artifact=self.setup_value()
        with patch('pipeline.tiered_research.checked_request',return_value=({'omissions':[],'titles':['changed']},NS(input_tokens=1,output_tokens=1))):
            with self.assertRaises(ContentQualityError):oc.complete('j',artifact,request,ctx,None)

    def test_schema_complete_output_can_be_checked_without_another_model_request(self):
        ctx,value,request,artifact=self.setup_value();artifact['content_text']=json.dumps(value,ensure_ascii=False)
        with patch('pipeline.tiered_research.checked_request') as send:
            result,usage=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result,value);send.assert_not_called();self.assertEqual(usage.output_tokens,0)

    def test_legacy_binding_is_separate_and_requires_matching_partial_and_request(self):
        _,_,request,artifact=self.setup_value();artifact['meta'].pop('request_sha256')
        original=copy.deepcopy(artifact)
        binding={'request_sha256':oc.fingerprint(request),'partial_sha256':oc.digest(artifact['content_text'])}
        with patch.object(oc,'get_optional_artifact',side_effect=[None,artifact,{'content_text':json.dumps(binding)}]):
            recovered=oc.saved('j',request)
        self.assertEqual(recovered['meta']['request_sha256'],oc.fingerprint(request))
        self.assertEqual(artifact,original)
        binding['partial_sha256']='different'
        with patch.object(oc,'get_optional_artifact',side_effect=[None,artifact,{'content_text':json.dumps(binding)}]):
            with self.assertRaises(ContentQualityError):oc.saved('j',request)

    def test_truncated_suffix_reuses_complete_values_then_requests_only_the_remainder(self):
        ctx,value,request,artifact=self.setup_value()
        # Main response contains its first complete section; suffix 1 contains
        # the next complete section but its following field was interrupted.
        second=copy.deepcopy(value['sections'][0]);second.update(id='s02',parent_id='s01',level=3,reader_progression=None)
        value['sections'].append(second)
        artifact['content_text']=json.dumps({k:v for k,v in value.items() if k not in ('sections','omissions')},ensure_ascii=False)[:-1]+',"sections":['+json.dumps(value['sections'][0],ensure_ascii=False)+',{"id":"cut'
        store={};calls=[]
        def send(job,step,r,client=None):
            calls.append((step,copy.deepcopy(r)))
            if step=='outline_suffix_completion':
                store[step+'_incomplete']={'content_text':json.dumps({'sections':[second]},ensure_ascii=False)[:-1]+',"omissions":[{"research_id":"cut',
                    'meta':{'request_sha256':oc.fingerprint(r),'stop_reason':'max_tokens','input_tokens':10,'output_tokens':20}}
                raise ContentQualityError('品質検査の応答が完了していません。')
            self.assertEqual(set(r['output_config']['format']['schema']['properties']),{'omissions'})
            payload=json.loads(r['messages'][0]['content'])
            self.assertEqual(payload['completed_fields']['sections'],value['sections'])
            text=json.dumps({'omissions':[]},ensure_ascii=False)
            store[step]={'content_text':text,'meta':{'request_sha256':oc.fingerprint(r),'result_sha256':oc.digest(text)}}
            return {'omissions':[]},NS(input_tokens=2,output_tokens=3)
        with patch.object(oc,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch('pipeline.tiered_research.get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch('pipeline.tiered_research.checked_request',side_effect=send):
            result,usage=oc.complete('j',artifact,request,ctx,None)
            again,_=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result,value);self.assertEqual(again,value)
        self.assertEqual([s for s,_ in calls],['outline_suffix_completion','outline_suffix_completion_2'])
        self.assertEqual(usage.input_tokens,12);self.assertEqual(usage.output_tokens,23)
        self.assertEqual(qb.review_operation('outline_suffix_completion_2'),('outline','suffix_completion',1))

    def test_partial_suffix_cannot_rewrite_finished_sections_or_make_no_progress(self):
        ctx,value,request,artifact=self.setup_value()
        fixed,key,head,remaining,_=oc.prefix(artifact['content_text'],bp.schema())
        for text in ('{"omissions":[{"research_id":"cut','{"sections":[]}'):
            with self.assertRaises(ContentQualityError):oc.advance(fixed,key,head,remaining,text)
        fixed={'sections':[]};key='sections';head=value['sections'];remaining={'type':'object','properties':{'sections':bp.schema()['properties']['sections']},'required':['sections'],'additionalProperties':False}
        with self.assertRaises(ContentQualityError):oc.advance({},key,head,remaining,json.dumps({'sections':head})[:-1])

    def test_incomplete_provider_output_is_classified_separately_from_article_quality(self):
        from pipeline.content_quality import response_text,ResponseIncompleteError
        from pipeline.autofix import classify_error
        with self.assertRaises(ResponseIncompleteError) as error:response_text(NS(stop_reason='max_tokens'))
        diagnosis=classify_error(error.exception)
        self.assertEqual(diagnosis['reason'],'AI応答の中断・出力上限')
        self.assertFalse(diagnosis['retryable']) # This is suffix completion, not whole-job retry.
