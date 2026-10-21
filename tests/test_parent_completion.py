"""Outside-app parent authority and conservative legacy reconciliation."""
import copy
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from server import RiverStore
from parent_review import reconcile,is_final

class ParentCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,9,1,tzinfo=timezone.utc)
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def task(self,title='Synthetic certificates'):
        t=self.store.create_task({'title':title,'subject':'Synthetic','instructions':'Test only','requirements':{'parent':True,'understanding':True,'submission':True}})
        return self.store.task(t['id'])
    def legacy(self):
        t=self.task()
        t.update(reportedAt='2026-09-09T00:06:24.810740+00:00',parentConfirmed={'at':'2026-09-09T00:10:09.343192+00:00','actor':'parent','note':'Synthetic parent checked both documents outside the app.'})
        return t
    def run_reconciliation(self):
        self.store.state.get('migrations',{}).pop('parent_school_confirmation_final_v1',None)
        self.store.transaction(lambda:reconcile(self.store))
    def test_legacy_reconciliation_preserves_original_records_and_persists_once(self):
        t=self.legacy();before=copy.deepcopy(t)
        self.run_reconciliation()
        self.assertEqual(t['completedAt'],before['parentConfirmed']['at'])
        for key in ('reportedAt','reportedNote','parentConfirmed','understanding','submission'):
            self.assertEqual(t[key],before[key])
        self.assertTrue(is_final(t));self.assertEqual(self.store.view_task(t)['needsReview'],[])
        self.assertNotIn(t['id'],[x['id'] for x in self.store.view()['summary']['unfinished']])
        count=len(t['history']);reconcile(self.store);self.assertEqual(len(t['history']),count)
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        self.assertEqual(len(self.store.task(t['id'])['history']),count)
    def test_reconciliation_does_not_revive_corrections_or_touch_quizzes(self):
        corrected=self.legacy();corrected['reviewHold']='Synthetic later correction'
        quiz=self.legacy();quiz['practiceQuizId']='synthetic-unfinished-quiz'
        returned=self.legacy();returned['parentReturned']={'at':self.store.stamp()}
        later=self.legacy();self.store.history(later,'assistant','evidence_revoked',{'note':'Synthetic later correction'})
        never_ready=self.legacy();never_ready['reportedAt']=None
        self.run_reconciliation()
        for t in (corrected,quiz,returned,later,never_ready):self.assertIsNone(t['completedAt'])
    def test_confirm_without_assistant_records_is_final_and_repeat_keeps_parent_note(self):
        t=self.task();path='/api/parent/tasks/'+t['id']
        self.store.transaction(lambda:self.store.mutate('/api/tasks/'+t['id']+'/report',{},'river'))
        self.store.transaction(lambda:self.store.mutate(path+'/confirm',{'note':'Synthetic outside-app review'},'parent'))
        confirmation=copy.deepcopy(t['parentConfirmed']);completed=t['completedAt'];events=len(t['history'])
        self.now+=timedelta(minutes=1)
        self.store.transaction(lambda:self.store.mutate(path+'/confirm',{},'parent'))
        self.assertEqual(t['parentConfirmed'],confirmation);self.assertEqual(t['completedAt'],completed)
        self.assertEqual(len(t['history']),events)
        self.assertIsNone(t['understanding']);self.assertIsNone(t['submission'])
        self.assertTrue(is_final(t))
        self.store.transaction(lambda:self.store.mutate(path+'/return',{},'parent'))
        self.assertIsNone(t['completionBasis']);self.assertIsNone(t['completedAt']);self.assertFalse(is_final(t))
