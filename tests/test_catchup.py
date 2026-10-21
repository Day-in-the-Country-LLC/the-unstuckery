"""Saturday schoolwork credit, using only temporary synthetic records."""
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from server import RiverStore, AppError
from rewards import reward_state


class SaturdayCatchupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,11,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        for day in range(7,12):
            d=f'2026-09-{day:02d}'
            self.now=self.now.replace(day=day)
            self.store.mutate('/api/parent/rewards/day',{'date':d,'expectedSchoolIds':[],'note':'Synthetic plan'},'parent')
            for task in self.store.day(d)['chores']:
                self.store.mutate('/api/tasks/'+task['id']+'/report',{},'river')
                self.store.mutate('/api/parent/tasks/'+task['id']+'/confirm',{},'parent')
            self.store.mutate('/api/parent/braces/confirm',{'date':d},'parent')
        self.now=self.now.replace(day=11)
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def plan(self,**extra):
        return self.store.create_task({'title':'Synthetic weekday assignment','subject':'Test','instructions':'Finish assigned work','planDate':'2026-09-07','requirements':{},**extra})
    def enable(self,enabled=True):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/rewards/catchup',{'enabled':enabled},'parent'))
    def week(self):
        return reward_state(self.store,'2026-09-11')['week']
    def report(self,t):self.store.mutate('/api/tasks/'+t['id']+'/report',{},'river')
    def finish(self,t):self.store.mutate('/api/tasks/'+t['id']+'/complete',{},'river')
    def test_opt_in_saturday_completion_keeps_original_plan_and_earns_week(self):
        task=self.plan();self.now=self.now.replace(day=12,hour=23,minute=59,second=59)
        self.report(task);self.finish(task)
        self.assertFalse(self.week()['eligible'])
        self.enable()
        week=self.week()
        self.assertTrue(week['eligible'])
        self.assertEqual(week['catchup']['deadlineDate'],'2026-09-12')
        self.assertEqual(self.store.task(task['id'])['planDate'],'2026-09-07')
        self.assertEqual(week['completedDays'],5)
        self.assertFalse(week['days'][5]['qualifying'])
        self.assertTrue(all('chore' != i['kind'] and 'todo' != i['kind'] for i in week['catchup']['items']))
        self.now+=timedelta(seconds=1)
        self.assertTrue(self.week()['eligible'])
        self.assertFalse(reward_state(self.store,'2026-09-14')['week']['catchup']['enabled'])
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:self.assertTrue(reward_state(again,'2026-09-11')['week']['catchup']['enabled'])
        finally:again.db.close()
    def test_sunday_work_cannot_earn_previous_week(self):
        task=self.plan();self.enable();self.now=self.now.replace(day=13,hour=0,minute=0,second=0)
        self.report(task);self.finish(task)
        self.assertFalse(self.week()['eligible'])
        self.assertTrue(self.week()['catchup']['missedDeadline'])
        with self.assertRaises(AppError):self.store.mutate('/api/parent/rewards/award',{'date':'2026-09-11'},'parent')
    def test_later_parent_review_can_confirm_saturday_work(self):
        task=self.plan(requirements={'parent':True});self.enable();self.now=self.now.replace(day=12)
        self.report(task)
        self.assertFalse(self.week()['eligible'])
        self.now=self.now.replace(day=13)
        self.store.mutate('/api/parent/tasks/'+task['id']+'/confirm',{},'parent');self.finish(task)
        self.assertTrue(self.week()['eligible'])
        self.assertFalse(self.week()['catchup']['missedDeadline'])
    def test_all_weekday_schoolwork_is_required_even_if_not_selected_in_day_checklist(self):
        first=self.plan();second=self.plan(title='Another assigned item')
        self.store.mutate('/api/parent/rewards/day',{'date':'2026-09-07','expectedSchoolIds':[first['id']],'note':'Synthetic selection'},'parent')
        self.enable();self.now=self.now.replace(day=12)
        self.report(first);self.finish(first)
        self.assertFalse(self.week()['eligible'])
        self.assertIn(second['id'],[i['taskId'] for i in self.week()['catchup']['items']])
        self.report(second);self.finish(second)
        self.assertTrue(self.week()['eligible'])
    def test_parent_only_and_no_changes_to_todos_chores_or_school_budget(self):
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.store.mutate('/api/parent/rewards/catchup',{'enabled':True},actor)
        for value in ('yes',1,None):
            with self.assertRaises(AppError):self.store.mutate('/api/parent/rewards/catchup',{'enabled':value},'parent')
        todo=self.store.mutate('/api/parent/todos',{'title':'Synthetic to-do','planDate':'2026-09-11'},'parent')['todo']
        self.enable();self.now=self.now.replace(day=12)
        view=self.store.view()
        self.assertNotIn(todo['id'],[i['taskId'] for i in self.week()['catchup']['items']])
        self.assertTrue(all(t['planDate']=='2026-09-12' for t in view['tasks'] if t['kind']=='chore'))
        self.assertEqual(view['schoolMinutes'],90)
        self.assertEqual(view['schoolClock']['elapsedSeconds'],0)
        with self.assertRaises(AppError):self.store.mutate('/api/tasks/2026-09-07-petey/report',{},'river')
    def test_redeem_rechecks_added_and_corrected_schoolwork(self):
        task=self.plan();self.enable();self.now=self.now.replace(day=12)
        self.report(task);self.finish(task)
        award=self.store.mutate('/api/parent/rewards/award',{'date':'2026-09-11'},'parent')['award']
        self.store.mutate('/api/rewards/request',{'awardId':award['id'],'prizeId':'ice-cream'},'river')
        self.store.mutate('/api/tasks/'+task['id']+'/correct',{'note':'Synthetic reopened work'},'river')
        with self.assertRaises(AppError):self.store.mutate('/api/parent/rewards/redeem',{'awardId':award['id'],'requestRevision':1},'parent')

    def quiz(self):
        from test_practice import PracticeBehaviorTests
        body=PracticeBehaviorTests().body(count=1,target=1,sessions=1)
        quiz_id=self.store.mutate('/api/parent/practice/quizzes',body,'parent')['quizIds'][0]
        self.store.mutate('/api/parent/practice/quizzes/'+quiz_id+'/publish',{'reviewed':True},'parent')
        return next(t for t in self.store.state['tasks'] if t.get('practiceQuizId')==quiz_id)
    def answer_quiz(self,task):
        session=self.store.mutate('/api/practice/sessions/start',{'quizId':task['practiceQuizId']},'river')['session']
        self.store.mutate('/api/practice/sessions/'+session['id']+'/answer',{'questionId':'q1','answer':'2','usedHelp':False},'river')
    def test_assigned_quiz_completed_saturday_counts_as_schoolwork(self):
        task=self.quiz();self.enable();self.now=self.now.replace(day=12)
        self.answer_quiz(task);self.report(task);self.finish(task)
        self.assertTrue(self.week()['eligible'])
        self.assertIn(task['id'],[i['taskId'] for i in self.week()['catchup']['items']])
    def test_early_ready_report_does_not_allow_sunday_quiz_answers(self):
        task=self.quiz();self.enable();self.now=self.now.replace(day=12)
        self.report(task)
        self.now=self.now.replace(day=13)
        self.answer_quiz(task);self.finish(task)
        self.assertIsNotNone(task['completedAt'])
        self.assertFalse(self.week()['eligible'])
        self.assertTrue(self.week()['catchup']['missedDeadline'])
