"""Added routines use isolated data and never create real chore confirmations."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from server import RiverStore, AppError

class RoutineChoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
    def tearDown(self):
        self.store.db.close(); self.tmp.cleanup()
    def add(self,**body):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/routine-chores',{'title':'Synthetic tidy desk',**body},'parent'))['chore']
    def chores(self,day): return [t for t in self.store.view(day)['tasks'] if t['kind']=='chore']

    def test_repeats_today_and_future_without_rewriting_past_or_duplicating(self):
        before=copy.deepcopy(self.chores('2026-09-07'))
        self.chores('2026-09-09') # A projected future day may already be stored.
        added=self.add()
        self.assertEqual(len(self.chores('2026-09-08')),4)
        self.assertEqual(len(self.chores('2026-09-09')),4)
        self.assertEqual(len(self.chores('2026-09-09')),4)
        self.assertEqual(self.chores('2026-09-07'),before)
        self.assertEqual(self.chores('2026-09-09')[-1]['id'],'2026-09-09-'+added['id'])
        self.assertEqual(added['createdBy'],'parent')
        self.assertEqual(added['history'][0]['action'],'routine_chore_created')
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            self.assertEqual(len([t for t in again.view('2026-09-10')['tasks'] if t['kind']=='chore']),4)
        finally: again.db.close()

    def test_future_start_and_after_school_setting_are_respected(self):
        self.add(startDate='2026-09-10')
        self.assertEqual(len(self.chores('2026-09-09')),3)
        self.assertEqual(len(self.chores('2026-09-10')),4)
        self.assertEqual(self.chores('2026-09-12'),[])
        self.store.mutate('/api/parent/day',{'date':'2026-09-12','afterSchool':True},'parent')
        self.assertEqual(len(self.chores('2026-09-12')),4)

    def test_added_chore_uses_parent_only_no_note_confirmation(self):
        chore=self.add()
        ident='2026-09-08-'+chore['id']
        self.store.mutate('/api/tasks/'+ident+'/report',{'note':'Synthetic ready'},'river')
        self.assertIsNone(self.store.task(ident)['completedAt'])
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):
                self.store.mutate('/api/parent/tasks/'+ident+'/confirm',{},actor)
        self.store.mutate('/api/parent/tasks/'+ident+'/confirm',{},'parent')
        self.assertIsNotNone(self.store.task(ident)['completedAt'])
        self.assertEqual(self.store.task(ident)['parentConfirmed']['note'],'')
        self.now+=timedelta(days=1)
        self.assertIsNone(self.chores('2026-09-09')[-1]['completedAt'])

    def test_new_chore_is_a_reward_expectation_and_can_be_excused(self):
        chore=self.add()
        self.assertIn('chore:'+chore['id'],[i['id'] for i in self.store.view()['rewards']['day']['items']])
        self.store.mutate('/api/parent/rewards/day',{'date':'2026-09-08','expectedSchoolIds':[], 'excusedItems':{'chore:'+chore['id']:'Synthetic exception'},'note':'Synthetic review'},'parent')
        item=next(i for i in self.store.view()['rewards']['day']['items'] if i['id']=='chore:'+chore['id'])
        self.assertEqual(item['status'],'excused')
        self.assertIsNone(self.store.task('2026-09-08-'+chore['id'])['completedAt'])

    def test_invalid_duplicate_and_unprivileged_adds_are_rejected(self):
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):
                self.store.mutate('/api/parent/routine-chores',{'title':'Synthetic'},actor)
        for body in ({'title':''},{'title':[]},{'startDate':'2026-09-07'},{'startDate':'bad'}):
            with self.assertRaises(AppError): self.add(**body)
        self.add()
        with self.assertRaises(AppError): self.add(title=' SYNTHETIC TIDY DESK ')
        self.assertEqual(len(self.store.state['routineChores']),1)
