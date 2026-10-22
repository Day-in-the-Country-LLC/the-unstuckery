"""Synthetic regression cases for history-aware coaching; no real student work."""
import copy
import json
import unittest
import test_mastery as fixtures
import test_ai_review as ai_fixtures
from ai_review import answer_history, context, jobs, PROMPT_VERSION

class ProgressiveFeedbackTests(unittest.TestCase):
    setUp=fixtures.MasteryReviewTests.setUp
    tearDown=fixtures.MasteryReviewTests.tearDown
    call=fixtures.MasteryReviewTests.call
    answer=fixtures.MasteryReviewTests.answer
    review=fixtures.MasteryReviewTests.review
    worker=ai_fixtures.AIReviewTests.worker

    def test_actual_model_request_includes_every_prior_response_feedback_hint_and_result(self):
        for i in range(8):
            a=self.answer(1,answer=f'Synthetic earlier draft {i}')
            self.review(a,False,feedback=f'Earlier explanation {i}',hint=f'Earlier scaffold {i}')
        worker=self.worker();current=self.answer(1,answer='The current draft is separate.')
        worker.step()
        payload=json.loads(self.fake.calls[0][2]['input'])
        self.assertEqual(payload['answer'],current['answer'])
        self.assertEqual(payload['earlierAttempts'],[
            {'answer':f'Synthetic earlier draft {i}','result':'incorrect',
             'feedback':f'Earlier explanation {i}','hint':f'Earlier scaffold {i}'} for i in range(8)])
        self.assertIn('plain words',payload['tutoringSupport'])
        self.assertNotIn('PRIVATE EVIDENCE',json.dumps(payload['earlierAttempts']))
        self.assertEqual(jobs(self.store)[current['id']]['promptVersion'],PROMPT_VERSION)

    def test_history_excludes_other_quizzes_questions_versions_and_stale_sequence(self):
        def record(ident,**extra):
            return {'id':ident,'quizId':'quiz','questionId':'q','questionVersion':'v2',
                    'sequenceContext':['approved-earlier-step'],'answer':ident,'grade':'incorrect',
                    'feedback':'Public feedback','retryHint':'Public hint','reviewEvidence':'PRIVATE',**extra}
        current=record('current')
        data={'attempts':[
            record('other-quiz',quizId='different'),record('other-question',questionId='different'),
            record('old-version',questionVersion='v1'),record('stale-sequence',sequenceContext=['replaced-step']),
            record('relevant'),current,record('future-record')]}
        history=answer_history(data,{'id':'quiz','sequential':True},{'id':'q','questionVersion':'v2'},current)
        self.assertEqual(history,[{'answer':'relevant','result':'incorrect','feedback':'Public feedback','hint':'Public hint'}])

    def test_first_attempt_has_no_invented_history(self):
        current=self.answer(1)
        payload=context(self.store,{'quizId':self.id,'attemptId':current['id']})[2]
        self.assertEqual(payload['earlierAttempts'],[])

    def test_unrelated_old_or_later_hints_are_excluded_without_changing_saved_work(self):
        a=self.answer(1);self.review(a,False,hint='Relevant hint')
        current=self.answer(1)
        data=self.store.state['practice']
        template=copy.deepcopy(data['hints'][-1])
        data['hints'].extend([
            dict(template,questionVersion='old',text='EXCLUDE OLD'),
            dict(template,questionId='q2',text='EXCLUDE OTHER'),
            dict(template,at='2099-01-01T00:00:00+00:00',text='EXCLUDE LATER')])
        saved=copy.deepcopy(self.store.state)
        payload=context(self.store,{'quizId':self.id,'attemptId':current['id']})[2]
        self.assertEqual(payload['help'],['Relevant hint'])
        self.assertEqual(saved,self.store.state)

    def test_queued_review_records_the_prompt_version_actually_used(self):
        worker=self.worker();current=self.answer(1)
        jobs(self.store)[current['id']]['promptVersion']='legacy-prompt'
        worker.step()
        self.assertEqual(jobs(self.store)[current['id']]['promptVersion'],PROMPT_VERSION)
