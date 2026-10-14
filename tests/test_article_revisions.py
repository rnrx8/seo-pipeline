import unittest
from unittest.mock import patch
from pipeline import article_revisions as revisions
from pipeline.content_quality import ContentQualityError

class RunRevisionTests(unittest.TestCase):
 def test_feature_is_off_by_default(self):
  with patch.dict('os.environ',{},clear=True),patch.object(revisions,'rpc') as transport:
   with revisions.generation('j'):self.assertIsNone(revisions.RUN.get())
   transport.assert_not_called()
 def test_reads_pin_revisions_and_writes_advance_only_owned_pointers(self):
  token=revisions.RUN.set({'job_id':'j','id':'run','revisions':{'article':'v1'}})
  try:
   revisions.observe('j','article',{'revision_id':'v1'})
   with self.assertRaises(ContentQualityError):revisions.observe('j','article',{'revision_id':'v2'})
   with patch.object(revisions,'rpc',return_value={'revision_id':'v2'}) as rpc:
    revisions.save('j','article',{'content_text':'changed'})
    self.assertEqual(rpc.call_args.args[1]['p_expected_revision'],'v1')
   revisions.observe('j','article',{'revision_id':'v2'})
  finally:revisions.RUN.reset(token)
 def test_deleted_dependency_is_stale(self):
  token=revisions.RUN.set({'job_id':'j','id':'run','revisions':{'outline':'v1'}})
  try:
   with self.assertRaises(ContentQualityError):revisions.observe('j','outline',None)
  finally:revisions.RUN.reset(token)
 def test_malformed_save_receipt_never_falls_back_to_legacy(self):
  token=revisions.RUN.set({'job_id':'j','id':'run','revisions':{}})
  try:
   with patch.object(revisions,'rpc',return_value=None):
    with self.assertRaises(ContentQualityError):revisions.save('j','article',{})
  finally:revisions.RUN.reset(token)
 def test_unfinished_context_records_finish_and_resets(self):
  with patch.dict('os.environ',{'ARTICLE_REVISION_TRACKING':'1'}),patch.object(revisions,'rpc',side_effect=[{'id':'run','inherited_revisions':{}},None]) as rpc:
   with self.assertRaises(ValueError):
    with revisions.generation('j'):raise ValueError('generation stopped')
   self.assertEqual(rpc.call_args.args[0],'finish_article_run');self.assertIsNone(revisions.RUN.get())
 def test_seal_binds_exact_dependency_set(self):
  token=revisions.RUN.set({'job_id':'j','id':'run','revisions':{'article':'v2','outline':'o1'}})
  try:
   with patch.object(revisions,'rpc',return_value={'id':'completion'}) as rpc:
    revisions.seal('j');self.assertEqual(rpc.call_args.args[1]['p_expected_revisions'],{'article':'v2','outline':'o1'})
    self.assertTrue(revisions.RUN.get()['sealed'])
  finally:revisions.RUN.reset(token)
 def test_api_decorator_preserves_arguments_when_enabled(self):
  @revisions.revision_run
  def execute(job_id,keyword,resume_from=None):
   self.assertEqual(revisions.RUN.get()['job_id'],job_id)
   return keyword,resume_from
  with patch.dict('os.environ',{'ARTICLE_REVISION_TRACKING':'1'}),patch.object(revisions,'rpc',side_effect=[{'id':'r','inherited_revisions':{}},None]):
   self.assertEqual(execute('j','keyword','content_audit'),('keyword','content_audit'))
 def test_start_failure_marks_job_failed_without_entering_paid_stages(self):
  with patch.dict('os.environ',{'ARTICLE_REVISION_TRACKING':'1'}),patch.object(revisions,'rpc',side_effect=ValueError('offline')),patch('pipeline.db.update_job_status') as status,patch('pipeline.db.update_job_error'):
   with self.assertRaisesRegex(ValueError,'offline'):
    with revisions.generation('j'):self.fail('must not enter')
   status.assert_called_once_with('j','failed');self.assertIsNone(revisions.RUN.get())
 def test_cleanup_failure_preserves_primary_generation_error(self):
  with patch.dict('os.environ',{'ARTICLE_REVISION_TRACKING':'1'}),patch.object(revisions,'rpc',side_effect=[{'id':'r','inherited_revisions':{}},RuntimeError('cleanup')]):
   with self.assertRaisesRegex(ValueError,'primary'):
    with revisions.generation('j'):raise ValueError('primary')
   self.assertIsNone(revisions.RUN.get())

if __name__=='__main__':unittest.main()
