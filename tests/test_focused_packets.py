import copy,importlib.util,json,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'evaluations/tiered-facts/focused_packets.py'
s=importlib.util.spec_from_file_location('focused_packet_prototype',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class FocusedPacketTests(unittest.TestCase):
    def fixture(self):
        sources=[{'url':u,'title':u,'excerpts':[{'source_ref':'s'+str(n),'text':'元の条件 '+u}]} for n,u in enumerate(['https://a.test','https://b.test'],1)]
        qs=[{'id':k,'required':required} for k,required in [('q1',False),('q2',True),('q3',False)]]
        answers=[{'id':'q1','verified':True,'basis':'primary','evidence':[{'url':'https://a.test','quote':'元の条件 https://a.test'}]},
                 {'id':'q2','verified':False,'basis':'unresolved','evidence':[]},
                 {'id':'q3','verified':False,'basis':'unresolved','evidence':[]}]
        return [{'stage':'example','request':{'messages':[{'role':'user','content':json.dumps({'sources':sources,'plan':{'items':qs},'candidate_answers':answers})}]}}]
    def test_unknown_questions_stay_pending_and_sources_are_retained(self):
        bundle=m.build(self.fixture());self.assertEqual(len(bundle['source_store']),2)
        self.assertEqual([i['route'] for i in bundle['lookup']],['required_lookup','optional_resolution'])
        self.assertTrue(all(not i['approved'] for i in bundle['lookup']+bundle['audits']))
        packet=m.audit_payload(bundle)
        self.assertEqual(len(packet['source_documents']),1)
        self.assertEqual(len(packet['available_other_source_catalog']),1)
    def test_source_scope_revision_and_content_are_bound(self):
        b=m.build(self.fixture());p=m.audit_payload(b);page=next(iter(p['source_documents'].values()));ref=page['excerpts'][0]['source_ref']
        self.assertEqual(m.resolve_reference(b,p,'q1',ref)['quote'],'元の条件 https://a.test')
        bad=copy.deepcopy(p);next(iter(bad['source_documents'].values()))['excerpts'][0]['text']='偽の価格'
        with self.assertRaises(ValueError):m.resolve_reference(b,bad,'q1',ref)
        changed=copy.deepcopy(b);next(iter(changed['source_store'].values()))['fetched_at']='changed'
        with self.assertRaises(ValueError):m.resolve_reference(changed,p,'q1',ref)
        with self.assertRaises(StopIteration):m.resolve_reference(b,p,'q2',ref)
