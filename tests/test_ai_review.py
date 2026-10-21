"""Managed reviewer tests use fake transport and synthetic schoolwork only."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_mastery as fixtures
from ai_review import ReviewWorker, AgentError, AgentsClient, read_config, jobs, public_status, retry
from practice import practice_state, PracticeError

REVIEW={'correct':True,'independent':True,'rubricEvidence':'PRIVATE grading evidence','feedback':'Your explanation meets the criteria.'}

class FakeClient:
    def __init__(self):
        self.calls=[]
        self.session={'id':'sess_synthetic','status':'requires_action','required_actions':[{
            'type':'function_call','turn_id':'turn_synthetic','call_id':'call_synthetic','name':'save_review','arguments':copy.deepcopy(REVIEW)}]}
        self.error=None
    def request(self,method,path,body=None):
        self.calls.append((method,path,copy.deepcopy(body)))
        if self.error:raise self.error
        if method=='POST' and path=='/agents/sessions':return {'id':'sess_synthetic','status':'in_progress'}
        if method=='GET':return copy.deepcopy(self.session)
        return {}

class AIReviewTests(unittest.TestCase):
    setUp=fixtures.MasteryReviewTests.setUp
    tearDown=fixtures.MasteryReviewTests.tearDown
    call=fixtures.MasteryReviewTests.call
    answer=fixtures.MasteryReviewTests.answer
    review=fixtures.MasteryReviewTests.review
    def worker(self,key='synthetic-test-key',limit=30):
        self.store.ai_review_enabled=True
        path=Path(self.tmp.name)/'private.env';path.write_text(f'OPENAI_API_KEY={key}\nRIVER_AI_MAX_REVIEWS_PER_DAY={limit}\n');path.chmod(0o600)
        self.fake=FakeClient();worker=ReviewWorker(self.store,path,lambda key:self.fake)
        self.store.ai_review_worker=worker
        return worker
    def test_disabled_sends_nothing_and_does_not_queue_old_attempts(self):
        a=self.answer(1);self.assertNotIn('aiReviewJobs',self.store.state)
        worker=self.worker();worker.step()
        self.assertEqual(jobs(self.store),{});self.assertFalse(self.fake.calls)
        self.assertEqual(a['grade'],'pending')
    def test_missing_key_preserves_answer_and_waits(self):
        worker=self.worker('');a=self.answer(1)
        with patch.dict(os.environ,{},clear=True):worker.step()
        self.assertEqual(jobs(self.store)[a['id']]['status'],'queued');self.assertFalse(self.fake.calls)
        self.assertFalse(public_status(self.store)['ready'])
    def test_success_runs_function_and_keeps_parent_gate_and_private_evidence(self):
        worker=self.worker();a=self.answer(1,usedHelp=True);worker.step();worker.step()
        self.assertEqual(a['grade'],'correct');self.assertFalse(a['independent'])
        self.assertEqual(jobs(self.store)[a['id']]['status'],'completed')
        self.assertIsNone(self.task['reportedAt']);self.assertIsNone(self.task['completedAt'])
        public=json.dumps(practice_state(self.store))+json.dumps(public_status(self.store))
        for secret in ('PRIVATE grading evidence','PRIVATE RUBRIC','synthetic-test-key','sess_synthetic'):self.assertNotIn(secret,public)
        self.assertEqual(self.fake.calls[0][2]['environment'],{'type':'none'})
        worker.step();self.assertTrue(jobs(self.store)[a['id']]['cleaned'])
        self.assertEqual(sum(c[0]=='DELETE' for c in self.fake.calls),1)
    def test_retry_feedback_reopens_only_failed_question(self):
        worker=self.worker();a=self.answer(1);worker.step()
        self.fake.session['required_actions'][0]['arguments']=dict(REVIEW,correct=False,feedback='Find a supporting detail.',hint='Look at the passage again.')
        worker.step();self.assertEqual(a['grade'],'incorrect')
        b=self.answer(1);self.assertNotEqual(a['id'],b['id']);self.assertEqual(jobs(self.store)[b['id']]['status'],'queued')
    def test_duplicate_tool_call_is_idempotent_even_after_restart(self):
        worker=self.worker();a=self.answer(1);worker.step()
        action=self.fake.session['required_actions'][0]
        first=worker.apply(a['id'],action);history=copy.deepcopy(self.store.state)
        second=worker.apply(a['id'],action);self.assertEqual(first,second);self.assertEqual(history,self.store.state)
        from server import RiverStore
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        worker=self.worker();self.assertEqual(worker.apply(a['id'],action),first)
    def test_manual_review_wins_over_late_agent_and_completed_grade_cannot_be_overwritten(self):
        worker=self.worker();a=self.answer(1);worker.step();self.review(a,False)
        before=copy.deepcopy(self.store.state['practice']);worker.step()
        self.assertEqual(before,self.store.state['practice']);self.assertEqual(a['grade'],'incorrect')
    def test_changed_rubric_discards_result(self):
        worker=self.worker();a=self.answer(1);worker.step();self.quiz['questions'][0]['rubric']='Changed private rubric'
        worker.step();self.assertEqual(a['grade'],'pending');self.assertEqual(jobs(self.store)[a['id']]['status'],'failed')
    def test_restart_resumes_existing_session_without_recreating(self):
        worker=self.worker();a=self.answer(1);worker.step()
        from server import RiverStore
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        worker=self.worker();worker.step()
        self.assertFalse(any(c[1]=='/agents/sessions' for c in self.fake.calls))
        self.assertEqual(jobs(self.store)[a['id']]['status'],'completed')
    def test_interrupted_creation_requires_explicit_retry(self):
        worker=self.worker();a=self.answer(1);jobs(self.store)[a['id']].update(status='creating',tries=1)
        worker.step();self.assertFalse(self.fake.calls);self.assertEqual(jobs(self.store)[a['id']]['status'],'failed')
        with self.assertRaises(PracticeError):retry(self.store,self.id,{'attemptId':a['id']},'assistant')
        retry(self.store,self.id,{'attemptId':a['id']},'river');worker.step()
        self.assertEqual(jobs(self.store)[a['id']]['tries'],2)
    def test_explicit_retry_after_ended_session_starts_new_review(self):
        worker=self.worker();a=self.answer(1);worker.step()
        self.fake.session.update(status='idle',required_actions=[])
        worker.step();self.assertTrue(jobs(self.store)[a['id']]['sessionEnded'])
        retry(self.store,self.id,{'attemptId':a['id']},'river')
        self.assertIsNone(jobs(self.store)[a['id']]['sessionId'])
        worker.step()
        self.assertEqual(jobs(self.store)[a['id']]['tries'],2)
        self.assertEqual(sum(c[1]=='/agents/sessions' for c in self.fake.calls),2)
        self.assertEqual(jobs(self.store)[a['id']]['retiredSessions'],['sess_synthetic'])
    def test_errors_never_approve_or_expose_exception_secrets(self):
        worker=self.worker();a=self.answer(1);self.fake.error=RuntimeError('PRIVATE token and source payload')
        worker.step();self.assertEqual(a['grade'],'pending');self.assertNotIn('PRIVATE',json.dumps(public_status(self.store)))
    def test_daily_limit_blocks_new_spending_but_allows_existing_review_to_finish(self):
        worker=self.worker(limit=1);a=self.answer(1);b=self.answer(2)
        worker.step();worker.step();worker.step()
        self.assertEqual(a['grade'],'correct');self.assertEqual(b['grade'],'pending')
        self.assertEqual(jobs(self.store)[b['id']]['status'],'failed')
        self.assertEqual(sum(c[1]=='/agents/sessions' for c in self.fake.calls),1)
    def test_invalid_assessment_rolls_back_and_is_bounded(self):
        worker=self.worker();a=self.answer(1);worker.step()
        for i in range(3):
            self.fake.session['required_actions'][0].update(call_id=f'bad{i}',arguments={'correct':'yes'})
            worker.step()
        self.assertEqual(self.store.state['practice']['attempts'][-1]['grade'],'pending')
        self.assertEqual(jobs(self.store)[a['id']]['status'],'failed')
    def test_writing_approval_cannot_skip_sentence_assessment(self):
        worker=self.worker();self.quiz['questions'][0]['writingRequirements']={'minQualifiedSentences':8,'requiredCoverage':['Topic'],'expectedSentenceRange':'8–12'}
        a=self.answer(1);worker.step();worker.step()
        self.assertEqual(self.store.state['practice']['attempts'][-1]['grade'],'pending')
        self.assertEqual(jobs(self.store)[a['id']]['failures'],1)
    def test_unknown_tool_cannot_mutate_parent_data(self):
        worker=self.worker();a=self.answer(1);worker.step();self.fake.session['required_actions'][0]['name']='confirm_parent'
        worker.step();self.assertIsNone(self.task['parentConfirmed']);self.assertEqual(a['grade'],'pending')

class SecretConfigTests(unittest.TestCase):
    def test_private_literal_file_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{},clear=True):
            p=Path(tmp)/'review.env';p.write_text('OPENAI_API_KEY="$(do-not-run-this)"\n');p.chmod(0o600)
            self.assertEqual(read_config(p)['key'],'$(do-not-run-this)')
            p.chmod(0o644)
            with self.assertRaises(ValueError):read_config(p)
    def test_blank_private_file_does_not_reuse_inherited_development_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'OPENAI_API_KEY':'unrelated-development-credential'}):
            p=Path(tmp)/'review.env';p.write_text('OPENAI_API_KEY=\n');p.chmod(0o600)
            self.assertEqual(read_config(p)['key'],'')
    def test_transport_is_fixed_to_openai_and_http_errors_are_redacted(self):
        from urllib.error import HTTPError
        client=AgentsClient('synthetic-test-key')
        with self.assertRaises(AgentError):client.request('GET','https://untrusted.example/')
        with patch('ai_review.urlopen',side_effect=HTTPError('https://api.openai.com',401,'PRIVATE',{},None)) as request:
            with self.assertRaises(AgentError) as caught:client.request('GET','/agents/sessions/sess_test')
            self.assertNotIn('PRIVATE',str(caught.exception));self.assertIn('API key',str(caught.exception))
            import ssl
            tls=request.call_args.kwargs['context']
            self.assertTrue(tls.check_hostname)
            self.assertEqual(tls.verify_mode,ssl.CERT_REQUIRED)
