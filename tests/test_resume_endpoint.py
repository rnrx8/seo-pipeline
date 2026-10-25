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

    def test_outline_failure_returns_to_unfinished_research_without_replaying_discovery(self):
        saved = {'pipeline_failure': {'content_text':'{"step":"outline"}'},
                 'search_intent': {'meta':{'reader_needs_version':'v1'}},
                 'research_matrix': {'content_text':'{"valid":false,"status":"running","items":[]}'}}
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), \
             patch('api_server.get_job', return_value=self.job), \
             patch('api_server.build_step_plan', return_value=[('serp',None),('fact_sheet',None),('research_completeness',None),('outline',None)]), \
             patch('pipeline.db.get_optional_artifact', side_effect=lambda job,step:saved.get(step)), \
             patch('api_server.req_http_patch_claim', return_value=[self.job]):
            result,tasks=self.invoke()
        self.assertEqual(result.status,'started')
        self.assertEqual(tasks.tasks[0].args,('job','keyword','research_completeness'))

    def test_stale_research_returns_to_verification_and_current_research_keeps_stage(self):
        from pipeline.step_plan import research_resume_stage
        from pipeline.content_quality import ContentQualityError
        saved={'search_intent':{'meta':{'reader_needs_version':'v1'}},
               'research_matrix':{'content_text':'{"valid":true}'}}
        keys=['research_completeness','outline','article']
        load=lambda job,step:saved.get(step)
        with patch('pipeline.research_requirements._require_matrix_strict',side_effect=ContentQualityError('stale')):
            self.assertEqual(research_resume_stage('job','article',keys,load),'research_completeness')
        with patch('pipeline.research_requirements._require_matrix_strict',return_value={}) as strict:
            self.assertEqual(research_resume_stage('job','article',keys,load),'article')
            strict.assert_called_once_with('job')
        with patch('pipeline.research_requirements._require_matrix_strict',side_effect=RuntimeError('transport')):
            with self.assertRaisesRegex(RuntimeError,'transport'):
                research_resume_stage('job','article',keys,load)

    def test_resume_still_claims_job_once_before_scheduling_prerequisite(self):
        saved={'pipeline_failure':{'content_text':'{"step":"outline"}'},
               'search_intent':{'meta':{'reader_needs_version':'v1'}}}
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), \
             patch('api_server.get_job', side_effect=[self.job,{**self.job,'status':'running'}]), \
             patch('api_server.build_step_plan', return_value=[('research_completeness',None),('outline',None)]), \
             patch('pipeline.db.get_optional_artifact',side_effect=lambda job,step:saved.get(step)), \
             patch('api_server.req_http_patch_claim',return_value=[]) as claim:
            result,tasks=self.invoke()
        self.assertEqual(result.status,'running');self.assertFalse(tasks.tasks)
        claim.assert_called_once()

    def test_wrong_owner_is_rejected(self):
        from pipeline.browser_serp import BrowserAuthorizationError
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), patch('api_server.get_job', return_value=self.job), \
             patch('pipeline.browser_serp.authenticate_owner', side_effect=BrowserAuthorizationError('owner mismatch')):
            with self.assertRaises(HTTPException) as exc:self.invoke('Bearer wrong')
            self.assertEqual(exc.exception.status_code,403)

if __name__=='__main__':unittest.main()
