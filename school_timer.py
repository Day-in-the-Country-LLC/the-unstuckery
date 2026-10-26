"""Persistent work/break phases; rests start automatically, work resumes explicitly."""
import os
import copy
import secrets
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get('UNSTUCKERY_TIMEZONE', 'America/New_York'))
DURATIONS = {'work': 1800, 'break': 600, 'braces': 3600}


def advance(store, day):
    """Start only the rest countdown at a finished work boundary, including after restart.

    This runs under the store transaction used for state reads and mutations. The
    break starts at the saved work deadline, not when a tab next polls the server.
    It never starts work or the separate braces timer.
    """
    c = day['schoolClock']
    p = c.get('phase')
    if (c['date'] != store.today() or not p or p['kind'] != 'work'
            or not p.get('running') or not c.get('running') or not p.get('startedAt')):
        return
    remaining = max(0, p['durationSeconds'] - p['elapsedSeconds'])
    finished_at = datetime.fromisoformat(p['startedAt']) + timedelta(seconds=remaining)
    if store.now() < finished_at:
        return
    seconds = min(5400, c['elapsedSeconds'] + remaining)
    if seconds >= 5400:
        return
    c.update(elapsedSeconds=seconds, startedAt=None, running=False)
    c['phase'] = {
        'id': p['id'] + '-rest', 'kind': 'break', 'durationSeconds': 600,
        'elapsedSeconds': 0, 'startedAt': finished_at.isoformat(), 'running': True,
        'creditedSeconds': 0, 'afterWorkSession': p.get('sessionNumber', seconds // 1800),
        'fromWorkId': p['id'], 'automatic': True,
    }


def phase_view(store, day):
    advance(store, day)
    p = copy.deepcopy(day['schoolClock'].get('phase'))
    if not p:
        return None
    if p['kind'] == 'braces':
        b = store.braces_view(day['braces'])
        elapsed = b['elapsedSeconds']
        running = b['running']
    else:
        elapsed = p['elapsedSeconds']
        if p['running']:
            midnight = datetime.combine(date.fromisoformat(day['schoolClock']['date']) + timedelta(days=1), datetime.min.time(), TZ)
            elapsed += max(0, (min(store.now(), midnight) - datetime.fromisoformat(p['startedAt'])).total_seconds())
        running = p['running'] and day['schoolClock']['date'] == store.today()
    p['elapsedSeconds'] = min(p['durationSeconds'], int(elapsed))
    p['finished'] = p['elapsedSeconds'] >= p['durationSeconds']
    p['running'] = running and not p['finished']
    p['remainingSeconds'] = max(0, p['durationSeconds'] - p['elapsedSeconds'])
    return p


def settle(store):
    d = store.day(store.today())
    c = d['schoolClock']
    view = store.school_clock_view(c)
    seconds = view['elapsedSeconds']
    p = phase_view(store, d)
    c.update(elapsedSeconds=seconds, restSeconds=view['restSeconds'], startedAt=None, running=False)
    if p:
        c['phase'].update(elapsedSeconds=p['elapsedSeconds'], startedAt=None, running=False)
        if p['kind'] == 'break':
            c['phase']['creditedSeconds'] = p['elapsedSeconds']
    return d, c, p


def pause(store):
    d, c, p = settle(store)
    if p and p['kind'] == 'braces':
        store.mutate('/api/braces/pause', {}, 'river')
    return store.school_clock_view(c)


def start(store, kind=None):
    from server import AppError
    d = store.day(store.today())
    current = phase_view(store, d)
    if kind is not None and (not isinstance(kind, str) or kind not in DURATIONS):
        raise AppError('Choose a work session, short break, or braces break.')
    if kind is None and current is None and d['schoolClock']['elapsedSeconds'] > 0:
        kind = 'work'
    kind = kind or (current['kind'] if current and not current['finished'] else 'work')
    if current and current['running'] and current['kind'] == kind:
        return store.school_clock_view(d['schoolClock'])
    view = store.school_clock_view(d['schoolClock'])
    remaining = 5400 - view['elapsedSeconds']
    if kind == 'work' and remaining <= 0:
        raise AppError('Schoolwork time is up for today. Save your place and continue another day.', 409)
    if kind == 'braces' and store.braces_view(d['braces'])['timerElapsed']:
        raise AppError('Your braces hour has already elapsed today.', 409)
    d, c, p = settle(store)
    if not p or p['kind'] != kind or p['finished']:
        p = {'id': secrets.token_hex(12), 'kind': kind, 'durationSeconds': min(1800 - (view['elapsedSeconds'] % 1800), remaining) if kind == 'work' else 600 if kind == 'break' else 3600,
             'elapsedSeconds': 0, 'running': False, 'startedAt': None, 'creditedSeconds': 0}
        if kind == 'work':
            p['sessionNumber'] = min(3, view['elapsedSeconds'] // 1800 + 1)
        c['phase'] = p
    else:
        p = c['phase']
    if kind == 'braces':
        store.mutate('/api/braces/start', {}, 'river')
    else:
        p.update(startedAt=store.stamp(), running=True)
        if kind == 'work':
            c.update(startedAt=store.stamp(), running=True)
    return store.school_clock_view(c)


def correct(store, body, actor='parent'):
    """Remove off-task time without changing break time or completion evidence."""
    from server import AppError, valid_date, clean
    day = valid_date(body.get('date', store.today()))
    if day > store.today():
        raise AppError('Future schoolwork time cannot be corrected.', 409)
    minutes = body.get('removeMinutes')
    if type(minutes) is not int or not 1 <= minutes <= 90:
        raise AppError('Enter between 1 and 90 whole minutes to remove.')
    d = store.day(day)
    c = d['schoolClock']
    before = store.school_clock_view(c)
    removed = minutes * 60
    if removed > before['elapsedSeconds']:
        raise AppError('There is not that much recorded schoolwork time to remove.', 409)
    note = clean(body.get('note', ''), 4000) or 'Time spent off task.'
    seconds = before['elapsedSeconds'] - removed
    c.update(elapsedSeconds=seconds, startedAt=None, running=False)
    p = before['phase']
    if not p or p['kind'] == 'work':
        # A fresh paused interval works toward the corrected 30-minute boundary.
        c['phase'] = {'id': secrets.token_hex(12), 'kind': 'work',
                      'durationSeconds': min(1800 - seconds % 1800, 5400 - seconds),
                      'elapsedSeconds': 0, 'running': False, 'startedAt': None,
                      'creditedSeconds': 0, 'sessionNumber': min(3, seconds // 1800 + 1)}
    # Leave a rest/braces phase and its underlying timer untouched.
    store.history(c, actor, 'school_time_corrected', {
        'date': day, 'removedMinutes': minutes,
        'beforeSeconds': before['elapsedSeconds'], 'afterSeconds': seconds,
        'previousPhase': p, 'note': note,
    })
    return store.school_clock_view(c)
