"""Synthetic short-answer review fixtures; never actual student performance."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from server import RiverStore,AppError
from practice import PracticeError,practice_state
from mastery import status

class MasteryReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        body={'title':'Synthetic six-question review','subject':'Test','reviewMode':'mastery','sourceMaterial':'PRIVATE KEY test only',
              'concepts':[{'id':'c','label':'Reading','goal':'Use the passage','targetCorrect':1,'targetSessions':1}],
              'questions':[{'id':f'q{i}','conceptId':'c','type':'rubric','prompt':f'Synthetic explanation {i}',
                            'rubric':'PRIVATE RUBRIC','required':True,'hintSteps':['Locate the scene.','Look at the surrounding sentences.']} for i in range(1,7)]}
        self.id=self.call('/api/assistant/practice/drafts',body,'assistant')['quizIds'][0]
        self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'reviewed':True},'parent')
        self.quiz=next(q for q in self.store.state['practice']['quizzes'] if q['id']==self.id)
        self.task=self.store.task(self.quiz['taskId'])
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def call(self,path,body=None,actor='river'):
        return self.store.transaction(lambda:self.store.mutate(path,body or {},actor))
    def answer(self,i,**extra):
        self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':f'q{i}','answer':'Synthetic self-written response',**extra})
        return self.store.state['practice']['attempts'][-1]
    def review(self,a,correct=True,**extra):
        return self.call('/api/assistant/practice/attempts/'+a['id']+'/review',{'correct':correct,'independent':True,'rubricEvidence':'PRIVATE EVIDENCE','feedback':'Use a passage to explain this point.' if not correct else 'Approved.',**extra},'assistant')
    def ready(self):return self.call('/api/tasks/'+self.task['id']+'/report')
    def confirm(self):return self.call('/api/parent/practice/mastery/'+self.id+'/confirm',{},'parent')
    def all_answers(self):return [self.answer(i) for i in range(1,7)]
    def test_parent_editor_keeps_mastery_concepts_and_mode(self):
        quiz=practice_state(self.store,'parent')['quizzes'][0]
        self.assertEqual(quiz['reviewMode'],'mastery')
        self.assertEqual(len(quiz['concepts']),1)
        self.call('/api/parent/practice/quizzes/'+self.id,quiz,'parent')
        self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'reviewed':True},'parent')
        self.assertEqual(practice_state(self.store,'parent')['quizzes'][0]['reviewMode'],'mastery')

    def test_answers_pending_until_assistant_review_and_parent_cannot_grade(self):
        a=self.answer(1)
        self.assertEqual(a['grade'],'pending')
        with self.assertRaises(PracticeError):self.answer(1)
        with self.assertRaises(PracticeError):self.call('/api/parent/practice/attempts/'+a['id']+'/review',{'correct':True,'independent':True,'rubricEvidence':'Attempt'},'parent')
        self.review(a)
        self.assertEqual(status(self.store,self.quiz)['questions'][0]['status'],'approved')
        self.assertIsNone(self.task['understanding'])
        self.assertIsNone(self.task['completedAt'])
    def test_only_missed_question_reopens_and_hints_do_not_make_success_impossible(self):
        answers=self.all_answers()
        for a in answers[1:]:self.review(a)
        self.review(answers[0],False)
        rows=status(self.store,self.quiz)['questions']
        self.assertEqual([r['status'] for r in rows],['retry']+['approved']*5)
        h=self.call('/api/practice/mastery/'+self.id+'/hint',{'questionId':'q1'})
        self.assertEqual(h['hint'],'Look at the surrounding sentences.')
        retry=self.answer(1,usedHelp=True)
        with self.assertRaises(PracticeError):self.review(answers[0])
        self.review(retry)
        stats=status(self.store,self.quiz)
        self.assertTrue(stats['allApproved']);self.assertEqual(stats['totalAttempts'],7)
        self.assertEqual(stats['totalHints'],2);self.assertEqual(stats['totalHelpedAttempts'],1)
        self.assertFalse(self.store.state['practice']['attempts'][-1]['independent'])
        self.ready();self.confirm();self.assertIsNotNone(self.store.task(self.task['id'])['completedAt'])
    def test_completion_requires_all_answers_and_final_parent_button(self):
        with self.assertRaises(PracticeError):self.confirm()
        for a in self.all_answers():self.review(a)
        with self.assertRaises(AppError):self.call('/api/tasks/'+self.task['id']+'/complete')
        with self.assertRaises((AppError,PracticeError)):self.call('/api/parent/tasks/'+self.task['id']+'/confirm',{},'parent')
        with self.assertRaises(AppError):self.call('/api/assistant/tasks/'+self.task['id']+'/understanding',{},'assistant')
        for actor in ('river','assistant'):
            with self.assertRaises((AppError,PracticeError)):self.call('/api/parent/practice/mastery/'+self.id+'/confirm',{},actor)
        self.ready();self.confirm()
        task=self.store.task(self.task['id'])
        self.assertIsNotNone(task['completedAt']);self.assertEqual(task['parentConfirmed']['note'],'')
        self.assertIsNone(task['submission']);self.assertIsNone(task['understanding'])
        events=len(task['history']);self.confirm();self.assertEqual(len(task['history']),events)
    def test_private_key_and_evidence_never_appear_in_river_state(self):
        a=self.answer(1);self.review(a,False,help='Find the scene in the reading.')
        public=json.dumps(practice_state(self.store,'river'))
        for secret in ('PRIVATE KEY','PRIVATE RUBRIC','PRIVATE EVIDENCE'):
            self.assertNotIn(secret,public)
        self.assertIn('Synthetic self-written response',public)
        self.assertIn('Use a passage to explain this point.',public)
        self.assertEqual(status(self.store,self.quiz)['totalTips'],1)
        self.assertIn('PRIVATE EVIDENCE',json.dumps(practice_state(self.store,'assistant')))
    def test_withdrawing_one_review_reopens_completion_and_preserves_other_approvals(self):
        answers=self.all_answers()
        for a in answers:self.review(a)
        self.ready();self.confirm()
        self.call('/api/assistant/practice/attempts/'+answers[2]['id']+'/revoke',{'note':'Check the evidence for this scene.'},'assistant')
        stats=status(self.store,self.quiz)
        self.assertEqual(sum(r['status']=='approved' for r in stats['questions']),5)
        self.assertIsNone(self.store.task(self.task['id'])['completedAt'])
        self.assertIsNone(stats['parentConfirmed'])
    def test_budget_and_saturday_cutoff_still_apply(self):
        self.now=self.now.replace(day=12)
        self.call('/api/parent/rewards/catchup',{'enabled':True},'parent')
        for a in self.all_answers():self.review(a)
        self.ready()
        self.now=self.now.replace(day=13)
        self.confirm()
        from catchup import school_done
        self.assertTrue(school_done(self.store,self.task,'2026-09-12'))
        a=self.store.state['practice']['attempts'][0]
        self.call('/api/assistant/practice/attempts/'+a['id']+'/revoke',{'note':'Synthetic retry'},'assistant')
        retry=self.answer(1);self.review(retry);self.ready();self.confirm()
        self.assertFalse(school_done(self.store,self.task,'2026-09-12'))
        self.call('/api/assistant/practice/attempts/'+retry['id']+'/revoke',{'note':'Synthetic retry'},'assistant')
        self.store.day(self.store.today())['schoolClock'].update(elapsedSeconds=5400,running=False,phase=None)
        with self.assertRaises(PracticeError):self.answer(1)
