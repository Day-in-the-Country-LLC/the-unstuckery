"""Opt-in exercise planning API. No student, grading, or parent privileges.

plannerRequests stores request digests and quiz IDs in the existing JSON state
for durable, idempotent draft creation. No database/schema migration is needed.
"""
import copy
import hashlib
import json
import os
import re
import secrets

from practice import PracticeError, _add_drafts, _fields, _history, _publishable, _quiz, init_practice, practice_state


def init_planner(store):
    store.planner_token = None
    if os.environ.get('UNSTUCKERY_PLANNER_ENABLED') != '1':
        return
    path = store.data_dir / '.planner-token'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32))
    token = path.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
        raise ValueError('The private planner token is malformed.')
    path.chmod(0o600)
    store.planner_token = token


def allowed_request(method, path):
    if method == 'GET':
        return path in ('/api/planner/state', '/api/planner/practice')
    return method == 'POST' and bool(re.fullmatch(
        r'/api/planner/practice/(?:drafts(?:/[a-zA-Z0-9_-]+)?|quizzes/[a-zA-Z0-9_-]+/publish)', path))


def view(store, path, day=None):
    if path == '/api/planner/practice':
        return practice_state(store, 'planner')
    from server import valid_date
    selected = valid_date(day or store.today())
    keys = ('id', 'title', 'subject', 'scope', 'planDate', 'plannedMinutes',
            'practiceQuizId', 'reportedAt', 'completedAt')
    tasks = [{k: copy.deepcopy(t.get(k)) for k in keys}
             for t in store.state['tasks'] if t['kind'] == 'school']
    return {'date': selected, 'today': store.today(), 'schoolMinutes': 90,
            'plannedMinutes': sum(t['plannedMinutes'] for t in tasks if t['planDate'] == selected),
            'tasks': tasks, 'capabilities': ['read_practice', 'create_drafts', 'edit_own_unpublished_drafts', 'publish_own_practice']}


def _prepared(body, today):
    fields = _fields(body, today)
    if fields['reviewMode'] != 'mastery' or not any(q['required'] for q in fields['questions']):
        raise PracticeError('Planner exercises need assistant-reviewed responses and at least one required step.')
    _publishable(fields)
    return fields


def mutate(store, path, body, actor):
    if actor != 'planner' or not store.planner_token:
        raise PracticeError('This action needs enabled planner access.', 403)
    if not allowed_request('POST', path):
        raise PracticeError('Planner access is limited to exercise preparation and publication.', 403)
    data = init_practice(store)
    if path == '/api/planner/practice/drafts':
        request_id = body.get('requestId')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', request_id):
            raise PracticeError('Include a unique requestId; reuse it only when retrying this same draft.')
        fields = _prepared(body, store.today())
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        requests = store.state.setdefault('plannerRequests', {})
        prior = requests.get(request_id)
        if prior:
            if prior['digest'] != digest:
                raise PracticeError('This requestId already belongs to different draft content.', 409)
            quiz = _quiz(store, prior['quizId'])
            return {'ok': True, 'quizId': quiz['id'], 'revision': quiz['revision'], 'reused': True}
        result = _add_drafts(store, fields, 'planner')
        ident = result['quizIds'][0]
        requests[request_id] = {'digest': digest, 'quizId': ident}
        return {'ok': True, 'quizId': ident, 'revision': 1, 'reused': False}

    match = re.fullmatch(r'/api/planner/practice/(drafts|quizzes)/([a-zA-Z0-9_-]+)(/publish)?', path)
    quiz = _quiz(store, match[2])
    if quiz.get('createdBy') != 'planner':
        raise PracticeError('Planner access can change only exercises created through this planner API.', 403)
    if type(body.get('expectedRevision')) is not int or body['expectedRevision'] != quiz['revision']:
        raise PracticeError('This exercise changed. Read its current revision before continuing.', 409)
    if match[1] == 'drafts':
        if quiz['published'] or quiz.get('taskId') or any(a['quizId'] == quiz['id'] for a in data['attempts']):
            raise PracticeError('Only an unpublished, unstarted planner draft can be edited.', 409)
        fields = _prepared(body, store.today())
        before = {k: copy.deepcopy(quiz.get(k)) for k in fields}
        quiz.update(fields, revision=quiz['revision'] + 1, reviewedAt=None)
        _history(store, quiz, actor, 'quiz_draft_updated', {'before': before, 'after': fields})
        return {'ok': True, 'quizId': quiz['id'], 'revision': quiz['revision'], 'published': False}

    if set(body) - {'expectedRevision', 'plannedMinutes', 'instructions'}:
        raise PracticeError('Publication accepts only expectedRevision, plannedMinutes, and instructions.')
    minutes = body.get('plannedMinutes')
    if type(minutes) is not int or not 1 <= minutes <= 90:
        raise PracticeError('Choose a practice allowance between 1 and 90 minutes.')
    from server import required
    instructions = required(body, 'instructions')
    publication = {'plannedMinutes': minutes, 'instructions': instructions}
    if quiz['published']:
        if quiz.get('plannerPublication') != publication:
            raise PracticeError('This exercise is already published. Its saved work cannot be changed here.', 409)
        return {'ok': True, 'quizId': quiz['id'], 'taskId': quiz['taskId'], 'published': True, 'reused': True}
    if quiz.get('taskId') or any(a['quizId'] == quiz['id'] for a in data['attempts']):
        raise PracticeError('An exercise with existing work needs parent review before republication.', 409)
    if quiz['planDate'] < store.today():
        raise PracticeError('Publish new practice for today or a future date, not a past date.')
    planned = sum(t.get('plannedMinutes', 25) for t in store.state['tasks']
                  if t['kind'] == 'school' and t['planDate'] == quiz['planDate'])
    if planned + minutes > 90:
        raise PracticeError('This practice would exceed the 90-minute daily plan. Choose another date or allowance.', 409)
    _publishable(quiz)
    quiz.update(published=True, reviewedAt=store.stamp(), publishedBy='planner', plannerPublication=publication)
    task = store.create_task({
        'title': quiz['title'], 'subject': quiz['subject'], 'planDate': quiz['planDate'],
        'scope': 'practice', 'plannedMinutes': minutes, 'instructions': instructions,
        'sourceUrl': quiz['sourceUrl'], 'sourceNote': quiz['sourceNote'],
        'criteria': 'Extra practice — complete in this app. All required responses need tutor approval, then student readiness and parent confirmation. A finished work block does not finish the exercise or submit schoolwork.',
        'requirements': {'understanding': False, 'submission': False, 'parent': True}}, actor='planner')
    quiz['taskId'] = task['id']
    store.task(task['id'])['practiceQuizId'] = quiz['id']
    _history(store, quiz, actor, 'quiz_published', {'revision': quiz['revision'], 'taskId': task['id'], 'delegated': True})
    return {'ok': True, 'quizId': quiz['id'], 'taskId': task['id'], 'published': True, 'reused': False}
