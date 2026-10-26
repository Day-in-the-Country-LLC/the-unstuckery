#!/usr/bin/env python3
"""Student's local after-school app. Python 3.11+, standard library only."""
from __future__ import annotations
import argparse
import copy
import hashlib
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import sys

# Extension modules share AppError even when launched as a script.
sys.modules.setdefault("server", sys.modules[__name__])
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
from practice import PracticeError

LOCAL_TZ = ZoneInfo(os.environ.get('UNSTUCKERY_TIMEZONE', 'America/New_York'))
PARENT_TTL = 30 * 60

class AppError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def clean(value, limit=4000):
    if not isinstance(value, str):
        raise AppError('Please use text for this field.')
    return value.strip()[:limit]

def required(body, key, limit=4000):
    value = clean(body.get(key, ''), limit)
    if not value:
        raise AppError(f'Please include {key}.')
    return value

def valid_date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise AppError('Use a date in YYYY-MM-DD format.')

def safe_url(value):
    value = clean(value, 2000)
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise AppError('Use an http or https source link.')
    return value

class RiverStore:
    def __init__(self, data_dir, now=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.data_dir / 'river.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        row = self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        self._saved_body = row[0] if row else None
        self.state = json.loads(row[0]) if row else {'version':1,'auth':None,'days':{},'tasks':[],'importedDrafts':[]}
        self.sessions = {}
        self.failed_logins = []
        token_path = self.data_dir / '.assistant-token'
        if not token_path.exists():
            fd = os.open(token_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(32))
        self.assistant_token = token_path.read_text().strip()
        os.chmod(token_path, 0o600)
        from planner import init_planner
        init_planner(self)
        from manager_access import init_manager
        init_manager(self)
        os.chmod(self.data_dir / 'river.sqlite3', 0o600)
        from photos import init_schema
        init_schema(self)
        from rewards import init_rewards
        init_rewards(self)
        from practice import init_practice
        init_practice(self)
        from parent_review import reconcile
        reconcile(self)
        self.save()

    def stamp(self):
        return self.now().astimezone(timezone.utc).isoformat()

    def today(self):
        return self.now().astimezone(LOCAL_TZ).date().isoformat()

    def save(self):
        payload=json.dumps(self.state)
        if payload==self._saved_body:
            if self.db.in_transaction:
                self.db.commit()
                if getattr(self, 'storage', None):
                    self.storage.checkpoint(self, force=True)
            return
        self.db.execute('INSERT INTO state(id,body) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (payload,))
        self.db.commit()
        self._saved_body=payload
        if getattr(self, 'storage', None):
            self.storage.checkpoint(self)

    def transaction(self, fn):
        with self.lock:
            if getattr(self, 'storage', None):
                self.storage.ensure_write()
            original = copy.deepcopy(self.state)
            try:
                result = fn()
                self.save()
                return result
            except Exception:
                self.state = original
                self.db.rollback()
                raise

    def history(self, obj, actor, action, details):
        obj.setdefault('history', []).append({'at':self.stamp(),'actor':actor,'action':action,'details':copy.deepcopy(details)})

    def base_task(self, ident, kind, subject, title, instructions, plan_date, order):
        return {'id':ident,'kind':kind,'subject':subject,'title':title,'instructions':instructions,'planDate':plan_date,'order':order,
                'dueDate':'','sourceUrl':'','criteria':'','sourceNote':'','requirements':{'understanding':False,'submission':False,'parent':False},
                'reportedAt':None,'reportedNote':'','understanding':None,'submission':None,'parentConfirmed':None,'completedAt':None,
                'helpNote':'','reviewHold':'','scope':'assignment','assignmentId':'','plannedMinutes':25,'history':[]}

    def day(self, day):
        day = valid_date(day)
        if day not in self.state['days']:
            routine = [('petey','Take your pet outside','Take your pet outside to go to the bathroom.'),
                       ('dogs','Feed your pets','Give your pets their food.'),
                       ('dishwasher','Check the dishwasher','If there are clean dishes, put them away. Otherwise, report that no clean dishes need putting away.')]
            chores = [self.base_task(f'{day}-{key}','chore','After school',title,instructions,day,i) for i,(key,title,instructions) in enumerate(routine)]
            self.state['days'][day] = {'afterSchool':date.fromisoformat(day).weekday()<5,'chores':chores,
                'schoolClock':{'date':day,'startedAt':None,'elapsedSeconds':0,'running':False},
                'braces':{'date':day,'startedAt':None,'elapsedSeconds':0,'running':False,'parentConfirmed':None,'history':[]}}
        self.state['days'][day].setdefault('schoolClock',{'date':day,'startedAt':None,'elapsedSeconds':0,'running':False})
        chores = self.state['days'][day]['chores']
        existing = {chore['id'] for chore in chores}
        for template in self.state.get('routineChores', []):
            ident = f"{day}-{template['id']}"
            if day >= template['startDate'] and ident not in existing:
                chores.append(self.base_task(ident, 'chore', 'After school', template['title'], template['instructions'], day, len(chores)))
                existing.add(ident)
        return self.state['days'][day]

    def view_task(self, task):
        t = copy.deepcopy(task)
        if t.get('practiceQuizId'):
            from mastery import is_mastery
            quiz=next((q for q in self.state.get('practice',{}).get('quizzes',[]) if q['id']==t['practiceQuizId']),{})
            t['masteryReview']=is_mastery(quiz)
            if t['masteryReview']:
                from mastery import status
                stats=status(self,quiz)
                if not t['parentConfirmed']:t['reportedAt']=stats['reportedAt']
                t['canReportReady']=stats['allApproved'] and not t['reportedAt']
                t['quizSupport']={key:stats[key] for key in ('totalAttempts','totalHints','totalTips','totalHelpedAttempts')}
                revisits=[item for item in self.state.get('practice',{}).get('revisits',[]) if item['quizId']==quiz['id']]
                pending=sorted(item['dueDate'] for item in revisits if not item.get('completedAt'))
                completed=sorted(item['dueDate'] for item in revisits if item.get('completedAt'))
                if pending:
                    t['practiceReviewDate']=pending[0]
                    t['practiceReviewPending']=True
                elif completed and not t.get('completedAt'):
                    # Keep the task in that day's main list through Student's ready report and parent confirmation.
                    t['practiceReviewDate']=completed[-1]
                    t['practiceReviewPending']=False
        if t['kind']=='chore':
            from routines import scheduled
            t['routineScheduled'] = scheduled(self, t, self.day(t['planDate']))
            from rewards import chore_owner
            t['owner']=chore_owner(self,t['planDate'],t['id'].removeprefix(t['planDate']+'-'))
        needs = []
        if t['kind'] in ('chore','todo'):
            if not t['parentConfirmed'] and t.get('owner')!='mom':
                needs.append('Parent confirmation needed.' if t['reportedAt'] else 'Report ready when you have finished.')
            t['canComplete'] = False
        else:
            from parent_review import is_final
            parent_final=is_final(t)
            if not t['reportedAt']:
                needs.append('Check the instructions and report your work ready.')
            if t.get('reviewHold'):
                needs.append('Correction needs review: '+t['reviewHold'])
            for flag, field, text in [('understanding','understanding','Explain the learning goal to your assistant in your own words or try a fresh example.'),
                                       ('submission','submission','Your assistant needs to observe the portal confirmation and check the required work.'),
                                       ('parent','parentConfirmed','Parent review is needed.')]:
                if t['requirements'].get(flag) and not t[field] and not (parent_final and flag in ('understanding','submission')):
                    needs.append(text)
            from practice import practice_task_review
            needs.extend(practice_task_review(self,t))
            t['canComplete'] = not needs and not t['completedAt']
        t['needsReview'] = needs
        return t

    def braces_view(self, braces):
        b = copy.deepcopy(braces)
        seconds = b['elapsedSeconds']
        if b['running']:
            end_of_day = datetime.combine(date.fromisoformat(b['date']) + timedelta(days=1), datetime.min.time(), LOCAL_TZ)
            seconds += max(0, (min(self.now(), end_of_day) - datetime.fromisoformat(b['startedAt'])).total_seconds())
        b['elapsedSeconds'] = min(3600, int(seconds))
        b['timerElapsed'] = b['elapsedSeconds'] >= 3600
        b['running'] = b['running'] and not b['timerElapsed'] and b['date']==self.today()
        return b

    def school_clock_view(self, clock):
        from school_timer import advance, phase_view
        advance(self, self.day(clock['date']))
        c = copy.deepcopy(clock)
        seconds = c['elapsedSeconds']
        if c['running'] and (not c.get('phase') or c['phase']['kind'] == 'work'):
            midnight = datetime.combine(date.fromisoformat(c['date'])+timedelta(days=1),datetime.min.time(),LOCAL_TZ)
            added = max(0,(min(self.now(),midnight)-datetime.fromisoformat(c['startedAt'])).total_seconds())
            p = c.get('phase')
            if p:
                added = min(added, max(0, p['durationSeconds']-p['elapsedSeconds']))
            seconds += added
        c['elapsedSeconds'] = min(5400,int(seconds))
        c['limitReached'] = c['elapsedSeconds']>=5400
        c['phase'] = phase_view(self, self.day(c['date']))
        p = c['phase']
        c['restSeconds'] = c.get('restSeconds', 0) + (max(0, p['elapsedSeconds']-p.get('creditedSeconds', 0)) if p and p['kind'] == 'break' else 0)
        c['restComplete'] = c['restSeconds'] >= 600
        c['completedWorkSessions'] = c['elapsedSeconds'] // 1800
        c['nextKind'] = None if c['limitReached'] else ('break' if p and p['kind'] == 'break' and not p['finished'] else 'work')
        c['running'] = c['running'] and (not p or p['kind'] == 'work') and not c['limitReached'] and c['date']==self.today() and (not c['phase'] or c['phase']['running'])
        return c

    def start_school_clock(self):
        from school_timer import start, phase_view
        p = phase_view(self, self.day(self.today()))
        clock = self.school_clock_view(self.day(self.today())['schoolClock'])
        if (p and (p['kind'] != 'work' or not p['running'])) or (not p and clock['elapsedSeconds'] and not clock['running']):
            raise AppError('Your work clock is paused. Choose Resume work when you are ready, then submit your answer. Your draft is kept.',409)
        return start(self, 'work')

    def view(self, day=None):
        day = valid_date(day or self.today())
        d = self.day(day)
        school = sorted((t for t in self.state['tasks'] if t['planDate']<=day and (t['planDate']==day or not t['completedAt'] or datetime.fromisoformat(t['completedAt']).astimezone(LOCAL_TZ).date().isoformat()==day)), key=lambda t:(t['order'],t['planDate'],t['id']))
        published = {q['id'] for q in self.state.get('practice',{}).get('quizzes',[]) if q['published']}
        upcoming = sorted((t for t in self.state['tasks'] if t['planDate']>day and (not t.get('practiceQuizId') or t['practiceQuizId'] in published)),key=lambda t:(t['planDate'],t['order'],t['id']))
        # Preserve any recorded routine evidence even if parent later disables the routine.
        from routines import scheduled, catalog
        chores = [t for t in d['chores'] if scheduled(self,t,d) or t['reportedAt'] or t['history']]
        todos = sorted((t for t in self.state.get('todos',[]) if t['planDate']<=day and (t['planDate']==day or not t['completedAt'] or datetime.fromisoformat(t['completedAt']).astimezone(LOCAL_TZ).date().isoformat()==day)),key=lambda t:(t['planDate'],t['order']))
        tasks = [self.view_task(t) for t in chores+school+todos]
        from rewards import reward_state
        return {'timezone':str(LOCAL_TZ),'routineCatalog':catalog(self),'rewards':reward_state(self,day),'date':day,'today':self.today(),'afterSchool':d['afterSchool'],'schoolMinutes':90,'tasks':tasks,
            'schoolwork':{'today':[t for t in tasks if t['kind']=='school' and (t['planDate']==day or t.get('practiceReviewDate')==day)],
                          'earlier':[t for t in tasks if t['kind']=='school' and t['planDate']<day and not t['completedAt']
                                     and not (t.get('practiceReviewDate') and t['practiceReviewDate']>=day)],
                          'upcoming':[self.view_task(t) for t in upcoming]},
            'braces':self.braces_view(d['braces']),'schoolClock':self.school_clock_view(d['schoolClock']),
            'summary':{'completed':[t['title'] for t in tasks if t['completedAt']] + (['One hour in braces'] if d['braces']['parentConfirmed'] else []),
                'unfinished':[{'id':t['id'],'title':t['title'],'reasons':t['needsReview'] or ['Ready for Student to mark complete.']} for t in tasks if not t['completedAt'] and t.get('owner')!='mom' and t.get('routineScheduled') is not False] + ([] if d['braces']['parentConfirmed'] else [{'id':day+'-braces','title':'One hour in braces','reasons':['Parent review of wearing is still needed.']}]),
                'helpNeeded':[{'id':t['id'],'title':t['title'],'note':t['helpNote']} for t in tasks if t['helpNote']]}}

    def task(self, ident):
        for t in self.state['tasks'] + self.state.get('todos',[]):
            if t['id']==ident:
                return t
        for d in self.state['days'].values():
            for t in d['chores']:
                if t['id']==ident:
                    return t
        raise AppError('That task was not found.',404)

    def current_task(self, ident):
        t = self.task(ident)
        if (t['kind']=='chore' and t['planDate']!=self.today()) or (t['kind']!='school' and t['planDate']>self.today()):
            raise AppError('Open today to update this task.',409)
        if t['kind']=='school' and t['planDate']>self.today() and t.get('practiceQuizId'):
            from practice import _quiz
            if not _quiz(self,t['practiceQuizId'])['published']:
                raise AppError('This quiz needs parent publication before working ahead.',409)
        if t['kind']=='chore':
            from routines import scheduled
            if not scheduled(self,t,self.day(self.today())):
                raise AppError('This chore is not scheduled today.',409)
        return t

    def auth_status(self, session):
        if getattr(self, 'cloud_auth', None):
            return self.cloud_auth.status(session)
        now = self.now().timestamp()
        self.sessions = {key: expiry for key,expiry in self.sessions.items() if expiry>now}
        key = hashlib.sha256((session or '').encode()).hexdigest()
        return {'configured':bool(self.state['auth']),'parent':key in self.sessions}

    def authenticate(self, pin, setup=False):
        if not isinstance(pin,str) or not re.fullmatch(r'\d{4}',pin):
            raise AppError('Use a four-digit passcode.')
        now = self.now().timestamp()
        self.failed_logins = [at for at in self.failed_logins if now-at<600]
        if len(self.failed_logins)>=5:
            raise AppError('Please wait ten minutes before trying again.',429)
        if setup:
            if self.state['auth']:
                raise AppError('A parent passcode is already set.',409)
            salt = secrets.token_hex(16)
            digest = hashlib.scrypt(pin.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
            self.state['auth'] = {'salt':salt,'digest':digest}
        elif not self.state['auth']:
            raise AppError('Set a parent passcode first.',409)
        else:
            auth = self.state['auth']
            digest = hashlib.scrypt(pin.encode(),salt=bytes.fromhex(auth['salt']),n=16384,r=8,p=1).hex()
            if not hmac.compare_digest(digest,auth['digest']):
                self.failed_logins.append(now)
                raise AppError('That passcode did not match.',401)
        self.failed_logins = []
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now+PARENT_TTL
        return token

    def school_fields(self, body):
        req = body.get('requirements',{})
        if not isinstance(req,dict) or any(type(req.get(k,False)) is not bool for k in ('understanding','submission','parent')):
            raise AppError('Completion requirements must be yes or no.')
        scope = body.get('scope','assignment')
        if scope not in ('assignment','work_block','practice'):
            raise AppError('Choose an assignment, work session, or practice session.')
        minutes = body.get('plannedMinutes',25)
        if type(minutes) is not int or minutes<0 or minutes>90:
            raise AppError('Plan between 0 and 90 minutes for a task.')
        return {'scope':scope,'assignmentId':clean(body.get('assignmentId',''),100),'plannedMinutes':minutes,'subject':required(body,'subject',100),'title':required(body,'title',250),
            'instructions':required(body,'instructions'),'planDate':valid_date(body.get('planDate',self.today())),
            'dueDate':valid_date(body['dueDate']) if body.get('dueDate') else '',
            'sourceUrl':safe_url(body.get('sourceUrl','')),'criteria':clean(body.get('criteria','')),
            'sourceNote':clean(body.get('sourceNote','')),'requirements':{k:req.get(k,False) for k in ('understanding','submission','parent')}}

    def create_task(self, body, actor='parent'):
        fields = self.school_fields(body)
        task = self.base_task(secrets.token_hex(8),'school',fields['subject'],fields['title'],fields['instructions'],fields['planDate'],len(self.state['tasks']))
        task.update(fields)
        self.history(task,actor,'planned',fields)
        self.state['tasks'].append(task)
        from rewards import task_planned
        task_planned(self,task,actor=actor)
        return self.view_task(task)

    def drafts(self):
        draft_path = self.data_dir / 'draft-plan.json'
        if not draft_path.exists(): draft_path = ROOT / 'draft-plan.json'
        raw = json.loads(draft_path.read_text())
        raw['items'] = [t for t in raw['items'] if t['id'] not in self.state['importedDrafts']]
        return raw

    def mutate(self, path, body, actor):
        if path.startswith('/api/manager/') or actor == 'manager':
            from manager_access import mutate
            return mutate(self, path, body, actor)
        if path.startswith('/api/planner/') or actor == 'planner':
            from planner import mutate
            return mutate(self, path, body, actor)
        if path.startswith('/api/parent/') and actor != 'parent':
            raise AppError('Unlock parent review to use this control.',403)
        if path == '/api/browser-memory':
            if actor != 'river':
                raise AppError('Browser preferences and answer drafts belong to Student.',403)
            key = body.get('key', '')
            if not isinstance(key, str) or not re.fullmatch(r'river-(?:timer-sound|timer-heard|answer-[a-zA-Z0-9_-]{1,180})', key):
                raise AppError('That browser memory key is not supported.')
            value = body.get('value')
            if value is not None and (not isinstance(value, str) or len(value) > 20000):
                raise AppError('Keep the saved draft under 20,000 characters.')
            memory = self.state.setdefault('browserMemory', {})
            if value is None:
                memory.pop(key, None)
            else:
                if key not in memory and len(memory) >= 500:
                    raise AppError('There are too many saved answer drafts. Submit or clear older drafts first.')
                memory[key] = value
            return {'ok': True}
        from photos import mutate as photo_mutate
        photo_result = photo_mutate(self,path,body,actor)
        if photo_result is not None: return photo_result
        review_retry=re.fullmatch(r'/api/practice/mastery/([a-zA-Z0-9_-]+)/(?:review-retry|tutor-retry)',path)
        if review_retry:
            from ai_review import retry
            return retry(self,review_retry.group(1),body,actor)
        from backlog import mutate as mutate_backlog
        backlog_result=mutate_backlog(self,path,body,actor)
        if backlog_result is not None:return backlog_result
        from todos import mutate as mutate_todo
        todo_result = mutate_todo(self,path,body,actor)
        if todo_result is not None:
            return todo_result
        if path.startswith(('/api/practice/','/api/parent/practice/','/api/assistant/practice/')):
            from practice import practice_mutate
            return practice_mutate(self,path,body,actor)
        from rewards import reward_mutate
        reward_result=reward_mutate(self,path,body,actor)
        if reward_result is not None:
            return reward_result
        if path=='/api/parent/school-clock/correct':
            from school_timer import correct
            return correct(self,body)
        if path.startswith('/api/school-clock/'):
            if actor != 'river':
                raise AppError('Student operates the schoolwork timer in Today.',403)
            from school_timer import start, pause
            if path=='/api/school-clock/start':
                return start(self, body.get('kind'))
            if path=='/api/school-clock/pause':
                return pause(self)
        schedule_match = re.fullmatch(r'/api/parent/routine-chores/([^/]+)/schedule',path)
        if schedule_match:
            from routines import update_schedule
            return update_schedule(self,schedule_match.group(1),body)
        if path=='/api/parent/routine-chores':
            if actor != 'parent':
                raise AppError('Unlock parent review to add a routine chore.',403)
            from routines import validate_days
            days = validate_days(body.get('days'))
            title = required(body, 'title', 300)
            instructions = clean(body.get('instructions', ''), 4000) or 'When you have finished, let your parent know it is ready to check.'
            start_date = valid_date(body.get('startDate', self.today()))
            if start_date < self.today():
                raise AppError('Choose today or a future date. Earlier checklists stay as they were.')
            templates = self.state.setdefault('routineChores', [])
            if len(templates) >= 100:
                raise AppError('The routine already has 100 added chores.')
            from rewards import CHORE_TITLES
            titles = list(CHORE_TITLES.values()) + [item['title'] for item in templates]
            if title.casefold() in [item.casefold() for item in titles]:
                raise AppError('A chore with that name is already in the routine.',409)
            template = {'id': 'custom-' + secrets.token_hex(8), 'title': title, 'instructions': instructions,
                        'startDate': start_date, 'days': days, 'createdAt': self.stamp(), 'createdBy': 'parent', 'history': []}
            self.history(template, 'parent', 'routine_chore_created', {'title': title, 'instructions': instructions, 'startDate': start_date, 'days': days})
            templates.append(template)
            self.day(start_date)
            return {'ok': True, 'chore': copy.deepcopy(template)}
        if path=='/api/parent/tasks':
            return self.create_task(body)
        if path=='/api/parent/order':
            ids = body.get('ids')
            if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):
                raise AppError('Use a unique list of school tasks.')
            known = {t['id'] for t in self.state['tasks']}
            if not set(ids)<=known:
                raise AppError('Only school tasks can be ordered.')
            remainder = [t['id'] for t in sorted(self.state['tasks'],key=lambda t:t['order']) if t['id'] not in ids]
            for i,ident in enumerate(ids+remainder):
                t = self.task(ident)
                if t['order']!=i:
                    t['order']=i
                    self.history(t,'parent','reordered',{'order':i})
            return {'ok':True}
        if path=='/api/parent/day':
            d = self.day(body.get('date',self.today()))
            if type(body.get('afterSchool')) is not bool:
                raise AppError('Choose whether this is an after-school day.')
            d['afterSchool']=body['afterSchool']
            self.history(d,'parent','routine_changed',{'afterSchool':body['afterSchool']})
            return {'ok':True}
        if path=='/api/parent/import-drafts':
            ids = body.get('ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
                raise AppError('Choose draft tasks to add.')
            choices = {d['id']:d for d in self.drafts()['items']}
            if not set(ids)<=set(choices):
                raise AppError('These draft tasks were already added or are unavailable.',409)
            added = []
            for ident in ids:
                added.append(self.create_task(choices[ident]))
                self.state['importedDrafts'].append(ident)
            return {'ok':True,'tasks':added}
        match = re.fullmatch(r'/api/(parent/|assistant/)?tasks/([^/]+)(?:/([a-z]+))?',path)
        if match:
            scope, ident, action = match.groups()
            t = self.current_task(ident) if actor in ('river','assistant') else self.task(ident)
            if action=='confirm' and t['kind']!='school' and t['planDate']>self.today():
                raise AppError('Future work cannot be confirmed in advance.',409)
            if scope=='parent/' and action is None:
                if t['kind']!='school':
                    raise AppError('The routine is fixed; edit schoolwork in the planner.')
                fields = self.school_fields(body)
                changed = any(t.get(k)!=v for k,v in fields.items())
                if changed:
                    before = {k:t.get(k) for k in fields}
                    t.update(fields)
                    if before['planDate']!=fields['planDate']:
                        from rewards import task_planned
                        task_planned(self,t,before['planDate'])
                    # Keep evidence in history, require fresh checks if substantive work changes.
                    substantive = any(before[k]!=fields[k] for k in ('title','subject','instructions','criteria','scope','assignmentId','plannedMinutes'))
                    if substantive:
                        t['reportedAt']=None
                        t['understanding']=None
                        t['submission']=None
                        t['parentConfirmed']=None
                    if substantive or self.view_task(t)['needsReview']:
                        t['completedAt']=None
                    self.history(t,'parent','plan_edited',{'before':before,'after':fields,'freshChecksNeeded':substantive})
            elif scope is None and action=='report':
                if t['kind']=='school' and t['requirements'].get('parent') and actor!='river':
                    raise AppError('Student reports schoolwork ready for parent review.',403)
                if t.get('practiceQuizId'):
                    from mastery import is_mastery,status
                    from practice import _quiz
                    quiz=_quiz(self,t['practiceQuizId'])
                    if is_mastery(quiz) and (actor!='river' or not status(self,quiz)['allApproved']):
                        raise AppError('Student can report this quiz ready after every required answer is assistant-approved.',409)
                t['reportedBy']=actor
                t['parentReturned']=None
                t['reportedAt']=self.stamp()
                # Preserve the assigned date at the actual ready report, allowing
                # genuine work-ahead credit without accepting backdated evidence.
                if t['kind']=='school':t['reportedPlanDate']=t['planDate']
                t['reportedNote']=clean(body.get('note',''))
                self.history(t,actor,'reported_ready',{'note':t['reportedNote']})
            elif scope is None and action=='help':
                t['helpNote']=required(body,'note')
                self.history(t,actor,'help_requested',{'note':t['helpNote']})
            elif scope is None and action=='complete':
                if t['kind']!='school':
                    raise AppError('Only a parent can check off chores and other to-dos.',403)
                if t['completedAt']:
                    return self.view_task(t)
                if not self.view_task(t)['canComplete']:
                    raise AppError('This work still needs review: '+' '.join(self.view_task(t)['needsReview']),409)
                t['completedAt']=self.stamp()
                self.history(t,actor,'completed',{'note':'Student checked off the task after its required checks.'})
            elif scope is None and action=='correct':
                note = required(body,'note')
                t['reportedAt']=None
                t['completedAt']=None
                t['reviewHold']=note
                if t['kind'] in ('chore','todo'):
                    t['parentConfirmed']=None
                self.history(t,actor,'correction',{'note':note})
            elif scope=='parent/' and action=='confirm':
                if t.get('practiceQuizId'):
                    from practice import _quiz
                    from mastery import is_mastery
                    if is_mastery(_quiz(self,t['practiceQuizId'])):
                        from mastery import confirm_parent
                        confirm_parent(self,_quiz(self,t['practiceQuizId']))
                        return self.view_task(t)
                from parent_review import is_final,complete as complete_parent_review
                if t['kind']=='school' and is_final(t):return self.view_task(t)
                note=clean(body.get('note',''))
                if not t['reportedAt']:
                    raise AppError('Student has not reported this task ready yet.',409)
                t['parentConfirmed']={'at':self.stamp(),'actor':'parent','note':note}
                if t['kind'] in ('chore','todo'):
                    t['completedAt']=self.stamp()
                    t['reviewHold']=''
                self.history(t,'parent','parent_confirmed',t['parentConfirmed'])
                if t['kind']=='school' and not t.get('practiceQuizId'):
                    previous_hold=t.get('reviewHold','')
                    t['reviewHold']=''
                    if previous_hold:self.history(t,'parent','review_resolved',{'note':'Parent confirmed after outside-app review.','previous':previous_hold})
                    complete_parent_review(self,t)
                elif t['kind']=='school' and not self.view_task(t)['needsReview'] and not t['completedAt']:
                    t['completedAt']=self.stamp()
                    self.history(t,'parent','completed',{'note':'Student reported ready and parent confirmed; all required quiz checks passed.'})
            elif scope=='parent/' and action=='return':
                if t['kind']!='school':raise AppError('Use this control for schoolwork.',409)
                if not t['reportedAt'] and not t['parentConfirmed']:
                    if t.get('parentReturned'):return self.view_task(t)
                    raise AppError('Student has not reported this task ready yet.',409)
                previous={key:copy.deepcopy(t.get(key)) for key in ('reportedAt','reportedNote','reportedBy','parentConfirmed','completedAt','completionBasis')}
                t.update(reportedAt=None,reportedNote='',reportedBy=None,parentConfirmed=None,completedAt=None,completionBasis=None,
                         parentReturned={'at':self.stamp(),'actor':'parent'})
                if t.get('practiceQuizId'):
                    from practice import _quiz
                    _quiz(self,t['practiceQuizId'])['parentConfirmed']=None
                self.history(t,'parent','returned_to_river',{'previous':previous})
            elif scope in ('parent/','assistant/') and action=='revoke':
                field=body.get('field')
                allowed = ('understanding','submission','parentConfirmed') if actor=='parent' else ('understanding','submission')
                if field not in allowed:
                    raise AppError('This evidence cannot be changed with this access.',403)
                note=required(body,'note')
                previous=t[field]
                t[field]=None
                t['completedAt']=None
                self.history(t,actor,'evidence_revoked',{'field':field,'note':note,'previous':previous})
            elif scope in ('parent/','assistant/') and action=='resolve':
                note=required(body,'note')
                t['reviewHold']=''
                t['helpNote']=''
                self.history(t,actor,'review_resolved',{'note':note})
            elif scope=='assistant/' and action=='understanding':
                if t.get('practiceQuizId'):
                    from practice import _quiz
                    from mastery import is_mastery
                    quiz=_quiz(self,t['practiceQuizId'])
                    if is_mastery(quiz) and not quiz.get('parentConfirmed'):
                        raise AppError('All answers and final parent confirmation are required first.',409)
                if t['kind']!='school':
                    raise AppError('Understanding checks are for schoolwork.',403)
                if body.get('independent') is not True or body.get('method') not in ('own_words','fresh_example'):
                    raise AppError('Record an independent explanation or fresh example; an assistant-provided answer is not an understanding check.')
                t['understanding']={'at':self.stamp(),'actor':'assistant','goal':required(body,'goal'),'evidence':required(body,'evidence'),
                    'help':clean(body.get('help','')),'method':body['method'],'note':'A brief check of this goal, not a claim of complete mastery.'}
                self.history(t,actor,'understanding_checked',t['understanding'])
                if t['understanding']['help']:
                    t['helpNote']=t['understanding']['help']
            elif scope=='assistant/' and action=='submission':
                if t['kind']!='school':
                    raise AppError('Submission checks are for schoolwork.',403)
                if body.get('observed') is not True or body.get('completeWork') is not True:
                    raise AppError('Observe the portal confirmation and inspect all required work before recording a submission check.')
                source=safe_url(required(body,'sourceUrl'))
                observed=required(body,'observedAt',100)
                try:
                    when=datetime.fromisoformat(observed.replace('Z','+00:00'))
                    if when.tzinfo is None or when>self.now()+timedelta(minutes=1):
                        raise ValueError()
                except ValueError:
                    raise AppError('Use the actual observed confirmation time with its time zone.')
                t['submission']={'at':self.stamp(),'actor':'assistant','evidence':required(body,'evidence'),'sourceUrl':source,'observedAt':observed,
                    'note':'Portal confirmation observed; required work inspected. This does not establish credit or grading.'}
                self.history(t,actor,'submission_confirmed',t['submission'])
            else:
                raise AppError('That action is not available.',404)
            return self.view_task(t)
        match=re.fullmatch(r'/api/(parent/)?braces/(start|pause|reset|confirm|revoke)',path)
        if match:
            parent,action=match.groups()
            if bool(parent)!=(action in ('confirm','revoke')):
                raise AppError('That action needs the appropriate access.',403)
            selected_day=body.get('date',self.today()) if parent else self.today()
            if selected_day>self.today():
                raise AppError('Future braces wearing cannot be confirmed in advance.',409)
            b=self.day(selected_day)['braces']
            if action=='start':
                if not b['running'] and self.braces_view(b)['elapsedSeconds']<3600:
                    b['startedAt']=self.stamp()
                    b['running']=True
                    self.history(b,actor,'timer_started',{'note':'Student reports putting braces on.'})
            elif action=='pause':
                b['elapsedSeconds']=self.braces_view(b)['elapsedSeconds']
                b['running']=False
                b['startedAt']=None
                self.history(b,actor,'timer_paused',{'elapsedSeconds':b['elapsedSeconds']})
            elif action=='reset':
                note=required(body,'note')
                previous=self.braces_view(b)
                previous.pop('history',None)
                b.update(startedAt=None,elapsedSeconds=0,running=False,parentConfirmed=None)
                self.history(b,actor,'timer_corrected',{'note':note,'previous':previous})
            elif action=='confirm':
                b['parentConfirmed']={'at':self.stamp(),'actor':'parent','note':clean(body.get('note',''))}
                self.history(b,actor,'parent_confirmed',b['parentConfirmed'])
            elif action=='revoke':
                note=required(body,'note')
                previous=b['parentConfirmed']
                b['parentConfirmed']=None
                self.history(b,actor,'parent_confirmation_revoked',{'note':note,'previous':previous})
            return self.braces_view(b)
        raise AppError('That action was not found.',404)

class RiverHandler(BaseHTTPRequestHandler):
    server_version='Unstuckery/1.0'
    def setup(self):
        super().setup()
        self.connection.settimeout(30)
    def log_message(self, fmt, *args):
        # Never log request bodies, cookies or credentials.
        pass

    def output(self, data, status=200, cookie=None):
        raw=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie:
            self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(raw)

    def cookie_value(self, name):
        # Google Identity can set an unquoted JSON g_state cookie. SimpleCookie
        # stops parsing there, hiding any later session cookie. Read only our
        # exact cookie name, and reject ambiguous or non-token values.
        values = []
        for header in self.headers.get_all('Cookie', []):
            for part in header.split(';'):
                key, separator, value = part.strip().partition('=')
                if separator and key == name:
                    values.append(value)
        if len(values) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', values[0]):
            return values[0]
        return None

    def session(self):
        name = '__Host-unstuckery' if getattr(self.server.store, 'cloud_auth', None) else 'river_parent'
        return self.cookie_value(name)

    def redirect(self, target):
        self.send_response(303)
        self.send_header('Location', target)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def serve(self, method):
        store=self.server.store
        try:
            host=self.headers.get('Host','')
            cloud = getattr(store, 'cloud_auth', None)
            allowed = {cloud.host} if cloud else {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            expected_origin = cloud.origin if cloud else f'http://{host}'
            if host not in allowed:
                raise AppError('Open this app using its local address.',403)
            origin=self.headers.get('Origin')
            if origin and origin!=expected_origin:
                raise AppError('Requests must come from this local app.',403)
            parsed=urlparse(self.path)
            path=parsed.path
            if not path.startswith('/api/'):
                if method!='GET':
                    raise AppError('Method not available.',405)
                files={'/memory.js':'memory.js','/':'index.html','/index.html':'index.html','/app.js':'app.js','/timer-audio.js':'timer-audio.js','/style.css':'style.css','/practice.js':'practice.js','/practice.css':'practice.css','/backlog.js':'backlog.js','/canvas.js':'canvas.js','/canvas.css':'canvas.css','/photos.js':'photos.js','/photos.css':'photos.css'}
                if cloud:
                    files.update({'/login': 'login.html', '/login.js': 'login.js', '/cloud.js': 'cloud.js', '/parent': 'index.html', '/student': 'index.html'})
                    identity = cloud.status(self.session())
                    if path in ('/', '/index.html', '/parent', '/student', '/login'):
                        role = identity['role']
                        if not role and path != '/login':
                            self.redirect('/login'); return
                        if role and (path in ('/', '/index.html', '/login') or path == '/parent' and role != 'parent'):
                            self.redirect('/' + role); return
                if path not in files:
                    raise AppError('Page not found.',404)
                raw=(ROOT/'static'/files[path]).read_bytes()
                if cloud and files[path] == 'index.html':
                    raw = raw.replace(b'</head>', b'<script src="/cloud.js" defer></script></head>')
                self.send_response(200)
                mime={'html':'text/html','js':'text/javascript','css':'text/css'}[files[path].split('.')[-1]]
                self.send_header('Content-Type',mime+'; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store' if cloud else 'no-cache')
                google_csp = " https://accounts.google.com/gsi/" if cloud and files[path] == 'login.html' else ''
                self.send_header('Content-Security-Policy',f"default-src 'self'; script-src 'self'{google_csp}; style-src 'self' 'unsafe-inline'{google_csp}; img-src 'self' data:; connect-src 'self'{google_csp}; frame-src 'self'{google_csp}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.send_header('Cross-Origin-Opener-Policy', 'same-origin-allow-popups' if google_csp else 'same-origin')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
                self.end_headers()
                self.wfile.write(raw)
                return
            bearer=self.headers.get('Authorization','')
            assistant=bool(bearer) and hmac.compare_digest(bearer,'Bearer '+store.assistant_token)
            planner=bool(bearer and store.planner_token) and hmac.compare_digest(bearer,'Bearer '+store.planner_token)
            manager=bool(bearer and store.manager_token) and hmac.compare_digest(bearer,'Bearer '+store.manager_token)
            if bearer and not (assistant or planner or manager):
                raise AppError('Assistant access did not match.',401)
            if manager:
                from manager_access import allowed_request
                if not allowed_request(method,path):
                    raise AppError('Assistant manager access cannot use this route.',403)
            if path.startswith('/api/manager/') and not manager:
                raise AppError('This action needs assistant manager access.',401)
            if planner:
                from planner import allowed_request
                if not allowed_request(method, path):
                    raise AppError('Planner access is limited to exercise preparation and publication.',403)
            if path.startswith('/api/planner/') and not planner:
                raise AppError('This action needs planner access.',401)
            if bearer and (path.startswith('/api/parent/') or path.startswith('/api/auth')):
                raise AppError('Assistant access cannot use parent controls.',403)
            if path.startswith('/api/assistant/') and not assistant:
                raise AppError('This check needs assistant access.',401)
            if method!='GET' and not (assistant or planner or manager):
                if self.headers.get('X-Student-Request')!='1' or origin!=expected_origin:
                    raise AppError('Open this action from the local app.',403)
            if cloud:
                public_paths = ('/api/health', '/api/auth', '/api/auth/config', '/api/auth/google', '/api/auth/logout')
                if path not in public_paths and not (assistant or planner or manager) and not cloud.status(self.session())['role']:
                    raise AppError('Sign in to The Unstuckery to continue.', 401)
            if path == '/api/canvas/oauth/callback' or path == '/api/parent/canvas' or path.startswith('/api/parent/canvas/'):
                from canvas import handle_request
                handle_request(self,method,parsed)
                return
            with store.lock:
                parent=store.auth_status(self.session())['parent']
                if path.startswith('/api/parent/') and not parent:
                    raise AppError('Unlock parent review to continue.',401)
                if method=='GET':
                    query=parse_qs(parsed.query)
                    day=query.get('date',[None])[0]
                    photo_view=re.fullmatch(r'/api/tasks/([a-zA-Z0-9_-]+)/photos',path)
                    if path.startswith('/api/manager/'):
                        from manager_access import view
                        result=store.transaction(lambda:view(store,path,day))
                    elif path.startswith('/api/planner/'):
                        from planner import view
                        result=store.transaction(lambda:view(store,path,day))
                    elif path.startswith('/api/photos/'):
                        from photos import image_bytes
                        raw=image_bytes(store,path.removeprefix('/api/photos/'))
                        self.send_response(200)
                        self.send_header('Content-Type','image/jpeg')
                        self.send_header('Content-Length',str(len(raw)))
                        self.send_header('Cache-Control','no-store')
                        self.send_header('X-Content-Type-Options','nosniff')
                        self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'self'")
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    elif photo_view:
                        from photos import view as photo_state
                        result=photo_state(store,photo_view[1])
                    elif path=='/api/health':
                        result={'app':'river-after-school','version':1}
                        if getattr(store, 'storage', None):
                            result['workspaceId']=store.storage.workspace_id
                    elif path=='/api/storage':
                        result=store.storage.status() if getattr(store, 'storage', None) else {'mode':'local'}
                    elif path=='/api/browser-memory':
                        result={'values': copy.deepcopy(store.state.get('browserMemory', {})),
                                'workspaceId': store.storage.workspace_id if getattr(store, 'storage', None) else 'local'}
                    elif path=='/api/ai-review':
                        from ai_review import public_status
                        result=public_status(store)
                    elif cloud and path == '/api/auth/config':
                        result, login_cookie = cloud.challenge()
                        self.output(result, cookie=login_cookie); return
                    elif cloud and path == '/api/parent/accounts':
                        result = cloud.accounts()
                    elif path=='/api/auth':
                        result=store.auth_status(self.session())
                    elif path in ('/api/state','/api/assistant/state'):
                        result=store.transaction(lambda:store.view(day))
                    elif path in ('/api/practice','/api/parent/practice','/api/assistant/practice'):
                        from practice import practice_state
                        role='parent' if path.startswith('/api/parent/') else 'assistant' if path.startswith('/api/assistant/') else 'river'
                        result=store.transaction(lambda:practice_state(store,role))
                    elif path in ('/api/parent/backlog','/api/assistant/backlog'):
                        from backlog import view as backlog_view
                        result=store.transaction(lambda:backlog_view(store,'parent' if path.startswith('/api/parent/') else 'assistant'))
                    elif path=='/api/parent/drafts':
                        result=store.drafts()
                    elif path=='/api/parent/history':
                        selected=valid_date(day or store.today())
                        all_objects=list(store.state['tasks'])+list(store.state.get('todos',[]))
                        for d in store.state['days'].values():
                            all_objects.extend(d['chores'])
                            all_objects.append(dict(d['schoolClock'],title='Schoolwork timer',id=d['schoolClock']['date']+'-school-clock'))
                            all_objects.append(dict(d['braces'],title='Braces',id=d['braces']['date']+'-braces'))
                        result={'date':selected,'events':sorted([dict(e,taskId=t['id'],title=t['title']) for t in all_objects for e in t.get('history',[]) if datetime.fromisoformat(e['at']).astimezone(LOCAL_TZ).date().isoformat()==selected],key=lambda e:e['at'],reverse=True)}
                    else:
                        raise AppError('That view was not found.',404)
                    self.output(result)
                    return
                if method not in ('POST','PUT'):
                    raise AppError('Method not available.',405)
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    from photos import MAX_BODY
                    max_body=MAX_BODY if re.fullmatch(r'/api/tasks/[a-zA-Z0-9_-]+/photo-review',path) else 1048576
                    if size<0 or size>max_body:
                        raise AppError('This update is too large.',413)
                    body=json.loads(self.rfile.read(size) or b'{}')
                    if not isinstance(body,dict):
                        raise ValueError()
                except (ValueError,json.JSONDecodeError):
                    raise AppError('Please send a valid update.')
                cookie=None
                if cloud and path == '/api/auth/google':
                    token, target = cloud.login(body, self.cookie_value('__Host-unstuckery-login') or '')
                    cookie = cloud.cookie(token)
                    result = {'ok': True, 'redirect': target}
                elif cloud and path == '/api/auth/logout':
                    result = store.transaction(lambda: cloud.logout(self.session()))
                    cookie = cloud.cookie(logout=True)
                elif cloud and path == '/api/parent/accounts':
                    result = store.transaction(lambda: cloud.set_student(body))
                elif cloud and path.startswith('/api/auth/'):
                    raise AppError('Use Google sign-in for this installation.', 403)
                elif path in ('/api/auth/setup','/api/auth/login'):
                    token=store.transaction(lambda:store.authenticate(body.get('pin'),setup=path.endswith('setup')))
                    cookie=f'river_parent={token}; HttpOnly; SameSite=Strict; Path=/api; Max-Age={PARENT_TTL}'
                    result={'ok':True}
                elif path=='/api/auth/logout':
                    store.sessions.pop(hashlib.sha256((self.session() or '').encode()).hexdigest(),None)
                    cookie='river_parent=; HttpOnly; SameSite=Strict; Path=/api; Max-Age=0'
                    result={'ok':True}
                else:
                    actor='manager' if manager else 'planner' if planner else 'parent' if path.startswith('/api/parent/') else 'assistant' if assistant else 'river'
                    # Assistant may report/help/correct but Student alone checks off schoolwork.
                    if assistant and path.endswith('/complete'):
                        raise AppError('Student checks off schoolwork in Today.',403)
                    if assistant and (path.startswith('/api/braces/') or path.startswith('/api/school-clock/')):
                        raise AppError('Student operates the braces timer in Today.',403)
                    result=store.transaction(lambda:store.mutate(path,body,actor))
                self.output(result,cookie=cookie)
        except (AppError,PracticeError) as exc:
            self.output({'error':exc.message},exc.status)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            self.output({'error':'The update could not be saved. Please try again.'},500)

    def do_GET(self): self.serve('GET')
    def do_POST(self): self.serve('POST')
    def do_PUT(self): self.serve('PUT')
    def do_DELETE(self): self.serve('DELETE')
    def do_OPTIONS(self): self.serve('OPTIONS')

class RiverServer(ThreadingHTTPServer):
    request_queue_size = 32
    def __init__(self, *args, **kwargs):
        self.request_slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def server_close(self):
        if hasattr(self,'canvas'):
            self.canvas.close()
        if hasattr(self,'store') and getattr(self.store,'ai_review_worker',None):
            self.store.ai_review_worker.stop()
        super().server_close()
        if hasattr(self, 'data_lock'):
            self.data_lock.close()


def create_server(data_dir=None, port=4317, now=None, ai_review=False, review_config=None, canvas_transport=None, canvas_vault=None, storage=None, cloud_config=None):
    directory=Path(data_dir or ROOT/'data')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    data_lock=(directory/'.server-lock').open('a')
    try:
        fcntl.flock(data_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        data_lock.close()
        raise RuntimeError('This Student data folder is already open in another server. Use the running app.')
    try:
        server=RiverServer(('127.0.0.1',port),RiverHandler)
        server.data_lock=data_lock
        server.store=RiverStore(directory,now=now)
        server.store.storage=storage
        if cloud_config:
            from cloud_auth import CloudAuth
            server.store.cloud_auth = CloudAuth(server.store, cloud_config)
        from canvas import CanvasConnection
        server.canvas=CanvasConnection(server.store,canvas_transport,canvas_vault or (directory/'canvas-credentials.json' if cloud_config else None))
        if storage:
            storage.checkpoint(server.store, force=True)
            if storage.error:
                raise RuntimeError(storage.error)
        server.store.ai_review_enabled=ai_review
        if ai_review:
            from ai_review import ReviewWorker
            server.store.ai_review_worker=ReviewWorker(server.store,review_config)
            server.store.ai_review_worker.start()
        return server
    except Exception:
        data_lock.close()
        if 'server' in locals():
            server.server_close()
            if hasattr(server, 'store'):
                server.store.db.close()
        raise

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=4317)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--ai-review',action='store_true',help='Automatically review new mastery answers with the Agents API')
    parser.add_argument('--review-config',type=Path,help='Private settings file outside the synced app folder')
    parser.add_argument('--cloud-config', type=Path, help='Private Google sign-in and HTTPS configuration')
    args=parser.parse_args()
    cloud_config = None
    if args.cloud_config:
        from cloud_auth import load_config
        cloud_config = load_config(args.cloud_config)
    server=create_server(args.data_dir,args.port,ai_review=args.ai_review,review_config=args.review_config,cloud_config=cloud_config)
    print(f'River is ready at http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.db.close()

if __name__=='__main__': main()
