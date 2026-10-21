"""Opt-in Saturday catch-up with original assignments and dated evidence intact."""
import copy
from datetime import date, timedelta


def policy(store, selected):
    from rewards import init_rewards, period
    r = init_rewards(store)
    start, end = period(selected, r['settings'])
    supported = r['settings']['weekStart'] == 0 and all(d < 5 for d in r['settings']['qualifyingDays'])
    saved = r.get('weeks', {}).get(start, {})
    saturday = (date.fromisoformat(start) + timedelta(days=5)).isoformat()
    return {'enabled': supported and saved.get('saturdayCatchup', False),
            'supported': supported, 'start': start, 'end': end, 'deadlineDate': saturday,
            'closed': store.today() > saturday}


def configure(store, body):
    from rewards import init_rewards, day_string, fail
    day = day_string(body.get('date', store.today()))
    p = policy(store, day)
    if not p['supported']:
        fail('Saturday catch-up uses a Monday-start week with Monday–Friday qualifying days.')
    enabled = body.get('enabled')
    if type(enabled) is not bool:
        fail('Choose whether Saturday catch-up is enabled.')
    r = init_rewards(store)
    week = r.setdefault('weeks', {}).setdefault(p['start'], {})
    before = copy.deepcopy(week)
    week.update(saturdayCatchup=enabled)
    if before != week:
        store.history(r, 'parent', 'saturday_catchup_changed', {'week':p['start'], 'enabled':enabled})
    return {'ok': True, 'catchup': policy(store, day)}


def reported_in_time(store, task, deadline):
    from rewards import evidence_day
    reported = evidence_day(task.get('reportedAt'))
    worked_ahead = task.get('kind') == 'school' and task.get('reportedPlanDate') == task['planDate']
    return bool(reported and (task['planDate'] <= reported or worked_ahead) and reported <= min(deadline, store.today()))


def ready_by_deadline(store, task, deadline):
    if not reported_in_time(store, task, deadline):
        return False
    from rewards import evidence_day
    if task.get('requirements',{}).get('submission') and task.get('submission'):
        observed = evidence_day(task['submission'].get('observedAt'))
        if not observed or observed > deadline:
            return False
    if task.get('practiceQuizId'):
        # Later grading may verify Saturday answers; Sunday answers cannot earn last week's prize.
        from practice import practice_task_review
        prior = copy.copy(store)
        prior.state = copy.deepcopy(store.state)
        prior.state.setdefault('practice', {}).setdefault('attempts', [])
        prior.state['practice']['attempts'] = [a for a in prior.state['practice']['attempts']
            if a.get('quizId') != task['practiceQuizId'] or (evidence_day(a) and evidence_day(a) <= deadline)]
        if practice_task_review(prior, task):
            return False
    return True


def school_done(store, task, deadline):
    return bool(task.get('completedAt') and not store.view_task(task)['needsReview'] and ready_by_deadline(store, task, deadline))


def summary(store, selected, days):
    """Check every assigned weekday task, including one left out of a day checklist."""
    p = policy(store, selected)
    p.update(items=[], complete=False, missedDeadline=False)
    if not p['enabled']:
        return p
    weekday_days = [d for d in days if d['date'] < p['deadlineDate']]
    expected = {key for d in weekday_days if not d['holiday'] for key in d['expectedSchoolIds']}
    all_tasks = store.state['tasks']
    for task in all_tasks:
        assigned = p['start'] <= task['planDate'] < p['deadlineDate']
        if not assigned and task['id'] not in expected:
            continue
        item_id = 'school:' + task['id']
        relevant_days = [d for d in weekday_days if d['date'] == task['planDate'] or task['id'] in d['expectedSchoolIds']]
        if relevant_days and all(d['holiday'] or item_id in d['excusedItems'] for d in relevant_days):
            continue
        timely = ready_by_deadline(store,task,p['deadlineDate'])
        done = school_done(store, task, p['deadlineDate'])
        status = 'complete' if done else 'awaiting_review' if timely else 'missed_deadline' if p['closed'] else 'in_progress'
        p['items'].append({'id':item_id, 'taskId':task['id'], 'title':task['title'], 'kind':task['kind'],
                           'planDate':task['planDate'], 'status':status,
                           'reason':'Finished with required checks.' if done else 'Reported by Saturday; required checks or final checkoff are still needed.' if timely else 'Schoolwork or quiz responses were not finished before Sunday.' if p['closed'] else 'Finish and report ready by Saturday night.'})
    p['complete'] = all(i['status'] == 'complete' for i in p['items'])
    p['missedDeadline'] = any(i['status'] == 'missed_deadline' for i in p['items'])
    return p
