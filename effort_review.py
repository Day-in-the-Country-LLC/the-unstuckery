"""Opt-in evening effort evidence, separate from correctness and school completion."""
import os
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

STOP = "You have made three real efforts tonight. Please stop for the night. Your paragraph and feedback are saved. It does not need to be perfect."

def attempts(store, quiz, question):
    from practice import _valid_attempts
    return [a for a in _valid_attempts(store, quiz) if a['questionId'] == question['id']]

def day(attempt):
    return datetime.fromisoformat(attempt['at']).astimezone(ZoneInfo(os.environ.get('UNSTUCKERY_TIMEZONE', 'America/New_York'))).date().isoformat()

def view(store, quiz, question, date=None):
    date = date or store.today()
    count = sum(a.get('effortReview', {}).get('counts', False) for a in attempts(store, quiz, question) if day(a) == date)
    return {'date': date, 'count': count, 'target': 3, 'stopForTonight': count >= 3, 'message': STOP if count >= 3 else 'A sincere first draft counts. Revisions count when you try the feedback, even if they are imperfect.'}

def context(store, quiz, question, attempt):
    history = [a for a in attempts(store, quiz, question) if a['id'] != attempt['id']]
    counted = [a for a in history if a.get('effortReview', {}).get('counts') and day(a) == day(attempt)]
    return {'policy': 'three-genuine-efforts', 'evening': view(store, quiz, question, day(attempt)),
            'firstEffortTonight': not counted,
            'lastCountedEffort': {k: copy.deepcopy(counted[-1].get(k)) for k in ('answer', 'feedback', 'effortReview')} if counted else None,
            'previousResponse': {k: copy.deepcopy(history[-1].get(k)) for k in ('answer', 'feedback', 'effortReview')} if history else None}

def assess(store, quiz, question, attempt, body):
    from practice import PracticeError, _text, _norm
    value = body.get('effortReview')
    if not isinstance(value, dict) or type(value.get('counts')) is not bool:
        raise PracticeError('Include a separate genuine-effort assessment.')
    reason = _text(value.get('reason', ''), 'effort evidence', True, 2000)
    counts = value['counts']
    earlier = [a for a in attempts(store, quiz, question) if a['id'] != attempt['id'] and day(a) == day(attempt)]
    if any(_norm(a['answer']) == _norm(attempt['answer']) for a in earlier):
        counts = False
        reason = 'This response repeats writing already saved tonight.'
    return {'counts': counts, 'reason': reason, 'date': day(attempt)}
