import asyncio
import os
import unittest
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException
from api_server import resume, ResumeRequest

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.job = {'id':'job','tenant_id':'owner','status':'failed','main_keyword':'keyword'}

    def invoke(self, token='Bearer fixture-only'):
        tasks = BackgroundTasks()
        result = asyncio.run(resume(ResumeRequest(job_id='job'), tasks, token))
        return result, tasks

    def test_resume_starts_only_failed_stage(self):
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), \
             patch('api_server.get_job', return_value=self.job), \
             patch('api_server.build_step_plan', return_value=[('outline', None), ('research_validation', None)]), \
             patch('pipeline.db.get_optional_artifact', return_value={'content_text':'{"step":"research_validation"}'}), \
             patch('api_server.req_http_patch_claim', return_value=[self.job]):
            result, tasks=self.invoke()
            self.assertEqual(result.status,'started')
            self.assertEqual(tasks.tasks[0].args, ('job','keyword','research_validation'))

    def test_running_is_idempotent(self):
        self.job['status']='running'
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), patch('api_server.get_job', return_value=self.job):
            result, tasks=self.invoke()
            self.assertEqual(result.status,'running');self.assertFalse(tasks.tasks)

    def test_wrong_owner_is_rejected(self):
        from pipeline.browser_serp import BrowserAuthorizationError
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), patch('api_server.get_job', return_value=self.job), \
             patch('pipeline.browser_serp.authenticate_owner', side_effect=BrowserAuthorizationError('owner mismatch')):
            with self.assertRaises(HTTPException) as exc:self.invoke('Bearer wrong')
            self.assertEqual(exc.exception.status_code,403)

if __name__=='__main__':unittest.main()
