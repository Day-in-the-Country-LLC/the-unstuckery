"""Synthetic evidence: progression, frozen expectations, and actor boundaries."""
import copy
from datetime import timedelta
import unittest
import test_mastery as fixtures
from practice import PracticeError, practice_state
from server import AppError, RiverStore
from writing_coach import init, policy, progress, advance, assess, SKILLS, LEVELS


class WritingCoachTests(unittest.TestCase):
    setUp=fixtures.MasteryReviewTests.setUp
    tearDown=fixtures.MasteryReviewTests.tearDown
    call=fixtures.MasteryReviewTests.call
    answer=fixtures.MasteryReviewTests.answer
    review=fixtures.MasteryReviewTests.review

    def sample(self, index, day=0, **changes):
        quiz=copy.deepcopy(self.quiz)
        quiz.update(id='sample-'+str(index),writingPolicy=policy(self.store,self.quiz))
        q=quiz['questions'][0]
        q['writingRequirements']={'minQualifiedSentences':2,'requiredCoverage':['topic'],'expectedSentenceRange':'2'}
        self.store.state['practice']['quizzes'].append(quiz)
        at=(self.now+timedelta(days=day)).isoformat()
        attempt={'id':'a'+str(index),'quizId':quiz['id'],'questionId':q['id'],'questionVersion':q['questionVersion'],
                 'grade':'correct','independent':True,'at':at,'reviewedAt':at,
                 'writingMechanics':dict.fromkeys(SKILLS,True),**changes}
        self.store.state['practice']['attempts'].append(attempt)
        return attempt

    def test_defaults_are_gentle_and_automatic_and_public_quiz_explains_level(self):
        self.assertEqual(init(self.store)['level'],'gentle')
        self.assertTrue(init(self.store)['automatic'])
        public=practice_state(self.store)
        self.assertNotIn('writingCoach',public)
        self.assertEqual(public['quizzes'][0]['writingPolicy']['level'],'gentle')

    def test_five_independent_samples_on_three_days_advance_only_once(self):
        for step, (current, following) in enumerate(zip(list(LEVELS),list(LEVELS)[1:])):
            # He meets only the current bar; every future skill is still weak.
            mechanics={key:key in LEVELS[current]['requiredSkills'] for key in SKILLS}
            for i in range(5):self.sample(step*5+i,day=step*3+i//2,writingMechanics=mechanics.copy())
            self.assertTrue(progress(self.store)['ready'])
            advance(self.store)
            self.assertEqual(init(self.store)['level'],following)
            self.assertEqual(progress(self.store)['responses'],0)
            advance(self.store)
            self.assertEqual(init(self.store)['level'],following)
            self.assertEqual(len(set(LEVELS[following]['requiredSkills'])-set(LEVELS[current]['requiredSkills'])),1)
        self.assertIsNone(progress(self.store)['nextLevel'])

    def test_same_day_or_unready_mechanics_cannot_advance(self):
        for i in range(5): self.sample(i)
        self.assertFalse(progress(self.store)['ready'])
        self.store.state['practice']['attempts'][-1]['writingMechanics']['meaningClear']=False
        self.assertEqual(progress(self.store)['responses'],0)

    def test_support_and_retries_do_not_count_as_independent_first_attempts(self):
        for i in range(4):self.sample(i,day=i)
        first=self.sample(4,day=4,independent=False,grade='incorrect')
        retry=copy.deepcopy(first);retry.update(id='retry',independent=True,grade='correct')
        self.store.state['practice']['attempts'].append(retry)
        advance(self.store)
        self.assertEqual(init(self.store)['level'],'gentle')
        self.assertEqual(progress(self.store)['responses'],0)

    def test_started_quiz_stays_frozen_through_parent_change_and_restart(self):
        self.answer(1)
        self.call('/api/parent/practice/writing',{'level':'polishing','automatic':False,'expectedRevision':1},'parent')
        self.assertEqual(policy(self.store,self.quiz)['level'],'gentle')
        q=copy.deepcopy(self.quiz);q['id']='new';q.pop('writingPolicy')
        self.assertEqual(policy(self.store,q)['level'],'polishing')
        self.store.db.close();self.store=RiverStore(self.tmp.name,now=lambda:self.now)
        self.assertEqual(self.store.state['writingCoach']['level'],'polishing')
        self.assertEqual(self.store.state['practice']['quizzes'][0]['writingPolicy']['level'],'gentle')

    def test_legacy_started_quiz_does_not_inherit_stricter_level(self):
        self.answer(1);self.quiz.pop('writingPolicy')
        init(self.store).update(level='polishing',revision=2)
        self.assertEqual(policy(self.store,self.quiz)['level'],'gentle')
        self.assertEqual(policy(self.store,self.quiz)['revision'],0)

    def test_parent_control_is_authorized_validated_and_cannot_be_stale(self):
        body={'level':'building','automatic':False,'expectedRevision':1}
        for actor in ('river','assistant'):
            with self.assertRaises((AppError,PracticeError)):self.call('/api/parent/practice/writing',body,actor)
        for field,value in [('level','unknown'),('automatic','yes'),('expectedRevision',99)]:
            with self.assertRaises(PracticeError):self.call('/api/parent/practice/writing',dict(body,**{field:value}),'parent')
        self.call('/api/parent/practice/writing',body,'parent')
        with self.assertRaises(PracticeError):self.call('/api/parent/practice/writing',body,'parent')
        for i in range(5):self.sample(i,day=i)
        advance(self.store)
        self.assertEqual(init(self.store)['level'],'building')

    def test_gentle_accepts_minor_mechanics_but_stricter_level_enforces_recorded_skills(self):
        mechanics=dict.fromkeys(SKILLS,False);mechanics['meaningClear']=True
        body={'writingMechanics':mechanics}
        self.assertIsNotNone(assess({'writingPolicy':{'level':'gentle'}},body,True))
        for level in ('building','polishing'):
            with self.assertRaises(PracticeError):assess({'writingPolicy':{'level':level}},body,True)
        mechanics['capitalization']=True
        self.assertIsNotNone(assess({'writingPolicy':LEVELS['capitals'] | {'level':'capitals'}},body,True))
        with self.assertRaises(PracticeError):assess({'writingPolicy':LEVELS['building'] | {'level':'building'}},body,True)
        with self.assertRaises(PracticeError):assess({}, {}, True, required=True)
        self.assertIsNone(assess({}, {}, True))

    def test_later_quiz_setting_change_does_not_change_queued_context(self):
        from ai_review import context, jobs
        self.store.ai_review_enabled=True
        a=self.answer(1)
        before=context(self.store,jobs(self.store)[a['id']])[-1]
        self.call('/api/parent/practice/writing',{'level':'building','expectedRevision':1},'parent')
        self.assertEqual(before,context(self.store,jobs(self.store)[a['id']])[-1])

    def test_gentle_review_saves_optional_tip_and_honest_weak_skills_without_blocking_approval(self):
        self.quiz['questions'][0]['writingRequirements']={'minQualifiedSentences':2,'requiredCoverage':['rain'],'expectedSentenceRange':'2'}
        sentences=['rain waters plants.', 'a bucket stores water.']
        a=self.answer(1,answer=' '.join(sentences))
        mechanics=dict.fromkeys(SKILLS,True);mechanics.update(capitalization=False,coachingTip='Optional: look for a capital at the start of each sentence next time.')
        writing={'sentences':[dict(number=i,text=text,complete=True,relevant=True,accurate=True,addsNewIdea=True) for i,text in enumerate(sentences,1)],'coverage':{'rain':True},'evidenceExplained':True}
        self.review(a,writingReview=writing,writingMechanics=mechanics)
        self.assertEqual(a['grade'],'correct')
        self.assertFalse(a['writingMechanics']['capitalization'])
        self.assertIn('Optional:',a['feedback'])
        self.assertEqual(progress(self.store)['responses'],1)
