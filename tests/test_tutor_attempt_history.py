"""Tutor sees all relevant public attempt history; private grading evidence stays private."""
import copy
import json
import unittest
from datetime import timedelta
import test_tutoring as fixtures
from ai_review import jobs
from tutoring import context

class TutorHistoryTests(unittest.TestCase):
    setUp=fixtures.TutoringTests.setUp
    tearDown=fixtures.TutoringTests.tearDown
    call=fixtures.TutoringTests.call
    answer=fixtures.TutoringTests.answer
    review=fixtures.TutoringTests.review
    worker=fixtures.TutoringTests.worker
    ask=fixtures.TutoringTests.ask

    def test_all_attempts_feedback_and_hints_reach_actual_tutor_request(self):
        saved=[]
        for i in range(8):
            a=self.answer(1);self.review(a,False)
            a['feedback']=f'Try explanation {i}';a['retryHint']=f'Hint {i}'
            saved.append(a)
        worker=self.worker();self.ask();worker.step()
        req=next(c[2] for c in self.fake.calls if c[1]=='/agents/sessions')
        history=json.loads(req['input'])['answerHistory']
        self.assertEqual(len(history),8)
        for row,a in zip(history,saved):
            self.assertEqual(row,{'answer':a['answer'],'result':'incorrect','feedback':a['feedback'],'hint':a['retryHint']})
        self.assertNotIn('PRIVATE',json.dumps(req))

    def test_context_excludes_other_quizzes_questions_versions_and_future_attempts(self):
        a=self.answer(1);self.review(a,False)
        data=self.store.state['practice']['attempts']
        for changes in ({'quizId':'unrelated'},{'questionId':'q2'},{'questionVersion':-1},
                        {'at':(self.now+timedelta(hours=1)).isoformat()}):
            data.append(dict(copy.deepcopy(a),id='ignored'+str(len(data)),answer='UNRELATED',**changes))
        worker=self.worker();result=self.ask()
        payload=context(self.store,jobs(self.store)[result['messageId']])[2]
        self.assertEqual(len(payload['answerHistory']),1)
        self.assertNotIn('UNRELATED',json.dumps(payload))

    def test_sequence_scope_uses_current_approved_earlier_steps(self):
        self.quiz['sequential']=True
        a=self.answer(1);self.review(a,False)
        self.store.state['practice']['attempts'].append(dict(copy.deepcopy(a),id='stale',sequenceContext=['old'],answer='STALE'))
        worker=self.worker();result=self.ask()
        payload=context(self.store,jobs(self.store)[result['messageId']])[2]
        self.assertEqual(len(payload['answerHistory']),1)
        self.assertNotIn('STALE',json.dumps(payload))

    def test_pending_answer_is_labeled_pending_and_review_does_not_cancel_tutor(self):
        a=self.answer(1)
        worker=self.worker();result=self.ask()
        first=context(self.store,jobs(self.store)[result['messageId']])
        self.assertEqual(first[2]['answerHistory'][0]['result'],'pending')
        worker.step();self.review(a,False);worker.step()
        self.assertEqual(jobs(self.store)[result['messageId']]['status'],'completed')
