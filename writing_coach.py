"""Saved writing expectations and conservative progression from recorded assessments.

writingCoach holds the current level/revision, automatic flag, and change history.
Each started mastery quiz freezes a writingPolicy snapshot. Writing attempts may
hold writingMechanics: reviewer judgments, not a separate linguistic validator.
Only first-attempt independent writing evidence from the current policy revision
can advance future quizzes; existing quiz policies and approvals are never changed.
"""
import copy
from datetime import datetime

LEVELS = {
    'gentle': {'label': 'Getting ideas across',
               'description': 'Clear meaning matters. Minor spelling, grammar, capitals, and punctuation mistakes do not block approval.',
               'focus': 'Explain your ideas clearly',
               'practiceHint': 'Read your answer once. Can someone understand what you mean without guessing?',
               'requiredSkills': ['meaningClear']},
    'capitals': {'label': 'Starting with capitals',
                 'description': 'Keep your ideas clear. The new goal is a capital letter at the start of each sentence. Other minor language slips are still okay.',
                 'focus': 'Start each sentence with a capital',
                 'practiceHint': 'Before you submit, look at the first letter of each sentence. Check the first word of your answer and the words after sentence endings.',
                 'requiredSkills': ['meaningClear', 'capitalization']},
    'building': {'label': 'Building sentence habits',
                 'description': 'Keep using capitals. The new goal is punctuation that clearly separates your sentences. Minor grammar and spelling slips are still okay.',
                 'focus': 'Show where each sentence ends',
                 'practiceHint': 'Read one thought at a time. Where does that thought finish? Check for a period, question mark, or exclamation mark there.',
                 'requiredSkills': ['meaningClear', 'sentenceBoundaries', 'capitalization']},
    'grammar': {'label': 'Checking grammar',
                'description': 'Keep your sentence habits. The new goal is mostly consistent grammar. Minor spelling slips are still okay.',
                'focus': 'Check how the words fit together',
                'practiceHint': 'Read each sentence slowly. Check who or what it is about, what they do, and whether the verb fits.',
                'requiredSkills': ['meaningClear', 'sentenceBoundaries', 'capitalization', 'grammar']},
    'polishing': {'label': 'Polishing writing',
                  'description': 'Keep your sentence and grammar habits. The new goal is mostly consistent spelling of familiar words. An occasional harmless typo is still okay.',
                  'focus': 'Check familiar words for spelling',
                  'practiceHint': 'Take one final look for familiar words that seem misspelled. Focus on those words rather than rewriting your answer.',
                  'requiredSkills': ['meaningClear', 'sentenceBoundaries', 'capitalization', 'grammar', 'spelling']},
}
SKILLS = ('meaningClear', 'sentenceBoundaries', 'capitalization', 'grammar', 'spelling')


def init(store):
    return store.state.setdefault('writingCoach', {'level': 'gentle', 'revision': 1, 'automatic': True, 'history': []})


def policy(store, quiz, freeze=False):
    if quiz.get('writingPolicy'):
        return copy.deepcopy(quiz['writingPolicy'])
    from practice import init_practice
    profile = init(store)
    # Quizzes started before this feature retain the gentle default and do not
    # supply evidence for automatic advancement under the new assessment rules.
    legacy = any(a['quizId'] == quiz['id'] for a in init_practice(store)['attempts'])
    level = 'gentle' if legacy else profile['level']
    result = dict(level=level, revision=0 if legacy else profile['revision'], **copy.deepcopy(LEVELS[level]))
    if freeze:
        quiz['writingPolicy'] = copy.deepcopy(result)
    return result


def assess(quiz, body, approved, required=False):
    from practice import PracticeError, _bool, _text
    value = body.get('writingMechanics')
    level = quiz.get('writingPolicy', {}).get('level', 'gentle')
    if value is None and not required and level == 'gentle':
        return None  # Older/manual reviews remain supported, without advancement evidence.
    if not isinstance(value, dict):
        raise PracticeError('Assess writing clarity and mechanics before saving this writing review.')
    result = {key: _bool(value.get(key), key) for key in SKILLS}
    result['coachingTip'] = _text(value.get('coachingTip', ''), 'one optional writing tip', limit=1000)
    skills = quiz.get('writingPolicy', {}).get('requiredSkills', LEVELS[level]['requiredSkills'])
    if approved and not all(result[key] for key in skills):
        raise PracticeError('The recorded writing assessment does not meet this quiz’s saved writing level.', 409)
    return result


def progress(store):
    from practice import init_practice, LOCAL_TZ
    profile = init(store)
    levels = list(LEVELS)
    index = levels.index(profile['level'])
    next_level = levels[index + 1] if index + 1 < len(levels) else None
    result = {'responses': 0, 'days': 0, 'targetResponses': 5, 'targetDays': 3,
              'nextLevel': next_level, 'ready': False}
    if not next_level:
        return result
    data = init_practice(store)
    quizzes = {q['id']: q for q in data['quizzes']}
    first, samples = set(), []
    for attempt in data['attempts']:
        key = (attempt['quizId'], attempt['questionId'])
        if key in first:
            continue
        first.add(key)
        quiz = quizzes.get(attempt['quizId'], {})
        saved = quiz.get('writingPolicy', {})
        question = next((q for q in quiz.get('questions', []) if q['id'] == attempt['questionId']), {})
        if (saved.get('revision') != profile['revision'] or saved.get('level') != profile['level']
                or not quiz.get('published') or not question.get('writingRequirements')
                or attempt['questionVersion'] != question.get('questionVersion')
                or attempt.get('grade') == 'pending' or not attempt.get('writingMechanics')):
            continue
        samples.append(attempt)
    streak = []
    ordered = sorted(enumerate(samples), key=lambda item: (item[1].get('reviewedAt') or item[1]['at'], item[0]), reverse=True)
    for _, attempt in ordered:
        if (attempt.get('grade') != 'correct' or not attempt.get('independent')
                or not all(attempt['writingMechanics'][key] for key in LEVELS[profile['level']]['requiredSkills'])):
            break
        streak.append(attempt)
        if len(streak) == 5:
            break
    days = {datetime.fromisoformat(a['at']).astimezone(LOCAL_TZ).date() for a in streak}
    result.update(responses=len(streak), days=len(days), ready=len(streak) >= 5 and len(days) >= 3)
    return result


def advance(store):
    profile, evidence = init(store), progress(store)
    if profile['automatic'] and evidence['ready']:
        previous = profile['level']
        profile.update(level=evidence['nextLevel'], revision=profile['revision'] + 1)
        profile['history'].append({'at': store.stamp(), 'actor': 'system', 'from': previous,
                                   'to': profile['level'], 'evidence': evidence})


def update(store, body):
    from practice import PracticeError, _bool
    profile = init(store)
    level = body.get('level', profile['level'])
    if not isinstance(level, str) or level not in LEVELS:
        raise PracticeError('Choose one of the five writing levels.')
    automatic = _bool(body.get('automatic', profile['automatic']), 'automatic writing progression')
    if type(body.get('expectedRevision')) is not int or body['expectedRevision'] != profile['revision']:
        raise PracticeError('Writing expectations changed. Refresh and try again.', 409)
    if level != profile['level'] or automatic != profile['automatic']:
        previous = profile['level']
        profile.update(level=level, automatic=automatic, revision=profile['revision'] + 1)
        profile['history'].append({'at': store.stamp(), 'actor': 'parent', 'from': previous,
                                   'to': level, 'automatic': automatic})
    return {'ok': True, 'writingCoach': view(store)}


def view(store):
    return dict(copy.deepcopy(init(store)), levels=copy.deepcopy(LEVELS), progress=progress(store))
