"""Question-scoped tutor tests use synthetic practice and a fake AI transport."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import unittest

import test_mastery as fixtures
from ai_review import ReviewWorker, jobs
from practice import PracticeError, practice_state
from tutoring import INSTRUCTIONS, _next_weekday, context


class FakeTutorClient:
    def __init__(self):
        self.calls = []
        self.session = {'id': 'sess_tutor', 'status': 'requires_action', 'required_actions': [{
            'type': 'function_call', 'turn_id': 'turn_tutor', 'call_id': 'call_tutor',
            'name': 'save_tutor_reply', 'arguments': {
                'reply': 'A rate compares two amounts. For example, 120 miles in 2 hours is 60 miles per hour. What two amounts are being compared in your question?'
            }}]}

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == 'POST' and path == '/agents/sessions':
            return {'id': 'sess_tutor', 'status': 'in_progress'}
        if method == 'GET':
            return copy.deepcopy(self.session)
        return {}


class TutoringTests(unittest.TestCase):
    setUp = fixtures.MasteryReviewTests.setUp
    tearDown = fixtures.MasteryReviewTests.tearDown
    call = fixtures.MasteryReviewTests.call
    answer = fixtures.MasteryReviewTests.answer
    review = fixtures.MasteryReviewTests.review

    def worker(self):
        self.store.ai_review_enabled = True
        path = Path(self.tmp.name) / 'private.env'
        path.write_text('OPENAI_API_KEY=synthetic-test-key\n')
        path.chmod(0o600)
        self.fake = FakeTutorClient()
        worker = ReviewWorker(self.store, path, lambda key: self.fake)
        self.store.ai_review_worker = worker
        return worker

    def ask(self, message='I do not understand what a rate calculation is.'):
        return self.call('/api/practice/mastery/' + self.id + '/tutor', {
            'questionId': 'q1', 'message': message})

    def test_tutor_receives_question_and_support_context_without_private_answer_key(self):
        worker = self.worker()
        result = self.ask()
        worker.step()
        request = next(call for call in self.fake.calls if call[0] == 'POST' and call[1] == '/agents/sessions')
        payload = json.loads(request[2]['input'])
        self.assertEqual(payload['practiceQuestion'], 'Synthetic explanation 1')
        self.assertEqual(payload['studentMessage'], 'I do not understand what a rate calculation is.')
        self.assertIn('plain words', payload['tutoringSupport'])
        self.assertIn('writingPolicy', payload)
        serialized = json.dumps(request[2])
        self.assertNotIn('PRIVATE RUBRIC', serialized)
        self.assertNotIn('PRIVATE KEY', serialized)
        self.assertEqual(request[2]['agent']['tools'][0]['name'], 'save_tutor_reply')
        self.assertEqual(len(request[2]['agent']['tools']), 1)
        self.assertEqual(jobs(self.store)[result['messageId']]['status'], 'running')

    def test_tutor_is_allowed_to_teach_the_current_problem_without_writing_rivers_response(self):
        self.assertIn('work through the current problem interactively', INSTRUCTIONS)
        self.assertIn('After a genuine attempt, you may explain the\ncomplete solution', INSTRUCTIONS)
        self.assertIn('write the final response in\nhis own words', INSTRUCTIONS)
        self.assertNotIn('do not solve the\ncurrent question', INSTRUCTIONS)

    def test_sequential_tutor_uses_saved_writing_and_preserves_step_progress(self):
        body=practice_state(self.store,'parent')['quizzes'][0]
        body['sequential']=True
        self.call('/api/parent/practice/quizzes/'+self.id,body,'parent')
        self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'approved':True},'parent')
        self.review(self.answer(1))
        worker=self.worker()
        result=self.call('/api/practice/mastery/'+self.id+'/tutor',{
            'questionId':'q2','message':'What does this step mean?'})
        payload=context(self.store,jobs(self.store)[result['messageId']])[2]
        self.assertTrue(payload['stepByStep'])
        self.assertEqual(payload['approvedEarlierSteps'][0]['questionId'],'q1')
        worker.step();worker.step()
        self.assertEqual(jobs(self.store)[result['messageId']]['status'],'completed')
        self.assertEqual(self.store.state['practice']['revisits'],[])
        attempt=self.answer(2)
        self.assertTrue(attempt['supported'])
        self.review(attempt)
        public=practice_state(self.store,'river')['quizzes'][0]
        self.assertEqual(public['mastery']['questions'][1]['status'],'approved')
        self.assertEqual(public['mastery']['questions'][2]['status'],'unanswered')

    def test_saved_reply_supports_follow_up_and_marks_next_answer_as_helped(self):
        worker = self.worker()
        first = self.ask()
        worker.step()
        worker.step()
        self.assertEqual(jobs(self.store)[first['messageId']]['status'], 'completed')
        public = practice_state(self.store, 'river')
        chat = public['tutorChats'][0]
        self.assertEqual([message['role'] for message in chat['messages']], ['river', 'tutor'])
        self.assertIn('A rate compares two amounts', chat['messages'][1]['text'])

        follow_up = self.ask('I still do not understand which two amounts to compare.')
        _, _, payload, _ = context(self.store, jobs(self.store)[follow_up['messageId']])
        self.assertEqual([turn['role'] for turn in payload['earlierConversation']], ['river', 'tutor'])
        self.assertIn('A rate compares two amounts', payload['earlierConversation'][1]['text'])

        self.call('/api/practice/mastery/' + self.id + '/answer', {
            'questionId': 'q1', 'answer': 'Synthetic response after tutoring.'})
        attempt = self.store.state['practice']['attempts'][-1]
        self.assertTrue(attempt['supported'])

    def test_tutor_use_schedules_a_next_weekday_retrieval_check_without_reopening_today(self):
        worker = self.worker()
        self.ask()
        worker.step()
        worker.step()
        revisit = self.store.state['practice']['revisits'][0]
        self.assertEqual(revisit['dueDate'], '2026-09-09')
        self.assertIsNone(revisit['completedAt'])

        answers = [self.answer(i) for i in range(1, 7)]
        for attempt in answers:
            self.review(attempt)
        stats = practice_state(self.store, 'river')['quizzes'][0]['mastery']
        self.assertEqual(stats['questions'][0]['status'], 'scheduled_review')
        self.assertEqual(stats['questions'][0]['revisit']['dueDate'], '2026-09-09')
        self.assertFalse(stats['allApproved'])
        with self.assertRaises(PracticeError):
            self.answer(1)

        self.now += timedelta(days=1)
        stats = practice_state(self.store, 'river')['quizzes'][0]['mastery']
        self.assertEqual(stats['questions'][0]['status'], 'revisit')
        view = self.store.view()
        self.assertIn(self.task['id'], {task['id'] for task in view['schoolwork']['today']})
        self.assertNotIn(self.task['id'], {task['id'] for task in view['schoolwork']['earlier']})
        self.assertEqual(next(task for task in view['tasks'] if task['id'] == self.task['id'])['practiceReviewDate'], '2026-09-09')
        later = self.answer(1)
        self.assertEqual(later['revisitId'], revisit['id'])
        self.assertFalse(later['supported'])
        self.review(later)
        stats = practice_state(self.store, 'river')['quizzes'][0]['mastery']
        self.assertEqual(stats['questions'][0]['status'], 'approved')
        self.assertTrue(stats['allApproved'])
        saved_revisit = next(item for item in self.store.state['practice']['revisits'] if item['id'] == revisit['id'])
        self.assertEqual(saved_revisit['completedByAttemptId'], later['id'])
        self.assertIn(self.task['id'], {task['id'] for task in self.store.view()['schoolwork']['today']})

    def test_later_review_skips_weekends(self):
        self.assertEqual(_next_weekday('2026-09-11'), '2026-09-14')

    def test_public_chat_exposes_saved_messages_but_never_private_grading_material(self):
        worker = self.worker()
        self.ask()
        worker.step()
        worker.step()
        public = json.dumps(practice_state(self.store, 'river'))
        self.assertIn('I do not understand what a rate calculation is.', public)
        self.assertIn('A rate compares two amounts', public)
        self.assertNotIn('PRIVATE RUBRIC', public)
        self.assertNotIn('PRIVATE KEY', public)

    def test_only_river_can_ask_and_one_pending_message_is_allowed_per_question(self):
        self.worker()
        self.ask()
        with self.assertRaises(PracticeError):
            self.ask('A second message sent too quickly.')
        for actor in ('parent', 'assistant'):
            with self.assertRaises(PracticeError):
                self.call('/api/practice/mastery/' + self.id + '/tutor', {
                    'questionId': 'q1', 'message': 'Synthetic question.'}, actor)


if __name__ == '__main__':
    unittest.main()
