"""Delegated management boundaries; all identities/work are synthetic."""
import copy
from datetime import datetime,timedelta,timezone
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from server import create_server
from practice import PracticeError
from manager_access import revision

ROOT=Path(__file__).resolve().parents[1]
CONFIG={'origin':'https://learning.example.com','googleClientId':'synthetic.apps.googleusercontent.com','parentEmails':['parent@example.com']}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,26,20,tzinfo=timezone.utc)
        self.counter=0
        self.start()
        self.token=self.server.store.manager_token
        self.quiz={'title':'Synthetic planning exercise','subject':'Test','planDate':self.server.store.today(),
            'reviewMode':'mastery','sequential':True,'sourceMaterial':'Synthetic source.',
            'concepts':[{'id':'c','label':'Explain','targetCorrect':1,'targetSessions':1}],
            'questions':[{'id':'q','conceptId':'c','type':'rubric','prompt':'Explain one idea.','rubric':'One relevant explanation.','required':True}]}

    def start(self,enabled=True,cloud=True):
        with patch.dict(os.environ,{'UNSTUCKERY_MANAGER_ENABLED':'1' if enabled else '0','UNSTUCKERY_PLANNER_ENABLED':'1'}):
            self.server=create_server(self.tmp.name,port=0,now=lambda:self.now,cloud_config=CONFIG if cloud else None)
        self.cloud=cloud
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def stop(self):
        self.server.shutdown();self.thread.join(timeout=3);self.server.server_close();self.server.store.db.close()

    def tearDown(self):
        self.stop();self.tmp.cleanup()

    def body(self,**fields):
        self.counter+=1
        return dict(requestId=f'synthetic-{self.counter}',reason='Requested synthetic adjustment.',**fields)

    def call(self,path,body=None,token=True,method=None,origin=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Host':'learning.example.com' if self.cloud else f'127.0.0.1:{self.server.server_port}'}
        if token:headers['Authorization']='Bearer '+(self.token if token is True else token)
        if origin:headers['Origin']=origin
        if body is not None:headers['Content-Type']='application/json'
        conn.request(method or ('GET' if body is None else 'POST'),path,None if body is None else json.dumps(body),headers)
        r=conn.getresponse();status=r.status;raw=r.read();conn.close()
        self.assertNotIn(self.token.encode(),raw)
        return status,json.loads(raw)

    def draft(self,body=None):
        status,result=self.call('/api/manager/practice/drafts',body or self.body(**self.quiz))
        self.assertEqual(status,200,result)
        return result['quizId']

    def publish(self,ident):
        status,result=self.call(f'/api/manager/practice/quizzes/{ident}/publish',self.body(expectedRevision=1,plannedMinutes=20,instructions='Extra practice — complete in this app.'))
        self.assertEqual(status,200,result)
        return result['taskId']

    def state(self):
        code,s=self.call('/api/manager/state');self.assertEqual(code,200,s);return s

    def test_private_opt_in_credential_revocation_and_rotation(self):
        p=Path(self.tmp.name)/'.manager-token'
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertNotEqual(self.token,self.server.store.assistant_token)
        self.assertNotEqual(self.token,self.server.store.planner_token)
        self.state();self.stop();self.start(enabled=False)
        self.assertIsNone(self.server.store.manager_token)
        self.assertEqual(self.call('/api/manager/state')[0],401)
        self.stop();p.unlink();self.start()
        self.assertNotEqual(self.token,self.server.store.manager_token)
        self.assertEqual(self.call('/api/manager/state')[0],401)
        self.token=self.server.store.manager_token;self.state()

    def test_route_allowlist_does_not_grant_parent_student_or_grading_powers(self):
        for path in ('/api/parent/accounts','/api/parent/practice','/api/assistant/state','/api/state',
                     '/api/planner/state','/api/auth','/api/browser-memory','/api/photos/synthetic'):
            self.assertEqual(self.call(path)[0],403,path)
        for path in ('/api/auth/google','/api/parent/rewards/award','/api/tasks/synthetic/complete',
                     '/api/tasks/synthetic/report','/api/parent/braces/confirm','/api/school-clock/start',
                     '/api/practice/mastery/synthetic/answer','/api/assistant/practice/attempts/synthetic/review'):
            self.assertEqual(self.call(path,{'actor':'parent','parent':True})[0],403,path)
        self.assertEqual(self.call('/api/manager/state',token=False)[0],401)
        self.assertEqual(self.call('/api/manager/state',token=self.server.store.assistant_token)[0],401)
        self.assertEqual(self.call('/api/manager/state',token=self.server.store.planner_token)[0],401)
        self.assertEqual(self.call('/api/manager/state',origin='https://other.example.com')[0],403)
        self.assertEqual(self.call('/api/manager/practice/drafts',self.body(**self.quiz),method='PUT')[0],403)
        for actor in ('river','parent','assistant','planner'):
            with self.assertRaises(PracticeError):
                self.server.store.transaction(lambda:self.server.store.mutate('/api/manager/practice/drafts',self.body(**self.quiz),actor))

    def test_durable_idempotency_audit_and_exercise_ownership(self):
        body=self.body(**self.quiz);ident=self.draft(body)
        self.assertEqual(self.draft(body),ident)
        self.assertEqual(self.call('/api/manager/practice/drafts',dict(body,title='Changed'))[0],409)
        task_id=self.publish(ident)
        self.stop();self.start()
        self.assertEqual(self.draft(body),ident)
        s=self.server.store
        task=s.task(task_id)
        self.assertEqual(task['history'][0]['actor'],'manager')
        self.assertTrue(task['requirements']['parent'])
        self.assertIsNone(task['parentConfirmed']);self.assertIsNone(task['completedAt'])
        self.assertFalse(s.state['rewards']['days'][s.today()].get('reviewedAt'))
        events=self.call('/api/manager/audit')[1]['events']
        self.assertEqual(len(events),2);self.assertTrue(all(e['actor']=='manager' for e in events))
        self.assertTrue(all(e['credentialId']==s.manager_key_id for e in events))
        self.assertEqual(self.call(f'/api/manager/practice/drafts/{ident}',self.body(expectedRevision=1,**self.quiz))[0],409)
        older=s.transaction(lambda:s.mutate('/api/assistant/practice/drafts',self.quiz,'assistant'))['quizIds'][0]
        code,result=self.call(f'/api/manager/practice/drafts/{older}',self.body(expectedRevision=1,**dict(self.quiz,title='Revised draft')))
        self.assertEqual(code,200,result);self.assertEqual(result['revision'],2)
        self.assertEqual(self.call(f'/api/planner/practice/drafts/{older}',dict(self.quiz,expectedRevision=2),token=s.planner_token)[0],403)

    def test_task_adjustments_preserve_answers_and_reject_stale_or_sensitive_fields(self):
        ident=self.draft();task_id=self.publish(ident);s=self.server.store
        s.transaction(lambda:s.mutate('/api/practice/mastery/'+ident+'/answer',{'questionId':'q','answer':'Synthetic response.'},'river'))
        attempts=copy.deepcopy(s.state['practice']['attempts']);questions=copy.deepcopy(s.state['practice']['quizzes'][0]['questions'])
        task=next(t for t in self.state()['tasks'] if t['id']==task_id)
        body=self.body(expectedRevision=task['managerRevision'],changes={'instructions':'Use one small next step.','planDate':'2026-10-27'})
        code,result=self.call('/api/manager/tasks/'+task_id,body);self.assertEqual(code,200,result)
        self.assertEqual(self.call('/api/manager/tasks/'+task_id,body)[0],200)
        self.assertEqual(self.call('/api/manager/tasks/'+task_id,self.body(expectedRevision=task['managerRevision'],changes={'title':'Changed'}))[0],409)
        task=next(t for t in self.state()['tasks'] if t['id']==task_id)
        for key in ('completedAt','parentConfirmed','scope','requirements','grade'):
            self.assertEqual(self.call('/api/manager/tasks/'+task_id,self.body(expectedRevision=task['managerRevision'],changes={key:'forged'}))[0],400)
        self.assertEqual(s.state['practice']['attempts'],attempts)
        self.assertEqual(s.state['practice']['quizzes'][0]['questions'],questions)
        self.assertEqual(s.state['practice']['quizzes'][0]['planDate'],'2026-10-27')
        self.assertIsNone(s.task(task_id)['completedAt'])
        self.assertFalse(s.state['rewards']['days']['2026-10-27'].get('reviewedAt'))

    def test_timer_correction_once_with_manager_audit_without_touching_braces(self):
        s=self.server.store
        s.transaction(lambda:s.mutate('/api/school-clock/start',{'kind':'work'},'river'))
        self.now+=timedelta(minutes=20)
        braces=copy.deepcopy(s.day(s.today())['braces'])
        clock=self.state()['schoolClock']
        body=self.body(expectedRevision=clock['managerRevision'],date=s.today(),removeMinutes=10)
        code,result=self.call('/api/manager/school-clock/correct',body);self.assertEqual(code,200,result)
        self.assertEqual(result['schoolClock']['elapsedSeconds'],600)
        self.assertFalse(result['schoolClock']['running'])
        self.assertEqual(result['schoolClock']['history'][-1]['actor'],'manager')
        self.assertEqual(self.call('/api/manager/school-clock/correct',body)[0],200)
        self.assertEqual(self.state()['schoolClock']['elapsedSeconds'],600)
        self.assertEqual(self.call('/api/manager/school-clock/correct',self.body(expectedRevision=clock['managerRevision'],removeMinutes=1))[0],409)
        self.assertEqual(s.day(s.today())['braces'],braces)
        current=self.state()['schoolClock']['managerRevision']
        self.assertEqual(self.call('/api/manager/school-clock/correct',self.body(expectedRevision=current,removeMinutes=-1))[0],400)

    def test_failed_review_retry_keeps_answer_grade_and_decision(self):
        ident=self.draft();self.publish(ident);s=self.server.store;s.ai_review_enabled=True
        s.transaction(lambda:s.mutate('/api/practice/mastery/'+ident+'/answer',{'questionId':'q','answer':'Synthetic response.'},'river'))
        a=s.state['practice']['attempts'][-1];job=s.state['aiReviewJobs'][a['id']]
        s.transaction(lambda:job.update(status='failed',tries=3,sessionEnded=True,sessionId='synthetic-session',
            reviewMode='decisions',decisionReview={'correct':False},reviewIssue='Synthetic provider issue.'))
        attempt=copy.deepcopy(a)
        info=self.call('/api/manager/reviews')[1]['jobs'][a['id']]
        self.assertEqual(info['reviewIssue'],'Synthetic provider issue.')
        self.assertNotIn('sessionId',info)
        body=self.body(expectedRevision=info['managerRevision'])
        code,result=self.call('/api/manager/reviews/'+a['id']+'/retry',body);self.assertEqual(code,200,result)
        self.assertEqual(a,attempt);self.assertEqual(job['decisionReview'],{'correct':False})
        self.assertEqual(job['tries'],3);self.assertEqual(job['status'],'queued');self.assertIsNone(job['sessionId'])
        self.assertEqual(self.call('/api/manager/reviews/'+a['id']+'/retry',body)[0],200)
        fresh=self.call('/api/manager/reviews')[1]['jobs'][a['id']]['managerRevision']
        self.assertEqual(self.call('/api/manager/reviews/'+a['id']+'/retry',self.body(expectedRevision=fresh))[0],409)

    def test_plan_budget_creation_validation_and_completion_lock(self):
        state=self.state()
        fields={'title':'Verified synthetic task','subject':'Test','scope':'assignment','instructions':'Follow the teacher directions.',
            'sourceNote':'Synthetic teacher requirement checked today; still needs a paragraph.','plannedMinutes':75,'planDate':state['today']}
        code,result=self.call('/api/manager/tasks',self.body(expectedPlanRevision=state['planRevision'],**fields));self.assertEqual(code,200,result)
        task_id=result['taskId'];self.assertEqual(self.server.store.task(task_id)['history'][0]['actor'],'manager')
        ident=self.draft()
        self.assertEqual(self.call(f'/api/manager/practice/quizzes/{ident}/publish',self.body(expectedRevision=1,plannedMinutes=20,instructions='Practice.'))[0],409)
        self.assertEqual(self.call('/api/manager/tasks',self.body(expectedPlanRevision=state['planRevision'],**fields))[0],409)
        self.assertEqual(self.call('/api/manager/tasks',self.body(expectedPlanRevision=self.state()['planRevision'],**dict(fields,scope='practice')))[0],400)
        s=self.server.store;s.transaction(lambda:s.task(task_id).update(completedAt=s.stamp(),parentConfirmed={'actor':'parent'}))
        task=next(t for t in self.state()['tasks'] if t['id']==task_id)
        self.assertEqual(self.call('/api/manager/tasks/'+task_id,self.body(expectedRevision=task['managerRevision'],changes={'title':'Change finished work'}))[0],409)

    def test_cli_manager_reads_and_publishes_without_exposing_key(self):
        self.stop();self.start(cloud=False)
        source=Path(self.tmp.name)/'synthetic.json';source.write_text(json.dumps(self.body(**self.quiz)))
        command=[sys.executable,str(ROOT/'assistant.py'),'--manager','--url',f'http://127.0.0.1:{self.server.server_port}','--data-dir',self.tmp.name]
        result=subprocess.run(command+['practice-draft','--json-file',str(source)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);ident=json.loads(result.stdout)['quizId']
        source.write_text(json.dumps(self.body(expectedRevision=1,plannedMinutes=20,instructions='Practice in the app.')))
        result=subprocess.run(command+['practice-publish',ident,'--json-file',str(source)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['published'])
        self.assertNotIn(self.token,result.stdout+result.stderr)
        denied=subprocess.run(command+['practice-review','synthetic','--json-file',str(source)],capture_output=True,text=True)
        self.assertNotEqual(denied.returncode,0)

    def test_access_check_is_audited_once_without_changing_work(self):
        s=self.server.store
        before={k:copy.deepcopy(s.state.get(k)) for k in ('practice','tasks','days','rewards','webAccounts')}
        body=self.body()
        code,result=self.call('/api/manager/check',body)
        self.assertEqual(code,200,result);self.assertEqual(result['actor'],'manager')
        self.assertEqual(self.call('/api/manager/check',body)[0],200)
        self.assertEqual(len(self.call('/api/manager/audit')[1]['events']),1)
        self.assertEqual({k:s.state.get(k) for k in before},before)
