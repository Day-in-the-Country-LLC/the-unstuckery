"""Opt-in assistant management without parent or student impersonation.

managerRequests stores idempotency digests/results; managerAudit records the
credential ID, request, reason and outcome. Both are private JSON state fields.
No SQL migration is required. Tokens are separate owner-only files, never state.
"""
import copy
import hashlib
import json
import os
import re
import secrets

from practice import PracticeError, init_practice, practice_state, _quiz

PREFIX = '/api/manager'
CAPABILITIES = ['read_plan', 'read_practice', 'read_review_diagnostics', 'create_exercises',
                'edit_unpublished_exercises', 'publish_exercises', 'create_school_tasks',
                'adjust_task_plan', 'correct_work_time', 'retry_failed_reviews', 'read_audit']
TASK_FIELDS = {'title', 'subject', 'instructions', 'planDate', 'dueDate', 'plannedMinutes', 'sourceUrl', 'sourceNote'}


def init_manager(store):
    store.manager_token = None
    store.manager_key_id = None
    if os.environ.get('UNSTUCKERY_MANAGER_ENABLED') != '1':
        return
    path = store.data_dir / '.manager-token'
    if path.is_symlink():
        raise ValueError('The manager key must be a private regular file.')
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32))
    token = path.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
        raise ValueError('The private manager key is malformed.')
    path.chmod(0o600)
    store.manager_token = token
    store.manager_key_id = hashlib.sha256(token.encode()).hexdigest()[:16]


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def allowed_request(method, path):
    if method == 'GET':
        return path in (PREFIX+'/state', PREFIX+'/practice', PREFIX+'/reviews', PREFIX+'/audit')
    return method == 'POST' and bool(re.fullmatch(
        PREFIX+r'/(?:check|tasks(?:/[a-zA-Z0-9_-]+)?|school-clock/correct|reviews/[a-zA-Z0-9_-]+/retry|'
        r'practice/(?:drafts(?:/[a-zA-Z0-9_-]+)?|quizzes/[a-zA-Z0-9_-]+/publish))', path))


def plan_revision(store):
    return revision([{k:t.get(k) for k in ('id','planDate','plannedMinutes')} for t in store.state['tasks']])


def task_view(store, task):
    return dict(store.view_task(task), managerRevision=revision(task))


def view(store, path, day=None):
    from server import valid_date
    if path == PREFIX+'/practice':
        return practice_state(store, 'manager')
    if path == PREFIX+'/audit':
        return {'events': copy.deepcopy(store.state.get('managerAudit', [])), 'credentialId':store.manager_key_id}
    if path == PREFIX+'/reviews':
        from ai_review import public_status
        result = public_status(store)
        keys = ('attemptId','quizId','questionId','kind','status','message','tries','createdAt','completedAt',
                'reviewIssue','reviewMode','decisionReview','decisionRechecks')
        result['jobs'] = {ident:dict({k:copy.deepcopy(j.get(k)) for k in keys}, managerRevision=revision(j))
                          for ident,j in store.state.get('aiReviewJobs',{}).items()}
        return result
    selected = valid_date(day or store.today())
    clock = store.school_clock_view(store.day(selected)['schoolClock'])
    clock['managerRevision'] = revision(store.day(selected)['schoolClock'])
    tasks = [task_view(store,t) for t in store.state['tasks'] if t['kind']=='school']
    return {'date':selected, 'today':store.today(), 'schoolMinutes':90, 'schoolClock':clock,
            'tasks':tasks, 'planRevision':plan_revision(store),
            'plannedMinutes':sum(t['plannedMinutes'] for t in tasks if t['planDate']==selected),
            'capabilities':CAPABILITIES, 'credentialId':store.manager_key_id}


def expect(body, value, key='expectedRevision'):
    if body.get(key) != revision(value):
        raise PracticeError('This item changed. Read its current manager revision before updating it.',409)


def within_budget(store, day, minutes, exclude=None):
    planned = sum(t.get('plannedMinutes',25) for t in store.state['tasks']
                  if t['kind']=='school' and t['planDate']==day and t['id']!=exclude)
    if planned + minutes > 90:
        raise PracticeError('This change would exceed the 90-minute daily plan.',409)


def mutate(store, path, body, actor):
    if actor != 'manager' or not store.manager_token or not allowed_request('POST', path):
        raise PracticeError('This action needs enabled assistant manager access.',403)
    if not isinstance(body,dict):
        raise PracticeError('Use an update object.')
    request_id = body.get('requestId')
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', request_id):
        raise PracticeError('Include a unique requestId; retry only with identical contents.')
    from server import required
    reason = required(body,'reason',2000)
    digest = revision({'path':path,'body':body})
    requests = store.state.setdefault('managerRequests',{})
    prior = requests.get(request_id)
    if prior:
        if prior['digest'] != digest:
            raise PracticeError('This requestId belongs to a different update.',409)
        return dict(copy.deepcopy(prior['result']), reused=True)
    fields = {k:copy.deepcopy(v) for k,v in body.items() if k not in ('requestId','reason')}
    result = _apply(store,path,fields,reason,request_id)
    result = dict(result, requestId=request_id, reused=False)
    requests[request_id] = {'digest':digest,'result':copy.deepcopy(result)}
    store.state.setdefault('managerAudit',[]).append({'at':store.stamp(),'actor':'manager',
        'credentialId':store.manager_key_id,'requestId':request_id,'action':path,'reason':reason,
        'result':{k:result[k] for k in ('taskId','quizId','attemptId','removedMinutes') if k in result}})
    return result


def _apply(store,path,body,reason,request_id):
    from server import required, valid_date
    if path == PREFIX+'/check':
        if body:raise PracticeError('An access check accepts only requestId and reason.')
        return {'ok':True,'actor':'manager','credentialId':store.manager_key_id,'capabilities':CAPABILITIES}
    if path.startswith(PREFIX+'/practice/'):
        from planner import mutate_exercise
        # The shared exercise workflow receives the real actor, never parent authority.
        return mutate_exercise(store,path.replace(PREFIX,'/api/planner',1),body,'manager')
    if path == PREFIX+'/school-clock/correct':
        if set(body)-{'date','removeMinutes','expectedRevision'}:
            raise PracticeError('Use date, removeMinutes, and expectedRevision for a timer correction.')
        day = valid_date(body.get('date',store.today()))
        clock = store.day(day)['schoolClock']
        store.school_clock_view(clock)
        expect(body,clock)
        from school_timer import correct
        result=correct(store,{'date':day,'removeMinutes':body.get('removeMinutes'),'note':reason},actor='manager')
        return {'ok':True,'removedMinutes':body['removeMinutes'],'schoolClock':dict(result,managerRevision=revision(clock))}
    if path == PREFIX+'/tasks':
        allowed=TASK_FIELDS|{'scope','assignmentId','criteria','expectedPlanRevision'}
        if set(body)-allowed:raise PracticeError('Unsupported task field.')
        if body.get('expectedPlanRevision') != plan_revision(store):
            raise PracticeError('The work plan changed. Read it again before adding a task.',409)
        if body.get('scope') not in ('assignment','work_block'):
            raise PracticeError('Use the exercise draft workflow for in-app extra practice.')
        required(body,'sourceNote')
        fields=store.school_fields(dict(body,requirements={'parent':True}))
        if fields['planDate']<store.today():raise PracticeError('Plan new work for today or a future date.')
        within_budget(store,fields['planDate'],fields['plannedMinutes'])
        task=store.create_task(fields,actor='manager')
        return {'ok':True,'taskId':task['id'],'task':task_view(store,store.task(task['id']))}
    match=re.fullmatch(PREFIX+r'/tasks/([a-zA-Z0-9_-]+)',path)
    if match:
        if set(body)-{'changes','expectedRevision'}:raise PracticeError('Use changes and expectedRevision.')
        task=store.task(match[1]);expect(body,task)
        if task['kind']!='school' or any(task.get(k) for k in ('completedAt','parentConfirmed','reportedAt')):
            raise PracticeError('Manager adjustments are for unfinished schoolwork, not completion approvals.',409)
        changes=body.get('changes')
        if not isinstance(changes,dict) or not changes or set(changes)-TASK_FIELDS:
            raise PracticeError('Change only task wording, materials, dates, or planned minutes.')
        fields=store.school_fields(dict(task,**changes))
        if fields['planDate']<store.today() and fields['planDate']!=task['planDate']:
            raise PracticeError('Move work to today or a future date.')
        within_budget(store,fields['planDate'],fields['plannedMinutes'],task['id'])
        before={k:copy.deepcopy(task.get(k)) for k in changes}
        task.update({k:fields[k] for k in changes})
        if before.get('planDate',task['planDate'])!=task['planDate']:
            from rewards import task_planned
            task_planned(store,task,before['planDate'],actor='manager')
            if task.get('practiceQuizId'):
                quiz=_quiz(store,task['practiceQuizId'])
                quiz['planDate']=task['planDate']
                store.history(quiz,'manager','practice_rescheduled',{'from':before['planDate'],'to':task['planDate'],'reason':reason})
        store.history(task,'manager','plan_adjusted',{'before':before,'after':changes,'reason':reason})
        return {'ok':True,'taskId':task['id'],'task':task_view(store,task)}
    match=re.fullmatch(PREFIX+r'/reviews/([a-zA-Z0-9_-]+)/retry',path)
    if match:
        if set(body)-{'expectedRevision'}:raise PracticeError('Retry accepts only expectedRevision.')
        from ai_review import jobs,retry
        job=jobs(store).get(match[1])
        if not job:raise PracticeError('That review was not found.',404)
        expect(body,job)
        if job['status']!='failed':raise PracticeError('Only a failed saved review can be retried.',409)
        retry(store,job['quizId'],{'attemptId':match[1]},'manager')
        store.history(job,'manager','review_retry_requested',{'reason':reason,'requestId':request_id})
        return {'ok':True,'attemptId':match[1],'status':job['status']}
    raise PracticeError('Unsupported manager update.',403)
