"""Weekly rewards; parent controls expectations, awards, and redemption.

This module contains no credentials or independent persistence. The server calls
mutations inside RiverStore.transaction and supplies its authenticated actor.
"""
from __future__ import annotations
import os
import copy
from datetime import date, datetime, timedelta
import re
import secrets
from zoneinfo import ZoneInfo

CHORE_TITLES = {'petey': 'Take your pet outside', 'dogs': 'Feed your pets', 'dishwasher': 'Check the dishwasher'}
LOCAL_TZ = ZoneInfo(os.environ.get('UNSTUCKERY_TIMEZONE', 'America/New_York'))
DEFAULT_PRIZES = [
    {'id': 'mom-chore', 'title': 'Assign Parent a chore', 'description': 'Choose one physical chore for Parent to do throughout the following calendar week.', 'kind': 'chore_swap', 'active': True},
    {'id': 'doordash', 'title': 'Choose a family meal', 'description': 'Choose a meal together within a budget agreed with your parent.', 'kind': 'parent_review', 'active': True},
    {'id': 'ice-cream', 'title': 'Ice cream shop visit', 'description': 'Go to the ice cream shop and order anything you want. Parent arranges the visit.', 'kind': 'parent_review', 'active': True},
]


def fail(message, status=400):
    from server import AppError
    raise AppError(message, status)


def text(value, limit=4000, required=False):
    if not isinstance(value, str):
        fail('Please use text for this field.')
    value = value.strip()[:limit]
    if required and not value:
        fail('Please include a brief note.')
    return value


def day_string(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        fail('Use a date in YYYY-MM-DD format.')


def evidence_day(evidence):
    """Old or malformed evidence must never turn into automatic verification."""
    try:
        value = evidence.get('at') if isinstance(evidence, dict) else evidence
        when = datetime.fromisoformat(value)
        if when.tzinfo is None:
            return None
        return when.astimezone(LOCAL_TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


def init_rewards(store):
    state = store.state.setdefault('rewards', {})
    state.setdefault('settings', {'weekStart': 0, 'qualifyingDays': [0, 1, 2, 3, 4]})
    state.setdefault('days', {})
    state.setdefault('catalog', copy.deepcopy(DEFAULT_PRIZES))
    state.setdefault('awards', [])
    state.setdefault('weeks', {})
    state.setdefault('history', [])
    for award in state['awards']:
        award.setdefault('requestRevision', max(1, sum(e.get('action') == 'prize_requested' for e in award.get('history', []))) if award.get('requestedPrize') else 0)
    return state


def task_planned(store, task, oldPlanDate=None):
    """Record the parent's actual plan, never inferred historical completion."""
    r = init_rewards(store)
    current = task['planDate']
    if oldPlanDate and oldPlanDate != current:
        old = r['days'].get(oldPlanDate)
        if old and task['id'] in old['expectedSchoolIds']:
            old['expectedSchoolIds'].remove(task['id'])
            store.history(old, 'parent', 'task_moved', {'taskId': task['id'], 'toDate': current})
    d = r['days'].setdefault(current, {'expectedSchoolIds': [], 'excusedItems': {}, 'history': []})
    if task['id'] not in d['expectedSchoolIds']:
        d['expectedSchoolIds'].append(task['id'])
    d['reviewedAt'] = store.stamp()
    d['reviewedBy'] = 'parent'
    store.history(d, 'parent', 'task_expected', {'taskId': task['id'], 'scope': task.get('scope', 'assignment')})


def period(day, settings):
    current = date.fromisoformat(day)
    start = current - timedelta(days=(current.weekday() - settings['weekStart']) % 7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def overlaps(a, b):
    return a['start'] <= b['end'] and b['start'] <= a['end']


def chore_owner(store, day, key):
    """The routine remains scheduled; a redeemed reward changes responsibility."""
    for award in init_rewards(store)['awards']:
        swap = award.get('swap')
        if award.get('redeemedAt') and swap and swap['chore'] == key and swap['start'] <= day <= swap['end']:
            return 'mom'
    return 'river'


def chore_titles(store, day=None):
    titles = dict(CHORE_TITLES)
    selected = day or store.today()
    titles.update({chore['id']: chore['title'] for chore in store.state.get('routineChores', []) if chore['startDate'] <= selected})
    return titles


def day_progress(store, day, qualifying):
    r = init_rewards(store)
    from catchup import policy, school_done, reported_in_time
    catchup = policy(store,day)
    catchup_enabled = catchup['enabled'] and day < catchup['deadlineDate']
    d = store.day(day)
    plan = r['days'].get(day, {})
    expected = plan.get('expectedSchoolIds', [])
    excused = plan.get('excusedItems', {})
    reviewed = bool(plan.get('reviewedAt'))
    known = {t['id']: t for t in store.state['tasks']}
    planned_minutes = sum(known[i].get('plannedMinutes', 25) for i in expected if i in known)
    plan_issues = []
    if not reviewed:
        plan_issues.append('A parent needs to review this day’s school expectations, including days with no schoolwork.')
    if planned_minutes > 90:
        reviewed = False
        plan_issues.append('The expected school tasks exceed 90 minutes. A parent can select smaller work blocks for this day.')
    result = {'date': day, 'qualifying': qualifying, 'planReviewed': reviewed,
              'holiday': copy.deepcopy(plan.get('holiday')),
              'expectedSchoolIds': list(expected), 'excusedItems': copy.deepcopy(excused),
              'plannedMinutes': planned_minutes, 'planIssues': plan_issues,
              'items': [], 'complete': False, 'status': 'in_progress'}
    from routines import scheduled
    for chore in (t for t in d['chores'] if scheduled(store,t,d)):
        key = chore['id'].removeprefix(day + '-')
        owner = chore_owner(store, day, key)
        confirmed_day = evidence_day(chore.get('parentConfirmed'))
        valid_confirmation = bool(confirmed_day and day <= confirmed_day <= store.today())
        done = bool(chore.get('parentConfirmed') and chore.get('completedAt') and not chore.get('reviewHold') and valid_confirmation)
        timing_reason = 'The confirmation does not match this day; a parent needs to review the chore.' if chore.get('parentConfirmed') and not valid_confirmation else ''
        result['items'].append({'id': 'chore:' + key, 'title': chore['title'], 'kind': 'chore', 'owner': owner,
            'status': 'complete' if done or owner == 'mom' else 'awaiting_review' if chore.get('reportedAt') else 'in_progress',
            'reason': 'Parent is responsible this week.' if owner == 'mom' else 'Parent confirmed.' if done else timing_reason or ('Awaiting parent confirmation.' if chore.get('reportedAt') else 'Ready when the chore is finished.')})
    b = d['braces']
    bv = store.braces_view(b)
    confirmed_day = evidence_day(b.get('parentConfirmed'))
    braces_done = bool(confirmed_day and day <= confirmed_day <= store.today())
    timing_reason = 'The wearing confirmation does not match this day; a parent needs to review it.' if b.get('parentConfirmed') and not braces_done else ''
    result['items'].append({'id': 'braces', 'title': 'Wear braces for one hour', 'kind': 'braces', 'owner': 'river',
        'status': 'complete' if braces_done else 'awaiting_review' if bv['timerElapsed'] or b.get('parentConfirmed') else 'in_progress',
        'reason': 'Parent confirmed wearing for one hour.' if braces_done else timing_reason or ('Timer finished; wearing still needs parent review.' if bv['timerElapsed'] else 'One hour daily, with parent review.')})
    for todo in store.state.get('todos', []):
        if todo['planDate'] != day or not todo.get('countsForReward', True):
            continue
        confirmed = evidence_day(todo.get('parentConfirmed'))
        done = bool(todo.get('completedAt') and not todo.get('reviewHold') and confirmed and day <= confirmed <= store.today())
        result['items'].append({'id':'todo:'+todo['id'], 'taskId':todo['id'], 'title':todo['title'], 'kind':'todo', 'owner':'river',
            'status':'complete' if done else 'awaiting_review' if todo.get('reportedAt') else 'in_progress',
            'reason':'Parent confirmed.' if done else 'Awaiting parent confirmation.' if todo.get('reportedAt') else 'Assigned to-do is still open.'})
    for ident in expected:
        t = known.get(ident)
        if not t:
            result['items'].append({'id': 'school:' + ident, 'title': 'Schoolwork needing review', 'kind': 'school', 'owner': 'river', 'status': 'awaiting_review', 'reason': 'The planned task is unavailable; parent review is needed.'})
            continue
        view = store.view_task(t)
        report_day = evidence_day(t.get('reportedAt'))
        deadline = catchup['deadlineDate'] if catchup_enabled else day
        timely = reported_in_time(store,t,deadline)
        done = school_done(store,t,deadline) if catchup_enabled else bool(t.get('completedAt') and not view['needsReview'] and timely)
        timing_reason = 'Finished after this planned day; a parent needs to review the expectation.' if report_day and report_day > day else 'The work report predates this plan; a parent needs to review the expectation.' if report_day and report_day < t['planDate'] and not timely else ''
        if catchup_enabled and not timely:
            timing_reason = 'Finish and report ready by Saturday night.' if store.today() <= deadline else 'Not reported finished before Sunday.'
        scope = t.get('scope', 'assignment')
        result['items'].append({'id': 'school:' + ident, 'taskId': ident, 'title': t['title'], 'kind': 'school', 'scope': scope, 'owner': 'river',
            'status': 'complete' if done else 'awaiting_review' if t.get('reportedAt') else 'in_progress',
            'reason': ('Planned work block complete; the whole assignment is separate.' if scope == 'work_block' else 'Required checks and completion recorded.') if done else timing_reason or ' '.join(view['needsReview']) or 'Ready for Student to mark complete.'})
    for item in result['items']:
        if item['id'] in excused:
            item['status'] = 'excused'
            item['reason'] = 'Parent changed this prize expectation: ' + excused[item['id']]
    result['complete'] = reviewed and all(i['status'] in ('complete', 'excused') for i in result['items']) and day <= store.today()
    if day > store.today():
        result['status'] = 'future'
    elif not reviewed:
        result['status'] = 'needs_plan'
    elif result['complete']:
        result['status'] = 'complete'
    elif any(i['status'] == 'awaiting_review' for i in result['items']):
        result['status'] = 'awaiting_review'
    if result['holiday']:
        # Prize credit is separate from evidence that any task was performed.
        result['complete'] = day <= store.today()
        result['status'] = 'holiday' if result['complete'] else 'future'
        result['planIssues'] = []
    return result


def reward_state(store, selected=None):
    r = init_rewards(store)
    selected = day_string(selected or store.today())
    settings = r['settings']
    start, end = period(selected, settings)
    days = []
    for offset in range(7):
        day = (date.fromisoformat(start) + timedelta(days=offset)).isoformat()
        days.append(day_progress(store, day, date.fromisoformat(day).weekday() in settings['qualifyingDays']))
    from catchup import summary as catchup_summary
    catchup = catchup_summary(store,selected,days)
    qualifying = [d for d in days if d['qualifying']]
    reasons = []
    if any(d['status'] == 'future' for d in qualifying):
        reasons.append('The remaining qualifying days are still ahead.')
    if any(d['status'] == 'needs_plan' for d in qualifying):
        reasons.append('A parent still needs to review the school expectations for some days.')
    if any(d['plannedMinutes'] > 90 and not d['holiday'] for d in qualifying):
        reasons.append('A daily school plan exceeds the 90-minute limit and needs smaller work blocks.')
    if any(d['status'] == 'awaiting_review' for d in qualifying):
        reasons.append('Some completed work is awaiting required review.')
    if any(d['status'] == 'in_progress' for d in qualifying):
        reasons.append('Some expected tasks remain unfinished.')
    matching = next((a for a in r['awards'] if overlaps(a, {'start': start, 'end': end})), None)
    if catchup['enabled'] and not catchup['complete']:
        reasons.append('Saturday catch-up deadline missed: work was not reported finished before Sunday.' if catchup['missedDeadline'] else 'All assigned weekday schoolwork and quizzes must be finished and reported by Saturday night, with required checks completed.')
    eligible = all(d['complete'] for d in qualifying) and not matching and (not catchup['enabled'] or catchup['complete'])
    today = store.today()
    active_swaps = [dict(a['swap'], awardId=a['id']) for a in r['awards'] if a.get('redeemedAt') and a.get('swap') and a['swap']['start'] <= today <= a['swap']['end']]
    selected_day = next(d for d in days if d['date'] == selected)
    return {'settings': copy.deepcopy(settings),
        'week': {'id': start, 'start': start, 'end': end, 'days': days, 'completedDays': sum(d['complete'] for d in qualifying),
                 'requiredDays': len(qualifying), 'eligible': eligible, 'reasons': reasons, 'award': copy.deepcopy(matching), 'catchup':catchup},
        'catalog': copy.deepcopy(r['catalog']), 'history': copy.deepcopy(list(reversed(r['awards']))),
        'activeSwaps': active_swaps, 'day': copy.deepcopy(selected_day),
        'schoolChoices': [{k: t.get(k) for k in ('id', 'title', 'scope', 'planDate', 'completedAt')} for t in store.state['tasks'] if t['planDate'] <= selected or t['id'] in selected_day['expectedSchoolIds']],
        'choreChoices': [{'id': key, 'title': title} for key, title in chore_titles(store).items()]}


def reward_mutate(store, path, body, actor):
    if not re.match(r'^/api/(?:parent/)?rewards/', path):
        return None
    parent = path.startswith('/api/parent/')
    if parent and actor != 'parent':
        fail('Unlock parent review to change prizes or expectations.', 403)
    if not parent and actor != 'river':
        fail('Student selects a prize in Today.', 403)
    r = init_rewards(store)
    if path == '/api/parent/rewards/catchup':
        from catchup import configure
        return configure(store,body)
    if path == '/api/parent/rewards/holiday':
        day = day_string(body.get('date', store.today()))
        enabled = body.get('enabled')
        if type(enabled) is not bool:
            fail('Choose whether this date is a holiday freebie.')
        d = r['days'].setdefault(day, {'expectedSchoolIds': [], 'excusedItems': {}, 'history': []})
        before = copy.deepcopy(d.get('holiday'))
        if bool(before) != enabled:
            d['holiday'] = {'at': store.stamp(), 'actor': 'parent'} if enabled else None
            store.history(d, actor, 'holiday_marked' if enabled else 'holiday_removed', {'before': before, 'after': d['holiday']})
        return {'ok': True, 'day': day_progress(store, day, date.fromisoformat(day).weekday() in r['settings']['qualifyingDays'])}
    if path == '/api/parent/rewards/settings':
        start = body.get('weekStart')
        days = body.get('qualifyingDays')
        if type(start) is not int or start not in range(7) or not isinstance(days, list) or not days or any(type(v) is not int or v not in range(7) for v in days) or len(days) != len(set(days)):
            fail('Choose a week start and at least one qualifying day. Monday is 0 and Sunday is 6.')
        previous = copy.deepcopy(r['settings'])
        r['settings'] = {'weekStart': start, 'qualifyingDays': sorted(days)}
        store.history(r, actor, 'reward_settings_changed', {'before': previous, 'after': r['settings']})
        return {'ok': True, 'settings': copy.deepcopy(r['settings'])}
    if path == '/api/parent/rewards/day':
        day = day_string(body.get('date', store.today()))
        ids = body.get('expectedSchoolIds')
        if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids) or len(ids) != len(set(ids)):
            fail('Choose a unique list of expected school tasks.')
        known = {t['id']: t for t in store.state['tasks']}
        if any(i not in known or known[i]['planDate'] > day for i in ids):
            fail('Choose school tasks planned on or before this day.')
        minutes = sum(known[i].get('plannedMinutes', 25) for i in ids)
        if minutes > 90:
            fail('Keep the daily school plan within 90 minutes. Use a smaller planned work block when needed.')
        excused = body.get('excusedItems', {})
        allowed = {'braces', *('chore:' + k for k in chore_titles(store, day)), *('school:' + i for i in ids), *('todo:' + t['id'] for t in store.state.get('todos',[]) if t['planDate']==day and t.get('countsForReward',True))}
        if not isinstance(excused, dict) or not set(excused) <= allowed:
            fail('Choose one of this day’s chores, braces, or expected school tasks to change its prize expectation.')
        excused = {key: text(value, required=True) for key, value in excused.items()}
        note = text(body.get('note', ''), required=True)
        d = r['days'].setdefault(day, {'expectedSchoolIds': [], 'excusedItems': {}, 'history': []})
        previous = {k: copy.deepcopy(d.get(k)) for k in ('expectedSchoolIds', 'excusedItems', 'reviewedAt')}
        d.update(expectedSchoolIds=ids, excusedItems=excused, reviewedAt=store.stamp(), reviewedBy='parent', note=note)
        store.history(d, actor, 'day_expectations_reviewed', {'before': previous, 'expectedSchoolIds': ids, 'excusedItems': excused, 'note': note})
        return {'ok': True, 'day': day_progress(store, day, date.fromisoformat(day).weekday() in r['settings']['qualifyingDays'])}
    if path == '/api/parent/rewards/catalog':
        ident = body.get('id')
        prize = next((p for p in r['catalog'] if p['id'] == ident), None) if ident else None
        if ident and not prize:
            fail('That prize was not found.', 404)
        title = text(body.get('title', ''), 160, required=True)
        description = text(body.get('description', ''), 2000, required=True)
        kind = body.get('kind', prize['kind'] if prize else 'parent_review')
        active = body.get('active', True)
        if kind not in ('chore_swap', 'parent_review') or type(active) is not bool:
            fail('Choose a chore swap or parent-reviewed prize and whether it is available.')
        if prize:
            before = copy.deepcopy(prize)
            prize.update(title=title, description=description, kind=kind, active=active)
        else:
            before = None
            prize = {'id': secrets.token_hex(8), 'title': title, 'description': description, 'kind': kind, 'active': active}
            r['catalog'].append(prize)
        store.history(r, actor, 'prize_saved', {'before': before, 'after': prize})
        return {'ok': True, 'prize': copy.deepcopy(prize)}
    if path == '/api/parent/rewards/award':
        selected = day_string(body.get('date', body.get('week', store.today())))
        week = reward_state(store, selected)['week']
        if week['award']:
            fail('A prize has already been earned for this week. Open the existing reward.', 409)
        if not week['eligible']:
            fail('This week still needs review: ' + ' '.join(week['reasons']), 409)
        note = text(body.get('note', ''))
        award = {'id': week['id'], 'start': week['start'], 'end': week['end'], 'earnedAt': store.stamp(), 'earnedBy': 'parent',
                 'note': note, 'settings': copy.deepcopy(r['settings']), 'evidenceSnapshot': copy.deepcopy(week['days']), 'catchupSnapshot':copy.deepcopy(week['catchup']),
                 'requestedPrize': None, 'requestedAt': None, 'requestRevision': 0, 'redeemedAt': None, 'selectedPrize': None, 'swap': None, 'history': []}
        store.history(award, actor, 'reward_earned', {'note': note})
        r['awards'].append(award)
        return {'ok': True, 'award': copy.deepcopy(award)}
    if path in ('/api/rewards/request', '/api/parent/rewards/redeem'):
        award = next((a for a in r['awards'] if a['id'] == body.get('awardId')), None)
        if not award:
            fail('That earned reward was not found.', 404)
        if award.get('redeemedAt'):
            fail('This week’s prize has already been redeemed.', 409)
        if path.endswith('/request'):
            prize = next((p for p in r['catalog'] if p['id'] == body.get('prizeId') and p['active']), None)
            if not prize:
                fail('Choose an available prize.')
            request = {'prize': copy.deepcopy(prize), 'chore': None, 'swap': None}
            if prize['kind'] == 'chore_swap':
                chore = body.get('chore')
                titles = chore_titles(store)
                if not isinstance(chore, str) or chore not in titles:
                    fail('Choose one of the available routine chores.')
                last = date.fromisoformat(award['end'])
                start = last + timedelta(days=(7 - last.weekday()) % 7 or 7)
                request['chore'] = chore
                request['swap'] = {'chore': chore, 'title': titles[chore], 'owner': 'mom', 'start': start.isoformat(), 'end': (start + timedelta(days=6)).isoformat()}
                if store.today() > request['swap']['end']:
                    fail('That following week has ended. Choose another available prize for parent review.', 409)
            award['requestedPrize'] = request
            award['requestedAt'] = store.stamp()
            award['requestRevision'] += 1
            store.history(award, actor, 'prize_requested', dict(request, requestRevision=award['requestRevision']))
        else:
            request = award.get('requestedPrize')
            if not request:
                fail('Student can choose a prize before parent redemption.', 409)
            revision = body.get('requestRevision')
            if type(revision) is not int or revision != award['requestRevision']:
                fail('Student’s prize choice has changed or needs refreshing. Review the current choice before confirming it.', 409)
            if request.get('swap') and store.today() > request['swap']['end']:
                fail('The chore-swap week has ended. Student can select another prize.', 409)
            # Corrections remain authoritative even after the original award.
            snapshot_days = award['evidenceSnapshot']
            current_week = reward_state(store,award['start'])['week']
            if current_week['catchup']['enabled'] and not current_week['catchup']['complete']:
                fail('This week still has assigned work that needs review or missed the Saturday deadline.',409)
            for snapshot in snapshot_days:
                if snapshot['qualifying'] and not day_progress(store, snapshot['date'], True)['complete']:
                    fail('This earned week has changed and needs review before redemption. Check its daily expectations and evidence.', 409)
            note = text(body.get('note', ''))
            award['selectedPrize'] = copy.deepcopy(request['prize'])
            award['swap'] = copy.deepcopy(request.get('swap'))
            award['redeemedAt'] = store.stamp()
            award['redeemedBy'] = 'parent'
            award['redemptionNote'] = note
            store.history(award, actor, 'prize_redeemed', {'prize': award['selectedPrize'], 'swap': award['swap'], 'note': note, 'requestRevision': revision})
        return {'ok': True, 'award': copy.deepcopy(award)}
    fail('That prize action was not found.', 404)
