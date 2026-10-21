"""Dated schoolwork access, early credit, and quiz/timer boundaries; synthetic only."""
import copy
from datetime import timedelta
from pathlib import Path
import unittest
import test_mastery as fixtures
from test_ai_review import FakeClient
from server import AppError
from practice import PracticeError, practice_state
from rewards import day_progress
from catchup import reported_in_time, school_done
from ai_review import ReviewWorker, jobs


class WorkAheadTests(unittest.TestCase):
    setUp=fixtures.MasteryReviewTests.setUp
    tearDown=fixtures.MasteryReviewTests.tearDown
    call=fixtures.MasteryReviewTests.call

    def future(self):return (self.now+timedelta(days=1)).date().isoformat()

    def school(self,date,title='Synthetic assigned work'):
        result=self.call('/api/parent/tasks',{'title':title,'subject':'Test','instructions':'Complete the assigned worksheet.',
                                             'planDate':date,'requirements':{'parent':True}},'parent')
        return self.store.task(result['id'])

    def future_quiz(self,publish=True,mode='mastery'):
        body=copy.deepcopy(self.quiz);body.update(planDate=self.future(),reviewMode=mode)
        ident=self.call('/api/assistant/practice/drafts',body,'assistant')['quizIds'][0]
        if publish:self.call('/api/parent/practice/quizzes/'+ident+'/publish',{'approved':True},'parent')
        return next(q for q in self.store.state['practice']['quizzes'] if q['id']==ident)

    def test_browse_separates_today_earlier_and_future_without_changing_clock_date(self):
        past=self.school((self.now-timedelta(days=1)).date().isoformat(),'Earlier worksheet')
        upcoming=self.school(self.future(),'Future worksheet')
        view=self.store.view()
        self.assertIn(self.task['id'],{t['id'] for t in view['schoolwork']['today']})
        self.assertEqual([t['id'] for t in view['schoolwork']['earlier']],[past['id']])
        self.assertEqual([t['id'] for t in view['schoolwork']['upcoming']],[upcoming['id']])
        self.assertNotIn(upcoming['id'],{t['id'] for t in view['tasks']})
        self.assertEqual(view['schoolClock']['date'],self.store.today())

    def test_work_ahead_report_and_parent_confirmation_keep_plan_and_earn_credit_on_assigned_day(self):
        planned=self.future();task=self.school(planned)
        with self.assertRaises(AppError):self.call('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
        self.call('/api/tasks/'+task['id']+'/report')
        self.call('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
        task=self.store.task(task['id'])
        self.assertEqual(task['planDate'],planned)
        self.assertEqual(task['reportedPlanDate'],planned)
        self.assertTrue(task['completedAt'].startswith(self.store.today()))
        self.assertTrue(reported_in_time(self.store,task,planned))
        self.assertTrue(school_done(self.store,task,planned))
        self.assertFalse(day_progress(self.store,planned,True)['complete'])
        self.now+=timedelta(days=1)
        item=next(i for i in day_progress(self.store,planned,True)['items'] if i.get('taskId')==task['id'])
        self.assertEqual(item['status'],'complete')
        self.assertIn(task['id'],{t['id'] for t in self.store.view()['schoolwork']['today']})
        self.assertFalse(day_progress(self.store,planned,True)['complete']) # Daily chores still needed.

    def test_earlier_completed_work_leaves_unfinished_list(self):
        task=self.school((self.now-timedelta(days=1)).date().isoformat())
        self.call('/api/tasks/'+task['id']+'/report')
        self.call('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
        self.assertNotIn(task['id'],{t['id'] for t in self.store.view()['schoolwork']['earlier']})

    def test_early_credit_requires_real_work_ahead_report_and_never_accepts_future_timestamp(self):
        task=self.school(self.future());task['reportedAt']=self.store.stamp()
        self.assertFalse(reported_in_time(self.store,task,self.future()))
        task['reportedPlanDate']=task['planDate']
        self.assertTrue(reported_in_time(self.store,task,self.future()))
        task['reportedAt']=(self.now+timedelta(days=1)).isoformat()
        self.assertFalse(reported_in_time(self.store,task,self.future()))

    def test_future_quiz_is_visible_and_ai_reviews_saved_answer_with_todays_timer(self):
        q=self.future_quiz()
        self.assertIn(q['id'],{q['id'] for q in practice_state(self.store)['quizzes']})
        self.store.ai_review_enabled=True
        self.call('/api/practice/mastery/'+q['id']+'/answer',{'questionId':'q1','answer':'Synthetic explanation.'})
        a=self.store.state['practice']['attempts'][-1]
        path=Path(self.tmp.name)/'review.env';path.write_text('OPENAI_API_KEY=synthetic-test-key\n');path.chmod(0o600)
        fake=FakeClient();worker=ReviewWorker(self.store,path,lambda _:fake)
        worker.step();worker.step()
        self.assertEqual(a['grade'],'correct')
        self.assertEqual(jobs(self.store)[a['id']]['status'],'completed')
        self.assertEqual(q['planDate'],self.future())
        self.assertEqual(self.store.view()['schoolClock']['date'],self.store.today())
        self.assertTrue(self.store.view()['schoolClock']['running'])
        self.assertIsNone(self.store.task(q['taskId'])['reportedAt'])

    def test_unpublished_future_quiz_stays_hidden_and_cannot_start(self):
        q=self.future_quiz(False)
        self.assertNotIn(q['id'],{q['id'] for q in practice_state(self.store)['quizzes']})
        with self.assertRaises(PracticeError):self.call('/api/practice/mastery/'+q['id']+'/answer',{'questionId':'q1','answer':'Synthetic.'})
        published=self.future_quiz();published['published']=False
        self.assertNotIn(published['taskId'],{t['id'] for t in self.store.view()['schoolwork']['upcoming']})
        with self.assertRaises(AppError):self.call('/api/tasks/'+published['taskId']+'/report')

    def test_future_practice_session_uses_current_day_and_time_limit(self):
        q=self.future_quiz(mode='practice')
        result=self.call('/api/practice/sessions/start',{'quizId':q['id']})
        self.assertEqual(result['session']['date'],self.store.today())
        self.assertEqual(q['planDate'],self.future())

    def test_future_mastery_completion_keeps_parent_gate_and_original_plan(self):
        q=self.future_quiz();task=self.store.task(q['taskId'])
        for question in q['questions']:
            self.call('/api/practice/mastery/'+q['id']+'/answer',{'questionId':question['id'],'answer':'Synthetic response.'})
            a=self.store.state['practice']['attempts'][-1]
            self.call('/api/assistant/practice/attempts/'+a['id']+'/review',{'correct':True,'independent':True,'rubricEvidence':'Synthetic verification evidence.','feedback':'Approved.'},'assistant')
        with self.assertRaises(PracticeError):self.call('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
        self.call('/api/tasks/'+task['id']+'/report')
        self.call('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
        task=self.store.task(q['taskId'])
        self.assertIsNotNone(task['completedAt'])
        self.assertEqual(task['planDate'],self.future())
        self.assertTrue(school_done(self.store,task,self.future()))

    def test_work_ahead_cannot_bypass_todays_schoolwork_limit(self):
        q=self.future_quiz()
        self.store.day(self.store.today())['schoolClock'].update(elapsedSeconds=5400,running=False)
        with self.assertRaises(PracticeError):self.call('/api/practice/mastery/'+q['id']+'/answer',{'questionId':'q1','answer':'Synthetic.'})
        self.assertFalse(self.store.state['practice']['attempts'])

    def test_future_routine_chores_remain_date_bound(self):
        chore=self.store.day(self.future())['chores'][0]
        with self.assertRaises(AppError):self.call('/api/tasks/'+chore['id']+'/report')
        with self.assertRaises(AppError):self.call('/api/parent/tasks/'+chore['id']+'/confirm',{},'parent')
