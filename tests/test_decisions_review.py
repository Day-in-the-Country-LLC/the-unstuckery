"""Synthetic Decisions transport: routing, immutable grades, recovery and writing gates."""
import copy
import json
from pathlib import Path
import unittest
import test_ai_review as fixtures
import decisions_review as decisions
from ai_review import AgentError, ReviewWorker, context, jobs, public_status, retry
from practice import PracticeError

class FakeDecisions(fixtures.FakeClient):
    def __init__(self):
        super().__init__()
        self.verdict = 'pass'
        self.overrides = {}
        self.broken = None
        self.session['required_actions'][0].update(name='save_review_feedback', arguments={
            'feedback': 'Not yet. Compare your claim with a question about the weather. What could you measure?',
            'hint': 'Try one question that leaves room for either result.',
            'revisionGoals': ['What detail from your source could explain your point?']})

    def request(self, method, path, body=None):
        if path != '/decisions': return super().request(method, path, body)
        self.calls.append((method,path,copy.deepcopy(body)))
        if self.error: raise self.error
        if self.broken is not None: return self.broken
        state=json.loads(body['input'])
        result=[]
        for q in body['questions']:
            name=q['name']
            value=self.overrides.get(name, self.verdict if name == 'verdict' else True)
            if name.startswith('boundary_'):
                candidate=state['boundaryCandidates'][int(name.split('_')[1])]
                value=candidate['left'].endswith(('.', '!', '?'))
            result.append({'name':name,'type':'choice','choice':value,'confidence':0.95})
        return {'model':decisions.MODEL,'answers':result}

class DecisionsWorkerTests(unittest.TestCase):
    setUp=fixtures.AIReviewTests.setUp
    tearDown=fixtures.AIReviewTests.tearDown
    call=fixtures.AIReviewTests.call
    answer=fixtures.AIReviewTests.answer
    review=fixtures.AIReviewTests.review

    def worker(self):
        self.store.ai_review_enabled=True
        path=Path(self.tmp.name)/'private.env'
        path.write_text('OPENAI_API_KEY=synthetic-key\nOPENAI_REVIEW_MODE=decisions\nRIVER_AI_MAX_REVIEWS_PER_DAY=0\n')
        path.chmod(0o600)
        self.fake=FakeDecisions()
        worker=ReviewWorker(self.store,path,lambda _:self.fake)
        self.store.ai_review_worker=worker
        return worker

    def test_pass_never_calls_sol_and_does_not_complete_parent_task(self):
        worker=self.worker();a=self.answer(1,usedHelp=True);worker.step()
        self.assertEqual(a['grade'],'correct');self.assertFalse(a['independent'])
        self.assertEqual([c[1] for c in self.fake.calls],['/decisions'])
        self.assertIsNone(self.task['completedAt']);self.assertIsNone(self.task['parentConfirmed'])
        self.assertNotIn('criteria',json.dumps(public_status(self.store)))

    def test_fail_calls_sol_only_for_coaching_with_entire_history(self):
        for i in range(7): self.review(self.answer(1),False)
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1);worker.step()
        self.assertEqual(a['grade'],'pending')
        req=self.fake.calls[-1][2];payload=json.loads(req['input'])
        self.assertEqual(len(payload['earlierAttempts']),7)
        self.assertEqual(req['agent']['model'],'gpt-6.1-sol')
        self.assertEqual(req['agent']['tools'][0]['name'],'save_review_feedback')
        self.assertFalse(payload['decisionAssessment']['correct'])
        self.assertNotIn('feedback', payload['decisionAssessment'])
        self.assertEqual(jobs(self.store)[a['id']]['decisionReview']['feedback'], '')
        grading=json.loads(self.fake.calls[0][2]['input'])
        self.assertNotIn('earlierAttempts',grading)
        worker.step();self.assertEqual(a['grade'],'incorrect')
        self.assertIn('weather',a['feedback'])

    def test_sol_cannot_override_a_failed_decision(self):
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1);worker.step()
        self.fake.session['required_actions'][0]['arguments']['correct']=True
        worker.step()
        self.assertEqual(self.store.state['practice']['attempts'][-1]['grade'],'pending')
        self.assertEqual(jobs(self.store)[a['id']]['failures'],1)

    def test_refusal_malformed_and_missing_context_do_not_fail_student_or_call_sol(self):
        for broken in ({}, {'answers':[{'name':'verdict','type':'refusal'}]},
                       {'answers':[{'name':'verdict','type':'choice','choice':'pass','confidence':float('nan')}]},
                       {'answers':[{'name':'verdict','type':'choice','choice':'unreviewable','confidence':0.9}]}):
            with self.subTest(broken=broken):
                worker=self.worker();self.fake.broken=broken;a=self.answer(1);worker.step()
                self.assertEqual(a['grade'],'pending')
                self.assertEqual(jobs(self.store)[a['id']]['status'],'failed')
                self.assertEqual([c[1] for c in self.fake.calls],['/decisions'])
                self.review(a,False)

    def test_network_failure_and_retry_do_not_grade_as_fail(self):
        worker=self.worker();self.fake.error=AgentError('Connection interrupted.');a=self.answer(1);worker.step()
        self.assertEqual(a['grade'],'pending')
        self.fake.error=None;retry(self.store,self.id,{'attemptId':a['id']},'river');worker.step()
        self.assertEqual(a['grade'],'correct')

    def test_persisted_pass_resumes_without_network_after_restart(self):
        worker=self.worker();a=self.answer(1)
        _,_,payload,fingerprint=context(self.store,jobs(self.store)[a['id']])
        assessment=decisions.evaluate(self.fake,payload)
        self.store.transaction(lambda:jobs(self.store)[a['id']].update(
            status='decided',reviewMode='decisions',fingerprint=fingerprint,decisionReview=assessment,tries=1))
        from server import RiverStore
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        worker=self.worker();worker.step()
        self.assertFalse(self.fake.calls)
        self.assertEqual(self.store.state['practice']['attempts'][-1]['grade'],'correct')

    def test_feedback_startup_retry_reuses_fixed_decision(self):
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1);worker.step()
        job=jobs(self.store)[a['id']];job.update(status='creating',sessionId=None)
        worker.step();self.assertEqual(job['status'],'failed')
        retry(self.store,self.id,{'attemptId':a['id']},'river');worker.step();worker.step()
        self.assertEqual(a['grade'],'incorrect')
        self.assertEqual(sum(c[1]=='/decisions' for c in self.fake.calls),1)

    def test_old_contradictory_feedback_is_removed_from_coaching_context(self):
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1)
        _,_,payload,fingerprint=context(self.store,jobs(self.store)[a['id']])
        assessment=decisions.evaluate(self.fake,payload)
        assessment['feedback']='Pass — your answer meets this step’s requirements.'
        self.store.transaction(lambda:jobs(self.store)[a['id']].update(
            status='decided',reviewMode='decisions',fingerprint=fingerprint,decisionReview=assessment,tries=1))
        self.fake.calls.clear();worker.step()
        req=self.fake.calls[-1][2]
        supplied=json.loads(req['input'])['decisionAssessment']
        self.assertFalse(supplied['correct']);self.assertNotIn('feedback',supplied)
        self.assertNotIn('Pass —',req['input'])
        self.assertFalse(any(c[1]=='/decisions' for c in self.fake.calls))
        worker.step();self.assertEqual(a['grade'],'incorrect')

    def test_unable_to_review_retains_pending_and_retry_uses_fresh_coaching_session(self):
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1);worker.step()
        action=self.fake.session['required_actions'][0]
        action.update(name='unable_to_review',arguments={'reason':'PRIVATE conflicting rubric detail'})
        worker.step();job=jobs(self.store)[a['id']]
        self.assertEqual(a['grade'],'pending');self.assertTrue(job['sessionEnded'])
        self.assertNotIn('parent check',job['message'])
        self.assertNotIn('PRIVATE',json.dumps(public_status(self.store)))
        retry(self.store,self.id,{'attemptId':a['id']},'river')
        self.assertIsNone(job['sessionId']);self.assertEqual(job['status'],'queued')
        action.update(name='save_review_feedback',call_id='recovered',arguments={
            'feedback':'Not yet. Find the missing piece in the source.', 'hint':'Which detail is missing?'})
        worker.step();worker.step()
        self.assertEqual(a['grade'],'incorrect')
        self.assertEqual(sum(c[1]=='/decisions' for c in self.fake.calls),1)
        self.assertEqual(sum(c[1]=='/agents/sessions' for c in self.fake.calls),2)
        self.assertIsNone(self.task['parentConfirmed'])

    def test_stale_decision_cannot_override_manual_review(self):
        worker=self.worker();a=self.answer(1)
        original=self.fake.request
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            self.review(a,False)
            return result
        self.fake.request=changed;worker.step()
        self.assertEqual(a['grade'],'incorrect')
        self.assertEqual(jobs(self.store)[a['id']]['status'],'failed')

    def test_writing_pass_accounts_for_exact_text_without_sol(self):
        worker=self.worker()
        self.quiz['questions'][0]['writingRequirements']={'minQualifiedSentences':2,'requiredCoverage':['Topic'],'expectedSentenceRange':'2'}
        a=self.answer(1,answer='Trees give shade. Their roots hold soil.')
        self.fake.overrides['mechanics_spelling']=False
        worker.step()
        self.assertEqual(a['grade'],'correct')
        self.assertEqual(a['writingReview']['qualifyingSentences'],2)
        self.assertEqual(' '.join(x['text'] for x in a['writingReview']['sentences']),a['answer'])
        self.assertFalse(a['writingMechanics']['spelling'])
        self.assertTrue(all(c[1]=='/decisions' for c in self.fake.calls))

    def test_writing_checks_can_block_global_pass_and_receive_coaching(self):
        worker=self.worker()
        self.quiz['questions'][0]['writingRequirements']={'minQualifiedSentences':2,'requiredCoverage':['Topic'],'expectedSentenceRange':'2'}
        a=self.answer(1,answer='Trees give shade. Their roots hold soil.')
        self.fake.overrides['coverage_0']=False
        worker.step();worker.step()
        self.assertEqual(a['grade'],'incorrect')
        self.assertIn('What detail',a['feedback'])
        self.assertFalse(a['writingReview']['coverage']['Topic'])
        self.assertEqual(jobs(self.store)[a['id']]['decisionReview']['feedback'], '')

    def test_effort_is_separate_and_preserved_on_pass(self):
        worker=self.worker();self.quiz['questions'][0]['effortPolicy']='three-genuine-efforts'
        a=self.answer(1);worker.step()
        self.assertEqual(a['grade'],'correct');self.assertTrue(a['effortReview']['counts'])
        requests=[json.loads(c[2]['input']) for c in self.fake.calls]
        self.assertNotIn('effortCoaching',requests[0]);self.assertIn('effortCoaching',requests[1])

    def test_duplicate_retry_has_no_new_decision_or_coaching_call(self):
        worker=self.worker();self.fake.verdict='fail';a=self.answer(1);worker.step();worker.step()
        count=len(self.fake.calls)
        result=self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':'q1','answer':a['answer']})
        self.assertTrue(result['duplicate']);self.assertEqual(len(self.fake.calls),count)

class DecisionsBatches(unittest.TestCase):
    def test_large_boundary_sets_are_batched_and_preserve_all_text(self):
        fake=FakeDecisions();text=' '.join(['Word.']*200 + ['word']*210)
        spans=decisions.sentence_spans
        with self.assertRaises(AgentError):spans(fake,{'answer':text})  # 201 segments exceeds validated writing limit
        self.assertEqual([len(c[2]['questions']) for c in fake.calls],[400,9])
