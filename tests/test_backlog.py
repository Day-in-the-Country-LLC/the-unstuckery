"""Unscheduled activity storage is separate from assigned schoolwork and prizes."""
import copy,json,tempfile,unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from server import RiverStore,AppError
from backlog import view

class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=datetime(2026,9,10,15,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        self.body={'title':'Synthetic fraction practice','subject':'Math','estimatedMinutes':15,'instructions':'Synthetic fixture only.','materialsUrl':'https://drive.google.com/file/d/synthetic/view'}
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def call(self,path,body=None,actor='parent'):return self.store.transaction(lambda:self.store.mutate(path,body or {},actor))
    def add(self,actor='parent'):return self.call('/api/'+actor+'/backlog',self.body,actor)['itemId']
    def item(self):return view(self.store,'parent')['items'][0]
    def test_unscheduled_ideas_do_not_change_today_timers_rewards_or_public_state(self):
        before=copy.deepcopy(self.store.view());self.add('assistant')
        self.assertEqual(before,self.store.view());self.assertEqual(self.store.state['tasks'],[])
        item=self.item();self.assertNotIn('planDate',item);self.assertEqual(item['status'],'available')
        self.assertNotIn('Synthetic fraction practice',json.dumps(self.store.view()))
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        self.assertEqual(self.item()['title'],self.body['title'])
    def test_parent_schedule_creates_one_normal_school_task_on_selected_day(self):
        ident=self.add('assistant');route='/api/parent/backlog/'+ident+'/schedule'
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.call(route,{'expectedRevision':1},actor)
        with self.assertRaises(AppError):self.call('/api/assistant/backlog/'+ident+'/schedule',{'expectedRevision':1},'assistant')
        result=self.call(route,{'date':'2026-09-11','expectedRevision':1})
        self.call(route,{'date':'2026-09-11','expectedRevision':1})
        self.assertEqual(len(self.store.state['tasks']),1)
        task=self.store.task(result['taskId'])
        self.assertEqual(task['planDate'],'2026-09-11');self.assertEqual(task['sourceUrl'],self.body['materialsUrl'])
        self.assertEqual(task['requirements'],{'parent':True,'understanding':False,'submission':False})
        self.assertFalse(any(t['id']==task['id'] for t in self.store.view()['tasks']))
        self.now+=timedelta(days=1)
        self.assertTrue(any(t['id']==task['id'] for t in self.store.view()['tasks']))
        self.call('/api/tasks/'+task['id']+'/report',{},'river');self.call('/api/parent/tasks/'+task['id']+'/confirm')
        self.assertIsNotNone(task['completedAt']);self.assertEqual(self.item()['status'],'scheduled')
    def test_archive_restore_and_revision_guards_preserve_history_without_assignment(self):
        ident=self.add();base='/api/parent/backlog/'+ident
        self.call(base+'/archive',{'expectedRevision':1});self.assertEqual(self.item()['status'],'archived')
        with self.assertRaises(AppError):self.call(base+'/schedule',{'expectedRevision':2})
        self.call(base+'/restore',{'expectedRevision':2})
        with self.assertRaises(AppError):self.call(base,dict(self.body,expectedRevision=1))
        self.call(base,dict(self.body,title='Edited idea',expectedRevision=3))
        self.assertEqual(len(self.item()['history']),4);self.assertEqual(self.store.state['tasks'],[])
        with self.assertRaises(AppError):self.call('/api/assistant/backlog/'+ident,dict(self.body,expectedRevision=4),'assistant')
    def test_invalid_input_and_past_dates_are_rejected(self):
        for changes in ({'title':''},{'estimatedMinutes':True},{'estimatedMinutes':91},{'materialsUrl':'javascript:alert(1)'}):
            with self.assertRaises(AppError):self.call('/api/parent/backlog',dict(self.body,**changes))
        ident=self.add()
        with self.assertRaises(AppError):self.call('/api/parent/backlog/'+ident+'/schedule',{'date':'2026-09-09','expectedRevision':1})
        self.assertEqual(self.store.state['tasks'],[])
