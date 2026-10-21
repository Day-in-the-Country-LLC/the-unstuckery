import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from server import RiverStore, AppError

class ChoreDaysTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
    def tearDown(self):
        self.store.db.close(); self.tmp.cleanup()
    def add(self,days):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/routine-chores',{'title':'Synthetic bath/shower','days':days},'parent'))['chore']
    def change(self,key,days,effectiveDate='2026-09-08'):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/routine-chores/'+key+'/schedule',{'days':days,'effectiveDate':effectiveDate},'parent'))
    def ids(self,day):
        return [t['id'].removeprefix(day+'-') for t in self.store.view(day)['tasks'] if t['kind']=='chore' and t['routineScheduled']]

    def test_shower_appears_sunday_tuesday_thursday_including_no_school_sunday(self):
        key=self.add([6,1,3])['id']
        for day,expected in [('2026-09-08',True),('2026-09-09',False),('2026-09-10',True),('2026-09-11',False),('2026-09-12',False),('2026-09-13',True),('2026-09-14',False)]:
            self.assertEqual(key in self.ids(day),expected)
            items=self.store.view(day)['rewards']['day']['items']
            self.assertEqual('chore:'+key in [i['id'] for i in items],expected)
        self.assertFalse(self.store.view('2026-09-13')['afterSchool'])
        self.assertNotIn('dogs',self.ids('2026-09-13'))

    def test_every_day_and_legacy_after_school_are_distinct(self):
        self.change('dogs',list(range(7)))
        self.store.mutate('/api/parent/day',{'date':'2026-09-12','afterSchool':False},'parent')
        self.assertEqual(self.ids('2026-09-12'),['dogs'])
        self.change('dogs',None,'2026-09-12')
        self.assertEqual(self.ids('2026-09-12'),[])

    def test_future_changes_preserve_past_and_update_preprojected_days(self):
        key=self.add(list(range(7)))['id']
        original=copy.deepcopy(self.store.view('2026-09-08')['rewards']['day']['items'])
        self.ids('2026-09-09')
        self.change(key,[6,1,3],'2026-09-09')
        self.assertNotIn(key,self.ids('2026-09-09'))
        self.assertEqual(self.store.view('2026-09-08')['rewards']['day']['items'],original)
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            sunday=again.view('2026-09-13')
            self.assertIn(key,[t['id'].removeprefix('2026-09-13-') for t in sunday['tasks']])
            self.assertTrue(again.state['routineScheduleHistory']['history'])
        finally: again.db.close()

    def test_schedule_edits_preserve_report_history_without_requiring_off_day_work(self):
        key=self.add([1])['id']; ident='2026-09-08-'+key
        self.store.mutate('/api/tasks/'+ident+'/report',{'note':'Synthetic report'},'river')
        before=copy.deepcopy(self.store.task(ident))
        self.change(key,[3])
        after=self.store.view()
        kept=next(t for t in after['tasks'] if t['id']==ident)
        self.assertFalse(kept['routineScheduled'])
        self.assertEqual(kept['history'],before['history'])
        self.assertNotIn(ident,[t['id'] for t in after['summary']['unfinished']])
        self.assertNotIn('chore:'+key,[i['id'] for i in after['rewards']['day']['items']])
        with self.assertRaises(AppError):
            self.store.mutate('/api/tasks/'+ident+'/report',{},'river')

    def test_validation_and_actor_boundaries(self):
        for days in ([],[1,1],[True],[7],['1'],'daily'):
            with self.assertRaises(AppError): self.change('dogs',days)
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):
                self.store.mutate('/api/parent/routine-chores/dogs/schedule',{'days':[1]},actor)
        with self.assertRaises(AppError): self.change('dogs',[1],'2026-09-07')
        with self.assertRaises(AppError): self.change('missing',[1])
        self.assertIn('dogs',self.ids('2026-09-08'))
