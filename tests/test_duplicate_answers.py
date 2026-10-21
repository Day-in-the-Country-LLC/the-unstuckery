"""Unchanged answer checks use synthetic responses and preserve review evidence."""
import copy
from datetime import timedelta
import unittest
import test_mastery as fixtures
from mastery import status
from ai_review import jobs

class DuplicateAnswerTests(unittest.TestCase):
    setUp=fixtures.MasteryReviewTests.setUp
    tearDown=fixtures.MasteryReviewTests.tearDown
    call=fixtures.MasteryReviewTests.call
    answer=fixtures.MasteryReviewTests.answer
    review=fixtures.MasteryReviewTests.review

    def submit(self,text,question='q1'):
        return self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':question,'answer':text})

    def test_identical_retry_preserves_answer_feedback_history_and_review_queue(self):
        a=self.answer(1);self.review(a,False)
        before=copy.deepcopy(self.store.state)
        result=self.submit(a['answer'])
        self.assertTrue(result['duplicate']);self.assertIn('same answer',result['message'])
        self.assertEqual(self.store.state,before)
        self.assertEqual(status(self.store,self.quiz)['questions'][0]['status'],'retry')

    def test_spacing_alone_does_not_create_another_review(self):
        a=self.answer(1);self.review(a,False)
        result=self.submit('  Synthetic\n self-written   response \t')
        self.assertTrue(result['duplicate']);self.assertEqual(len(self.store.state['practice']['attempts']),1)

    def test_capitalization_and_punctuation_changes_are_reviewed(self):
        self.store.ai_review_enabled=True
        a=self.answer(1);self.review(a,False)
        for text in ('synthetic self-written response','synthetic self-written response.'):
            result=self.submit(text);self.assertNotIn('duplicate',result)
            latest=self.store.state['practice']['attempts'][-1]
            self.assertEqual(jobs(self.store)[latest['id']]['status'],'queued')
            self.review(latest,False)
        self.assertEqual(len(self.store.state['practice']['attempts']),3)

    def test_same_text_is_allowed_for_a_different_question(self):
        a=self.answer(1);self.review(a,False)
        self.assertNotIn('duplicate',self.submit(a['answer'],'q2'))
        self.assertEqual(len(self.store.state['practice']['attempts']),2)

    def test_only_the_latest_response_is_compared_for_ordinary_retries(self):
        a=self.answer(1);self.review(a,False)
        b=self.answer(1);self.review(b,False)
        self.assertNotIn('duplicate',self.submit(a['answer']))

    def test_new_question_version_can_review_the_same_text(self):
        a=self.answer(1);self.review(a,False)
        self.quiz['questions'][0]['questionVersion']='synthetic-new-version'
        self.assertNotIn('duplicate',self.submit(a['answer']))

    def test_new_due_learning_check_allows_same_answer_but_not_unchanged_retry(self):
        a=self.answer(1);self.review(a)
        question=self.quiz['questions'][0]
        self.store.state['practice'].setdefault('revisits',[]).append({
            'id':'synthetic-revisit','quizId':self.id,'questionId':'q1',
            'questionVersion':question['questionVersion'],'scheduledAt':self.store.stamp(),
            'dueDate':self.store.today(),'completedAt':None})
        self.assertNotIn('duplicate',self.submit(a['answer']))
        later=self.store.state['practice']['attempts'][-1]
        self.assertEqual(later['revisitId'],'synthetic-revisit')
        self.review(later,False)
        self.assertTrue(self.submit(later['answer'])['duplicate'])

    def test_continued_retry_on_later_day_still_requires_a_revision(self):
        a=self.answer(1);self.review(a,False);self.now+=timedelta(days=1)
        self.assertTrue(self.submit(a['answer'])['duplicate'])
