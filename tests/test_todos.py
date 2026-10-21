import tempfile
import unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from server import RiverStore,AppError

class TodoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def add(self,**body):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/todos',{'title':'Synthetic pack bag',**body},'parent'))['todo']
    def finish(self,key):
        self.store.mutate('/api/tasks/'+key+'/report',{},'river')
        self.store.mutate('/api/parent/tasks/'+key+'/confirm',{},'parent')
        self.store.save()
    def listed(self,day=None):
        return [t for t in self.store.view(day)['tasks'] if t['kind']=='todo']
    def test_todos_are_separate_from_school_and_routines_and_carry_forward(self):
        todo=self.add()
        self.assertEqual(todo['plannedMinutes'],0)
        self.assertEqual(len(self.store.state['tasks']),0)
        self.assertNotIn(todo['id'],[c['id'] for c in self.store.day('2026-09-08')['chores']])
        self.now+=timedelta(days=1)
        self.assertEqual(self.listed()[0]['id'],todo['id'])
        self.assertEqual(self.listed()[0]['planDate'],'2026-09-08')
        self.finish(todo['id'])
        self.now+=timedelta(days=1)
        self.assertEqual(self.listed(),[])
        self.assertIsNotNone(self.listed('2026-09-08')[0]['parentConfirmed'])
        self.assertEqual(self.store.view()['schoolClock']['elapsedSeconds'],0)
    def test_future_and_weekend_assignment_dates_and_persistence(self):
        todo=self.add(planDate='2026-09-13')
        self.assertEqual(self.listed(),[])
        with self.assertRaises(AppError): self.store.mutate('/api/tasks/'+todo['id']+'/report',{},'river')
        with self.assertRaises(AppError): self.store.mutate('/api/parent/tasks/'+todo['id']+'/confirm',{},'parent')
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            s=again.view('2026-09-13')
            self.assertFalse(s['afterSchool'])
            self.assertIn(todo['id'],[t['id'] for t in s['tasks']])
        finally:again.db.close()
    def test_parent_only_one_click_completion_and_correction(self):
        todo=self.add();key=todo['id']
        with self.assertRaises(AppError): self.store.mutate('/api/parent/tasks/'+key+'/confirm',{},'parent')
        self.store.mutate('/api/tasks/'+key+'/report',{},'river')
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.store.mutate('/api/parent/tasks/'+key+'/confirm',{},actor)
        with self.assertRaises(AppError):self.store.mutate('/api/tasks/'+key+'/complete',{},'river')
        self.store.mutate('/api/parent/tasks/'+key+'/confirm',{},'parent')
        self.assertEqual(self.store.task(key)['parentConfirmed']['note'],'')
        self.store.mutate('/api/parent/tasks/'+key+'/revoke',{'field':'parentConfirmed','note':'Synthetic correction'},'parent')
        self.assertIsNone(self.store.task(key)['completedAt'])
        self.assertIn('parent_confirmed',[e['action'] for e in self.store.task(key)['history']])
    def test_edits_reset_completion_but_keep_history(self):
        todo=self.add();self.finish(todo['id'])
        self.store.mutate('/api/parent/todos/'+todo['id'],{'title':'Synthetic pack different bag','planDate':'2026-09-09'},'parent')
        self.assertIsNone(self.store.task(todo['id'])['completedAt'])
        self.assertEqual(self.listed(),[])
        self.assertIn('parent_confirmed',[e['action'] for e in self.store.task(todo['id'])['history']])
    def test_reward_option_and_exceptions_use_assigned_date_only(self):
        todo=self.add();key=todo['id']
        self.assertIn('todo:'+key,[i['id'] for i in self.store.view()['rewards']['day']['items']])
        self.assertNotIn('todo:'+key,[i['id'] for i in self.store.view('2026-09-09')['rewards']['day']['items']])
        self.store.mutate('/api/parent/rewards/day',{'date':'2026-09-08','expectedSchoolIds':[],'excusedItems':{'todo:'+key:'Synthetic exception'},'note':'Synthetic review'},'parent')
        self.assertEqual(next(i for i in self.store.view()['rewards']['day']['items'] if i['id']=='todo:'+key)['status'],'excused')
        self.store.mutate('/api/parent/todos/'+key,{'title':todo['title'],'countsForReward':False},'parent')
        self.assertNotIn('todo:'+key,[i['id'] for i in self.store.view()['rewards']['day']['items']])
    def test_bad_input_and_unprivileged_assignments_are_rejected(self):
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.store.mutate('/api/parent/todos',{'title':'Bad'},actor)
        for body in ({'title':''},{'planDate':'2026-09-07'},{'countsForReward':'yes'}):
            with self.assertRaises(AppError):self.add(**body)
