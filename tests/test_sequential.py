"""Synthetic fixtures for gated, saved writing steps."""
import unittest
import test_mastery as fixtures
from practice import PracticeError, practice_state
from mastery import status
from ai_review import context, AgentError

class SequentialTests(unittest.TestCase):
    call=fixtures.MasteryReviewTests.call
    answer=fixtures.MasteryReviewTests.answer
    review=fixtures.MasteryReviewTests.review
    ready=fixtures.MasteryReviewTests.ready
    confirm=fixtures.MasteryReviewTests.confirm
    tearDown=fixtures.MasteryReviewTests.tearDown
    def setUp(self):
        fixtures.MasteryReviewTests.setUp(self)
        body=practice_state(self.store,'parent')['quizzes'][0]
        body['sequential']=True
        self.call('/api/parent/practice/quizzes/'+self.id,body,'parent')
        self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'reviewed':True},'parent')
        self.quiz=self.store.state['practice']['quizzes'][0]
    def test_sequence_gates_retries_and_context(self):
        for action in ('answer','hint','tutor'):
            with self.assertRaises(PracticeError):self.call('/api/practice/mastery/'+self.id+'/'+action,{'questionId':'q2','answer':'skipping'})
        a=self.answer(1)
        with self.assertRaises(PracticeError):self.answer(2)
        self.review(a,False)
        with self.assertRaises(PracticeError):self.answer(2)
        retry=self.answer(1);self.review(retry)
        a2=self.answer(2)
        payload=context(self.store,{'quizId':self.id,'attemptId':a2['id']})[2]
        self.assertEqual(payload['approvedEarlierSteps'][0]['answer'],retry['answer'])
        self.assertTrue(payload['stepByStep'])
        self.assertEqual(status(self.store,self.quiz)['questions'][2]['status'],'locked')
        self.review(a2)
        public=practice_state(self.store,'river')['quizzes'][0]
        self.assertTrue(public['sequential'])
        self.assertEqual(public['mastery']['questions'][2]['previousAnswer'],a2['answer'])
    def test_legacy_quiz_can_enable_sequence(self):
        self.quiz.pop('sequential')
        body=practice_state(self.store,'parent')['quizzes'][0]
        body['sequential']=True
        self.call('/api/parent/practice/quizzes/'+self.id,body,'parent')
        self.assertTrue(self.quiz['sequential'])
    def test_revocation_invalidates_downstream(self):
        a=self.answer(1);self.review(a)
        b=self.answer(2);self.review(b)
        c=self.answer(3)
        self.call('/api/assistant/practice/attempts/'+a['id']+'/revoke',{'note':'Synthetic correction'},'assistant')
        with self.assertRaises(AgentError):context(self.store,{'quizId':self.id,'attemptId':c['id']})
        self.assertEqual(status(self.store,self.quiz)['questions'][1]['status'],'locked')
        a=self.answer(1);self.review(a)
        self.assertEqual(status(self.store,self.quiz)['questions'][1]['status'],'unanswered')
        with self.assertRaises(PracticeError):self.answer(3)
    def test_sequence_completes_only_after_all_steps(self):
        for i in range(1,7):
            a=self.answer(i);self.review(a)
        self.assertTrue(status(self.store,self.quiz)['allApproved'])
        self.ready();self.confirm()
        self.assertIsNotNone(self.store.task(self.task['id'])['completedAt'])
