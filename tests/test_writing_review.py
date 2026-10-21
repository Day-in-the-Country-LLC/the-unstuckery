"""Sentence-level reviewer attestations and safe updates to unused assistant drafts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from server import RiverStore,AppError
from practice import PracticeError,practice_state
from writing_review import assess

RULES={'minQualifiedSentences':8,'expectedSentenceRange':'8–12','requiredCoverage':['First idea','Second idea','Third idea']}

def assessment(count=8):
    rows=[{'number':i,'text':f'Synthetic sentence number {i}.','complete':True,'relevant':True,'accurate':True,'addsNewIdea':True} for i in range(1,count+1)]
    return {'sentences':rows,'coverage':dict.fromkeys(RULES['requiredCoverage'],True),'evidenceExplained':True}

class WritingAssessmentTests(unittest.TestCase):
    def check(self,data,approved=True,**extra):
        answer=' '.join(row['text'] for row in data['sentences'])
        return assess({'writingRequirements':RULES},{'answer':answer},{'writingReview':data,**extra},approved)
    def test_eight_qualifying_sentences_with_coverage_and_evidence_pass(self):
        self.assertEqual(self.check(assessment())['qualifyingSentences'],8)
        self.assertEqual(self.check(assessment(13))['qualifyingSentences'],13)
    def test_fragments_filler_repetition_off_topic_and_inaccuracy_do_not_count(self):
        for flag in ('complete','relevant','accurate','addsNewIdea'):
            data=assessment();data['sentences'][2][flag]=False;data['sentences'][2]['reason']='Synthetic reason this sentence does not qualify.'
            with self.assertRaises(PracticeError):self.check(data)
            review=self.check(data,False,revisionGoals=['Revise sentence 3 using your reading.'])
            self.assertEqual(review['qualifyingSentences'],7)
            self.assertFalse(review['sentences'][2]['counts'])
    def test_short_response_missing_character_or_missing_evidence_cannot_pass(self):
        with self.assertRaises(PracticeError):self.check(assessment(7))
        for key in RULES['requiredCoverage']:
            data=assessment();data['coverage'][key]=False
            with self.assertRaises(PracticeError):self.check(data)
        data=assessment();data['evidenceExplained']=False
        with self.assertRaises(PracticeError):self.check(data)
    def test_review_cannot_duplicate_or_omit_actual_answer_text_and_retry_goals_are_bounded(self):
        data=assessment()
        with self.assertRaises(PracticeError):assess({'writingRequirements':RULES},{'answer':'A shorter actual answer.'},{'writingReview':data},True)
        for goals in ([],['A','B','C']):
            with self.assertRaises(PracticeError):self.check(data,False,revisionGoals=goals)

class DraftWritingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=RiverStore(self.tmp.name,now=lambda:datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis')))
        self.body={'title':'Synthetic writing test','subject':'Writing','reviewMode':'mastery','planDate':'2026-09-08',
                   'sourceMaterial':'Synthetic source material for isolated tests.',
                   'concepts':[{'id':f'c{i}','label':f'Idea {i}','goal':'Explain an idea.'} for i in range(7)],
                   'questions':[{'id':f'q{i}' if i<6 else 'q18','conceptId':f'c{i}','prompt':'Explain this synthetic idea.',
                                 'type':'rubric','rubric':'Give a relevant explanation.','required':True} for i in range(7)]}
        self.body['questions'][-1]['writingRequirements']=copy.deepcopy(RULES)
        original=copy.deepcopy(self.body);original['questions']=original['questions'][:-1];original['concepts']=original['concepts'][:-1]
        self.id=self.call('/api/assistant/practice/drafts',original,'assistant')['quizIds'][0]
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def call(self,path,body,actor='river'):
        return self.store.transaction(lambda:self.store.mutate(path,body,actor))
    def quiz(self):return next(q for q in self.store.state['practice']['quizzes'] if q['id']==self.id)
    def update(self):return self.call('/api/assistant/practice/drafts/'+self.id,dict(self.body,expectedRevision=self.quiz()['revision']),'assistant')
    def test_update_preserves_identity_original_six_and_unpublished_state(self):
        before=copy.deepcopy(self.quiz()['questions']);history=len(self.quiz()['history'])
        self.update();q=self.quiz()
        self.assertEqual(q['questions'][:6],before)
        self.assertEqual(len(q['questions']),7);self.assertTrue(all(x['required'] for x in q['questions']))
        self.assertEqual(q['questions'][-1]['writingRequirements'],RULES)
        self.assertFalse(q['published']);self.assertIsNone(q['taskId']);self.assertEqual(len(q['history']),history+1)
        self.assertEqual(len(self.store.state['practice']['quizzes']),1)
    def test_updates_require_assistant_ownership_fresh_revision_and_no_publication(self):
        route='/api/assistant/practice/drafts/'+self.id
        for actor in ('river','parent'):
            with self.assertRaises((AppError,PracticeError)):self.call(route,dict(self.body,expectedRevision=1),actor)
        self.update()
        with self.assertRaises(PracticeError):self.call(route,dict(self.body,expectedRevision=1),'assistant')
        self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'reviewed':True},'parent')
        with self.assertRaises(PracticeError):self.update()
    def test_only_writing_question_retries_and_eight_sentence_gate_blocks_completion(self):
        self.update();self.call('/api/parent/practice/quizzes/'+self.id+'/publish',{'reviewed':True},'parent')
        for q in self.quiz()['questions'][:6]:
            self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':q['id'],'answer':'Synthetic response.'})
            a=self.store.state['practice']['attempts'][-1]
            self.call('/api/assistant/practice/attempts/'+a['id']+'/review',{'correct':True,'rubricEvidence':'Synthetic review.'},'assistant')
        data=assessment(7);answer=' '.join(r['text'] for r in data['sentences'])
        self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':'q18','answer':answer})
        a=self.store.state['practice']['attempts'][-1]
        route='/api/assistant/practice/attempts/'+a['id']+'/review'
        with self.assertRaises(PracticeError):self.call(route,{'correct':True,'rubricEvidence':'Synthetic review.','writingReview':data},'assistant')
        self.call(route,{'correct':False,'rubricEvidence':'Synthetic short response.','writingReview':data,'revisionGoals':['Add one relevant explanation using your reading.']},'assistant')
        rows=practice_state(self.store,'river')['quizzes'][0]['mastery']['questions']
        self.assertEqual([r['status'] for r in rows],['approved']*6+['retry'])
        self.assertIn('7 of 8',rows[-1]['responses'][-1]['feedback'])
        with self.assertRaises(PracticeError):self.call('/api/parent/practice/mastery/'+self.id+'/confirm',{},'parent')
        data=assessment();self.call('/api/practice/mastery/'+self.id+'/answer',{'questionId':'q18','answer':' '.join(r['text'] for r in data['sentences'])})
        a=self.store.state['practice']['attempts'][-1]
        self.call('/api/assistant/practice/attempts/'+a['id']+'/review',{'correct':True,'rubricEvidence':'Synthetic eight-sentence review.','writingReview':data},'assistant')
        self.call('/api/tasks/'+self.quiz()['taskId']+'/report',{},'river')
        self.call('/api/parent/practice/mastery/'+self.id+'/confirm',{},'parent')
        self.assertIsNotNone(self.store.task(self.quiz()['taskId'])['completedAt'])
