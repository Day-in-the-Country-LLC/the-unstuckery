"""Explicit student readiness and no-note parent return, using synthetic records."""
import copy
import unittest
from datetime import timedelta
import test_mastery as helpers
from server import AppError
from practice import PracticeError
from mastery import status

class ParentReadyTests(unittest.TestCase):
    setUp=helpers.MasteryReviewTests.setUp
    tearDown=helpers.MasteryReviewTests.tearDown
    def call(self,path,body=None,actor='river'):
        try:return helpers.MasteryReviewTests.call(self,path,body,actor)
        finally:
            if isinstance(self.id,str):
                self.quiz=next(q for q in self.store.state['practice']['quizzes'] if q['id']==self.id)
                if self.quiz.get('taskId'):self.task=self.store.task(self.quiz['taskId'])
    answer=helpers.MasteryReviewTests.answer
    review=helpers.MasteryReviewTests.review
    all_answers=helpers.MasteryReviewTests.all_answers
    ready=helpers.MasteryReviewTests.ready

    def test_mastery_report_is_explicit_and_send_back_preserves_all_learning(self):
        route='/api/parent/tasks/'+self.task['id']
        with self.assertRaises(AppError):self.ready()
        for a in self.all_answers():self.review(a)
        self.assertIsNone(self.task['reportedAt'])
        with self.assertRaises(PracticeError):self.call(route+'/confirm',{},'parent')
        with self.assertRaises(AppError):self.call('/api/tasks/'+self.task['id']+'/report',{},'assistant')
        self.ready()
        before=copy.deepcopy(self.store.state['practice']['attempts'])
        first=self.task['reportedAt']
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.call(route+'/return',{},actor)
        self.call(route+'/return',{},'parent')
        self.assertIsNone(self.task['reportedAt']);self.assertIsNone(self.task['parentConfirmed'])
        self.assertTrue(status(self.store,self.quiz)['allApproved'])
        self.assertEqual(before,self.store.state['practice']['attempts'])
        self.assertEqual(self.task['history'][-1]['action'],'returned_to_river')
        self.now+=timedelta(minutes=1);self.ready()
        self.assertNotEqual(first,self.task['reportedAt'])
        self.assertIsNone(self.task['parentReturned'])
        self.call(route+'/confirm',{},'parent')
        self.assertIsNotNone(self.task['completedAt'])
        self.call(route+'/return',{},'parent')
        self.assertIsNone(self.task['completedAt'])
        self.assertIsNone(self.quiz['parentConfirmed'])
        self.assertTrue(status(self.store,self.quiz)['allApproved'])
        self.ready();self.call(route+'/confirm',{},'parent')
        self.assertIsNotNone(self.task['completedAt'])

    def test_ordinary_parent_review_is_final_without_fabricating_evidence(self):
        task=self.store.transaction(lambda:self.store.create_task({'title':'Synthetic paper review','subject':'Test','instructions':'Synthetic work only.','requirements':{'parent':True,'submission':True,'understanding':True}}))
        task=self.store.task(task['id']);route='/api/parent/tasks/'+task['id']
        with self.assertRaises(AppError):self.call(route+'/confirm',{},'parent')
        task=self.store.task(task['id'])
        self.call('/api/tasks/'+task['id']+'/report')
        self.call(route+'/confirm',{},'parent')
        self.assertIsNotNone(task['parentConfirmed']);self.assertIsNotNone(task['completedAt'])
        self.assertIsNone(task['understanding']);self.assertIsNone(task['submission'])
        self.assertEqual(self.store.view_task(task)['needsReview'],[])
        task['understanding']={'at':self.store.stamp(),'evidence':'Synthetic test understanding only.'}
        task['submission']={'at':self.store.stamp(),'observedAt':self.store.stamp(),'evidence':'Synthetic test submission only.'}
        evidence=copy.deepcopy([task['understanding'],task['submission']])
        self.call(route+'/return',{},'parent')
        self.assertIsNone(task['reportedAt']);self.assertIsNone(task['parentConfirmed'])
        self.assertEqual(evidence,[task['understanding'],task['submission']])
        self.call('/api/tasks/'+task['id']+'/report')
        self.call(route+'/confirm',{},'parent')
        self.assertIsNotNone(task['completedAt'])
        self.assertEqual(evidence,[task['understanding'],task['submission']])
