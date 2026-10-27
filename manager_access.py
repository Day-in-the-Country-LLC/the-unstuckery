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
                'adjust_task_plan', 'remove_unfinished_steps', 'correct_work_time', 'retry_failed_reviews', 'read_audit']
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
        r'practice/(?:drafts(?:/[a-zA-Z0-9_-]+)?|quizzes/[a-zA-Z0-9_-]+/(?:publish|remove-steps)))', path))


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
        'result':{k:result[k] for k in ('taskId','quizId','attemptId','removedMinutes','removedQuestionIds') if k in result}})
    return result


def _apply(store,path,body,reason,request_id):
    from server import required, valid_date
    if path == PREFIX+'/check':
        if body:raise PracticeError('An access check accepts only requestId and reason.')
        return {'ok':True,'actor':'manager','credentialId':store.manager_key_id,'capabilities':CAPABILITIES}
    match=re.fullmatch(PREFIX+r'/practice/quizzes/([a-zA-Z0-9_-]+)/remove-steps',path)
    if match:
        return remove_steps(store,match[1],body,reason,request_id)
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


def remove_steps(store, quiz_id, body, reason, request_id):
    """Archive a withdrawn requirement without grading or rewriting saved work.

    Only an unfinished sequential exercise with no later saved responses can be
    changed. Surviving question versions and sequence contexts stay intact.
    """
    from mastery import status, progress, latest_attempts
    from practice import _valid_attempts
    if set(body)-{'expectedRevision','questionIds'}:
        raise PracticeError('Step removal accepts only expectedRevision and questionIds.')
    quiz=_quiz(store,quiz_id)
    if type(body.get('expectedRevision')) is not int or body['expectedRevision']!=quiz['revision']:
        raise PracticeError('This exercise changed. Read its current revision before removing steps.',409)
    ids=body.get('questionIds')
    if (not isinstance(ids,list) or not 1<=len(ids)<=20
            or any(not isinstance(i,str) or not re.fullmatch(r'[a-zA-Z0-9_-]+',i) for i in ids)
            or len(set(ids))!=len(ids)):
        raise PracticeError('Provide distinct questionIds to remove.')
    if not quiz['published'] or quiz.get('reviewMode')!='mastery' or not quiz.get('sequential') or not quiz.get('taskId'):
        raise PracticeError('Step removal is for published, sequential mastery practice.',409)
    task=store.task(quiz['taskId'])
    if quiz.get('parentConfirmed') or any(task.get(k) for k in ('reportedAt','parentConfirmed','completedAt')):
        raise PracticeError('Completed or reported-ready practice cannot be changed here.',409)
    questions=quiz['questions'];chosen=set(ids)
    if not chosen.issubset({q['id'] for q in questions}):
        raise PracticeError('A selected step is no longer in this exercise.',409)
    keep=[q for q in questions if q['id'] not in chosen]
    if not any(q['required'] for q in keep):
        raise PracticeError('Keep at least one required practice step.',409)
    rows=status(store,quiz)['questions']
    if any(r['questionId'] in chosen and r['status'] in ('approved','revisit','scheduled_review') for r in rows):
        raise PracticeError('Remove only unfinished steps.',409)
    latest=latest_attempts(store,quiz)
    for ident in chosen:
        attempt=latest.get(ident)
        if (attempt and attempt['grade']=='pending'
                and store.state.get('aiReviewJobs',{}).get(attempt['id'],{}).get('status')!='failed'):
            raise PracticeError('Wait for the pending review before removing this step.',409)
    from ai_review import ACTIVE
    if any(j.get('quizId')==quiz_id and j.get('status') in ACTIVE for j in store.state.get('aiReviewJobs',{}).values()):
        raise PracticeError('Wait for this exercise’s active AI review before removing a step.',409)
    first=min(i for i,q in enumerate(questions) if q['id'] in chosen)
    later={q['id'] for q in questions[first:] if q['id'] not in chosen}
    if any(a['questionId'] in later for a in _valid_attempts(store,quiz)):
        raise PracticeError('Later steps already have saved responses; their sequence cannot be changed here.',409)
    at=store.stamp()
    quiz.setdefault('archivedSteps',[]).extend({'question':copy.deepcopy(q),'position':i+1,
        'removedAt':at,'removedBy':'manager','reason':reason,'requestId':request_id}
        for i,q in enumerate(questions) if q['id'] in chosen)
    concept_ids={q['conceptId'] for q in keep}
    quiz.setdefault('archivedConcepts',[]).extend(copy.deepcopy(c) for c in quiz['concepts'] if c['id'] not in concept_ids)
    quiz['concepts']=[c for c in quiz['concepts'] if c['id'] in concept_ids]
    quiz['questions']=keep
    quiz['revision']+=1
    # Shared background can describe the original sequence. Keep it for history,
    # while making the parent's withdrawal explicit to future tutor/review calls.
    prompts='; '.join(q['prompt'] for q in questions if q['id'] in chosen)
    amendment=(f'\n\nParent-requested adjustment ({at}): Removed from this exercise: {prompts}\n'
               f'Reason: {reason}\nThere are now {len(keep)} active steps. Removed requirements are no longer '
               'part of this practice, including where older background describes them. Review and tutor '
               'only the remaining current step; do not ask the learner to complete a removed requirement.')
    quiz['sourceMaterial']+=amendment
    quiz['sourceNote']+=f'\n\nAdjustment ({at}): {len(ids)} unfinished step(s) removed at parent request; {len(keep)} active steps remain. {reason}'
    details={'questionIds':ids,'reason':reason,'requestId':request_id,'revision':quiz['revision']}
    store.history(quiz,'manager','practice_steps_removed',details)
    store.history(task,'manager','practice_steps_removed',dict(details,quizId=quiz_id))
    remaining=status(store,quiz)['questions']
    return {'ok':True,'quizId':quiz_id,'taskId':task['id'],'revision':quiz['revision'],
            'removedQuestionIds':ids,'progress':progress(store,quiz)[0],
            'nextQuestionId':next((r['questionId'] for r in remaining if r['status'] not in ('approved','scheduled_review')),None)}
