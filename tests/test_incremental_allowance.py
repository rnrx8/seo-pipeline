"""An optional allowance applies only to the authorized job, even in production."""
import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline import quality_budget as q
from pipeline.content_quality import ContentQualityError

class IncrementalAllowanceTests(unittest.TestCase):
 def fixture(self,limit=8):
  calls=[{'reserved_usd':30,'cost_usd':22,'status':'accounted'},
         {'reserved_usd':.3052525,'status':'unknown_cost_reserved'}]
  return {'job_id':'j','limit_usd':q.LIMIT,'calls':calls,'incremental_allowance':{
   'version':'incremental-allowance-v1','start_call_index':len(calls),'limit_usd':limit,
   'historical_calls_sha256':hashlib.sha256(json.dumps(calls,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
   'authorization':'追加8ドルまでで進める（約1,600円）'}}
 def test_historical_costs_and_unknown_holds_are_preserved(self):
  v=self.fixture();before=copy.deepcopy(v)
  with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'unlimited'}):
   self.assertEqual(q.total_remaining(v),8);q.guard_total(v,8)
   with self.assertRaises(ContentQualityError):q.guard_total(v,8.001)
  self.assertEqual(v,before)
 def test_pending_and_unknown_new_requests_consume_the_allowance(self):
  v=self.fixture();v['calls'] += [{'reserved_usd':2,'cost_usd':1,'status':'accounted'},
     {'reserved_usd':3,'status':'pending'},{'reserved_usd':2,'status':'unknown_cost_reserved'}]
  with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'unlimited'}):
   self.assertEqual(q.total_remaining(v),2)
   with self.assertRaises(ContentQualityError):q.guard_total(v,2.01)
 def test_existing_total_cap_remains_stricter(self):
  v=self.fixture();v['total_limit_usd']=23
  with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'bounded'}):
   self.assertAlmostEqual(q.total_remaining(v),.6947475)
   with self.assertRaises(ContentQualityError):q.guard_total(v,1)
 def test_normal_job_without_allowance_has_no_new_stop_condition(self):
  v=self.fixture();v.pop('incremental_allowance')
  with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'unlimited'}):
   self.assertEqual(q.total_remaining(v),float('inf'));q.guard_total(v,100)
 def test_modified_history_or_invalid_approval_is_rejected(self):
  for key,value in [('authorization',''),('start_call_index',-1),('limit_usd',True),('limit_usd',float('nan'))]:
   v=self.fixture();v['incremental_allowance'][key]=value
   with self.subTest(key=key,value=value),self.assertRaises(ContentQualityError):q.incremental_remaining(v)
  v=self.fixture();v['calls'][0]['cost_usd']=0
  with self.assertRaises(ContentQualityError):q.incremental_remaining(v)
 def test_native_all_provider_reservations_reject_before_appending(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder,'QUALITY_BUDGET_MODE':'unlimited'}),q.scope('j','article'):
   v=self.fixture(.00001);path=Path(folder)/(hashlib.sha256(b'j').hexdigest()+'.json');path.write_text(json.dumps(v))
   for reserve,payload in [(q.reserve,{'model':'gpt-6.1-sol','max_output_tokens':100}),
       (q.reserve_claude,{'model':'claude-sonnet-5-5','max_tokens':100}),
       (q.reserve_search,'query')]:
    with self.assertRaises(ContentQualityError):reserve(payload)
    self.assertEqual(json.loads(path.read_text()),v)
