"""Read-only Canvas connection and parent-reviewed assignment imports.

Credentials live outside the synced workspace. School records are a separate,
parent-only snapshot; importing never supplies learning or completion evidence.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import secrets
import ssl
import stat
import threading
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

from server import AppError, LOCAL_TZ, clean, required, valid_date

ORIGIN = os.environ.get('UNSTUCKERY_CANVAS_ORIGIN', 'https://canvas.example.edu').rstrip('/')
if not re.fullmatch(r'https://[a-zA-Z0-9.-]+', ORIGIN):
    raise ValueError('UNSTUCKERY_CANVAS_ORIGIN must be an HTTPS school hostname.')
CALLBACK = '/api/canvas/oauth/callback'
SCOPES = [
    'url:GET|/api/v1/users/:user_id/profile',
    'url:GET|/api/v1/users/:user_id/observees',
    'url:GET|/api/v1/users/:user_id/courses',
    'url:GET|/api/v1/courses/:course_id/assignments',
    'url:GET|/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id',
]


def ident(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,19}', str(value)):
        raise AppError('Canvas returned an invalid record identifier.', 502)
    return str(value)


def web_link(value):
    if not isinstance(value, str): return ''
    url = urljoin(ORIGIN, value)
    p = urlparse(url)
    return url if p.scheme in ('http', 'https') and p.hostname and not p.username and not p.password else ''


class PlainInstructions(HTMLParser):
    """Keep readable instructions and resource links, never execute portal HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.hidden = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if self.hidden: return
        values = dict(attrs)
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'tr'): self.parts.append('\n')
        if tag == 'li': self.parts.append('• ')
        url = web_link(values.get('href', '') if tag == 'a' else values.get('src', '') if tag in ('iframe', 'img') else '')
        if url and url != ORIGIN and url not in self.links: self.links.append(url)
        if tag == 'img' and values.get('alt'): self.parts.append(values['alt'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden: self.hidden -= 1
        if not self.hidden and tag in ('p', 'div', 'li', 'tr'): self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def instructions(html):
    parser = PlainInstructions()
    parser.feed(html if isinstance(html, str) else '')
    text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    return text[:30000], parser.links[:40]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CanvasTransport:
    def __init__(self):
        # Homebrew Python may not have a CA bundle; keep TLS verification enabled.
        cafile = os.environ.get('SSL_CERT_FILE') or ('/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None)
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))

    def request(self, url, token=None, form=None):
        p = urlparse(url)
        if p.scheme != 'https' or p.netloc != urlparse(ORIGIN).netloc or p.fragment or p.username or p.password:
            raise AppError('Canvas returned an unexpected destination. Nothing was sent there.', 502)
        if form is not None:
            if p.path != '/login/oauth2/token' or token:
                raise AppError('This connection only reads school records.', 403)
        elif not re.fullmatch(r'/api/v1/(?:users/(?:self|[0-9]+)/(?:profile|observees|courses)|courses/[0-9]+/assignments(?:/[0-9]+/submissions/[0-9]+)?)', p.path):
            raise AppError('This Canvas operation is not supported.', 403)
        headers = {'Accept': 'application/json', 'User-Agent': 'RiverAfterSchool/1.0'}
        if token: headers['Authorization'] = 'Bearer ' + token
        data = urlencode(form).encode() if form is not None else None
        if data: headers['Content-Type'] = 'application/x-www-form-urlencoded'
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=12) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000: raise AppError('Canvas returned too much data. Try a smaller refresh.', 502)
                return json.loads(raw), response.headers.get('Link', '')
        except HTTPError as exc:
            status = exc.code
            exc.close()
            messages = {401: 'Canvas authorization expired or was revoked. Reconnect this account.',
                        403: 'Canvas does not allow this account to read these records.',
                        404: 'This Canvas record is unavailable to this account.',
                        429: 'Canvas is limiting requests. Wait a little before refreshing again.'}
            raise AppError(messages.get(status, 'Canvas could not complete the request. Try again later.'), status if status in messages else 502) from None
        except (URLError, OSError, ValueError):
            raise AppError('Canvas could not be reached securely. Check the connection and try again.', 502) from None


class Vault:
    def __init__(self, data_dir, path=None):
        key = hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:20]
        self.path = Path(path) if path else Path.home() / 'Library/Application Support/RiverAfterSchool/canvas' / (key + '.json')

    def read(self):
        if not self.path.exists(): return {}
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AppError('Canvas settings must be a private file owned by this Mac account (mode 600).', 503)
        try: return json.loads(self.path.read_text())
        except (ValueError, OSError): raise AppError('The private Canvas settings could not be read.', 503) from None

    def write(self, value):
        if getattr(self, 'storage', None):
            self.storage.ensure_write()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.read()  # Reject unsafe existing files, including symlinks.
        tmp = self.path.with_name(self.path.name + '.' + secrets.token_hex(8))
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(value, f); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)
            if getattr(self, 'storage', None):
                self.storage.checkpoint(self.store)
        finally:
            if tmp.exists(): tmp.unlink()


def cached_state(store):
    return store.state.get('canvas', {'student': None, 'origin':ORIGIN, 'accounts': {}, 'courses': [], 'items': [], 'revision': 0, 'lastSyncAt': None, 'warnings': []})


def source_key(url):
    p = urlparse(url or '')
    match = re.match(r'^/courses/([0-9]+)/assignments/([0-9]+)(?:/|$)', p.path)
    return ':'.join(match.groups()) if p.scheme == 'https' and p.netloc == urlparse(ORIGIN).netloc and match else None


def linked_tasks(store, key):
    return [t for t in store.state['tasks'] if not t.get('practiceQuizId') and t.get('scope') != 'practice'
            and (t.get('canvasSource', {}).get('key') == key or source_key(t.get('sourceUrl')) == key)]


def submission_data(raw, student_id):
    if not isinstance(raw, dict) or str(raw.get('user_id')) != student_id: return None
    result = {key: raw.get(key) for key in ('workflow_state', 'submitted_at', 'graded_at', 'score', 'grade', 'attempt', 'late', 'missing', 'excused')}
    result['userId'] = student_id
    result['comments'] = [{'author': clean(c.get('author_name', ''), 250), 'text': clean(c.get('comment', ''), 8000), 'at': c.get('created_at')}
                          for c in raw.get('submission_comments', []) if isinstance(c, dict)][:30]
    result['files'] = [{'name': clean(a.get('display_name') or a.get('filename') or 'Submitted file', 500), 'url': web_link(a.get('url', ''))}
                       for a in raw.get('attachments', []) if isinstance(a, dict)][:30]
    if 'cached_due_date' in raw: result['dueAt'] = raw['cached_due_date']
    return result


def normalize_assignment(raw, course, student_id, role, stamp):
    aid, cid = ident(raw.get('id')), ident(course['id'])
    text, links = instructions(raw.get('description'))
    candidates = [raw.get('submission')]
    for key in ('submissions', 'observed_users'):
        candidates.extend(raw.get(key) or [])
    submission = next((s for candidate in candidates if (s := submission_data(candidate, student_id))), None)
    personal = role == 'student' or submission is not None and 'dueAt' in submission
    due = submission['dueAt'] if submission and 'dueAt' in submission else raw.get('due_at')
    item = {'key': cid + ':' + aid, 'assignmentId': aid, 'courseId': cid, 'course': clean(course.get('name', 'Course'), 250),
            'studentId': student_id, 'title': clean(raw.get('name') or 'Untitled assignment', 500), 'instructions': text,
            'links': links, 'sourceUrl': f'{ORIGIN}/courses/{cid}/assignments/{aid}', 'dueAt': due,
            'dueBasis': 'student' if personal else 'course', 'unlockAt': raw.get('unlock_at'), 'lockAt': raw.get('lock_at'),
            'locked': raw.get('locked_for_user') is True, 'pointsPossible': raw.get('points_possible'),
            'submissionTypes': raw.get('submission_types') or [], 'submission': submission, 'fetchedAt': stamp,
            'accountRole': role, 'updatedAt': raw.get('updated_at'), 'available': True}
    item['fingerprint'] = hashlib.sha256(json.dumps({k: v for k, v in item.items() if k != 'fetchedAt'}, sort_keys=True).encode()).hexdigest()
    return item


class CanvasConnection:
    def __init__(self, store, transport=None, vault_path=None):
        self.store, self.transport = store, transport or CanvasTransport()
        self.vault = Vault(store.data_dir, vault_path)
        self.vault.storage = getattr(store, 'storage', None)
        self.vault.store = store
        self.lock = threading.RLock()
        self.pending = {}
        self.job = {'running': False, 'message': ''}
        self.stop = threading.Event()
        self.worker = None

    def close(self):
        self.stop.set()
        if self.worker: self.worker.join(timeout=15)

    def require_parent(self, session):
        with self.store.lock:
            if not self.store.auth_status(session)['parent']:
                raise AppError('Unlock parent review to continue.', 401)

    def get(self, path, token, params=None, deadline=None):
        url = ORIGIN + path + ('?' + urlencode(params or {}, doseq=True) if params else '')
        rows, visited = [], set()
        for _ in range(40):
            if url in visited or self.stop.is_set() or deadline and time.monotonic() > deadline:
                raise AppError('The Canvas refresh stopped before all records were read. Previous records were kept.', 502)
            visited.add(url)
            result, header = self.transport.request(url, token=token)
            if not isinstance(result, list):
                if visited and len(visited) > 1: raise AppError('Canvas returned an incomplete page.', 502)
                return result
            rows.extend(result)
            if len(rows) > 5000: raise AppError('Canvas returned too many records in one list.', 502)
            next_links = re.findall(r'<([^>]+)>\s*;\s*rel="next"', header)
            if not next_links: return rows
            url = urljoin(url, next_links[0])
            # Pagination may change query parameters, never origin or endpoint.
            p = urlparse(url)
            if p.scheme != 'https' or p.netloc != urlparse(ORIGIN).netloc or p.path != path or p.fragment:
                raise AppError('Canvas returned an unexpected next page. Previous records were kept.', 502)
        raise AppError('Canvas has more pages than this refresh can safely read.', 502)

    def access_token(self, role):
        private = self.vault.read()
        account = private.get('accounts', {}).get(role)
        if not account: raise AppError('Connect a Canvas account first.', 409)
        if account.get('expiresAt', float('inf')) <= time.time() + 60:
            oauth = private.get('oauth', {})
            if not account.get('refresh_token'): raise AppError('Reconnect this Canvas account.', 409)
            result, _ = self.transport.request(ORIGIN + '/login/oauth2/token', form={
                'grant_type': 'refresh_token', 'client_id': oauth['clientId'], 'client_secret': oauth['clientSecret'],
                'refresh_token': account['refresh_token'], 'redirect_uri': account['redirectUri']})
            self.update_tokens(account, result)
            self.vault.write(private)
        return account['access_token']

    @staticmethod
    def update_tokens(account, result):
        token = result.get('access_token')
        if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
            raise AppError('Canvas did not return valid authorization.', 502)
        account['access_token'] = token
        if result.get('refresh_token'): account['refresh_token'] = result['refresh_token']
        if result.get('expires_in'): account['expiresAt'] = time.time() + int(result['expires_in'])

    def connect(self, role, account, session):
        profile = self.get('/api/v1/users/self/profile', account['access_token'])
        uid = ident(profile.get('id'))
        observees = self.get('/api/v1/users/self/observees', account['access_token']) if role == 'parent' else []
        choices = [{'id': ident(u['id']), 'name': clean(u.get('name', 'Student'), 250)} for u in observees]
        if role == 'parent' and not choices:
            raise AppError('This account has no linked students. Connect the parent account that observes Student.', 409)
        self.require_parent(session)
        with self.store.lock:
            state = copy.deepcopy(cached_state(self.store))
            if role == 'student' and (not state['student'] or uid != state['student']['id']):
                raise AppError('This is not the selected student’s Canvas account. Sign in as Student and try again.', 409)
            if role == 'parent' and state['student'] and state['student']['id'] not in [u['id'] for u in choices]:
                raise AppError('This parent account does not observe the selected student.', 409)
            if role == 'parent' and len(choices) == 1 and not state['student']: state['student'] = choices[0]
            state['accounts'][role] = {'id': uid, 'name': clean(profile.get('name', role), 250), 'mode': account['mode'], 'connectedAt': self.store.stamp()}
            if role == 'parent': state['observees'] = choices
            private = self.vault.read()
            private.setdefault('accounts', {})[role] = dict(account, userId=uid)
            self.vault.write(private)
            self.store.transaction(lambda: self.store.state.update(canvas=state))

    def status(self):
        with self.store.lock:
            result = copy.deepcopy(cached_state(self.store))
            for item in result['items']:
                linked = linked_tasks(self.store, item['key'])
                item['plannedTasks'] = [{'id': t['id'], 'title': t['title'], 'date': t['planDate'], 'completed': bool(t['completedAt'])} for t in linked]
                item['changedSinceImport'] = any(t.get('canvasSource', {}).get('fingerprint') not in (None, item['fingerprint']) for t in linked)
            result['origin'] = ORIGIN
            result['sync'] = copy.deepcopy(self.job)
        try:
            private = self.vault.read()
            result['oauthConfigured'] = bool(private.get('oauth'))
            for role, account in result['accounts'].items(): account['connected'] = role in private.get('accounts', {})
        except AppError as exc:
            result['oauthConfigured'] = False
            result['settingsError'] = exc.message
            for account in result['accounts'].values(): account['connected'] = False
        return result

    def sync(self, session):
        if not self.lock.acquire(blocking=False):
            if self.job['running']: return {'ok': True, 'running': True}
            raise AppError('The Canvas connection is busy. Try again in a moment.', 409)
        try:
            self.require_parent(session)
            if self.job['running']: return {'ok': True, 'running': True}
            state = cached_state(self.store)
            if not state['student']: raise AppError('Choose Student from the linked students first.', 409)
            private = self.vault.read()
            role = 'student' if 'student' in private.get('accounts', {}) else 'parent'
            if role not in private.get('accounts', {}): raise AppError('Connect a Canvas account first.', 409)
            self.job = {'running': True, 'message': 'Reading Canvas courses…', 'startedAt': self.store.stamp()}
            self.worker = threading.Thread(target=self.run_sync, args=(role, copy.deepcopy(state['student']), session), daemon=True)
            self.worker.start()
            return {'ok': True, 'running': True}
        finally:
            self.lock.release()

    def run_sync(self, role, student, session):
        try:
            with self.lock:
                token = self.access_token(role)
                deadline = time.monotonic() + 150
                courses = self.get('/api/v1/users/' + student['id'] + '/courses', token, {'enrollment_state': 'active', 'per_page': 100}, deadline)
                courses = [{'id': ident(c['id']), 'name': clean(c.get('name', 'Course'), 250)} for c in courses if c.get('name')]
                items, warnings, successful = [], [], set()
                stamp = self.store.stamp()
                for course in courses:
                    with self.store.lock: self.job['message'] = 'Reading ' + course['name'] + '…'
                    try:
                        rows = self.get('/api/v1/courses/' + course['id'] + '/assignments', token,
                                        {'per_page': 100, 'include[]': ['submission', 'observed_users'] if role == 'parent' else ['submission']}, deadline)
                        batch = [normalize_assignment(row, course, student['id'], role, stamp) for row in rows if row.get('published') is not False]
                        items.extend(batch); successful.add(course['id'])
                    except AppError as exc:
                        if exc.status not in (403, 404): raise
                        warnings.append(course['name'] + ': ' + exc.message)
                self.require_parent(session)
                if self.stop.is_set(): return
                with self.store.lock:
                    def commit():
                        state = copy.deepcopy(cached_state(self.store))
                        new_keys = {i['key'] for i in items}
                        for old in state['items']:
                            if old['key'] not in new_keys:
                                saved = copy.deepcopy(old)
                                saved['available'] = False
                                saved['unavailableReason'] = 'Not returned by the latest refresh; open Canvas to check.' if old['courseId'] in successful else 'This course could not be refreshed; showing the earlier record.'
                                items.append(saved)
                        state.update(items=items, courses=courses, warnings=warnings, lastSyncAt=stamp, revision=state['revision'] + 1)
                        self.store.state['canvas'] = state
                    self.store.transaction(commit)
                    self.job = {'running': False, 'message': 'Refresh complete.' if not warnings else 'Refresh completed with some unavailable courses.'}
        except AppError as exc:
            with self.store.lock: self.job = {'running': False, 'message': exc.message, 'error': True}
        except Exception:
            with self.store.lock: self.job = {'running': False, 'message': 'Canvas could not be refreshed. Previous records were kept.', 'error': True}

    def import_item(self, body):
        state = cached_state(self.store)
        if body.get('revision') != state['revision']: raise AppError('Canvas records changed. Review the refreshed assignment before adding it.', 409)
        item = next((i for i in state['items'] if i['key'] == body.get('key')), None)
        if not item or not item['available']: raise AppError('Refresh this assignment in Canvas before adding it.', 409)
        existing = linked_tasks(self.store, item['key'])
        if existing: return {'ok': True, 'alreadyPlanned': True, 'taskId': existing[0]['id']}
        fields = body.get('task')
        if not isinstance(fields, dict): raise AppError('Review the assignment and choose a day first.')
        fields = dict(fields, sourceUrl=item['sourceUrl'], assignmentId='canvas:' + item['key'], requirements={'parent': True, 'submission': False, 'understanding': False})
        if valid_date(fields.get('planDate')) < self.store.today(): raise AppError('Choose today or a future planning date.')
        fields['sourceNote'] = f"Canvas {item['accountRole']} account, read {item['fetchedAt']}. " + ('Student-specific deadline.' if item['dueBasis'] == 'student' else 'Course deadline; student-specific deadline needs checking.')
        task = self.store.create_task(fields)
        saved = self.store.task(task['id'])
        saved['canvasSource'] = {key: copy.deepcopy(item[key]) for key in ('key', 'studentId', 'fingerprint', 'fetchedAt', 'dueAt', 'dueBasis', 'submission')}
        self.store.history(saved, 'parent', 'canvas_imported', saved['canvasSource'])
        return {'ok': True, 'taskId': task['id']}

    def begin_oauth(self, role, session, base):
        if role not in ('parent', 'student'): raise AppError('Choose the parent or student account.')
        private = self.vault.read()
        if not private.get('oauth'): raise AppError('Enter the school-issued app credentials first.', 409)
        if role == 'student' and not cached_state(self.store)['student']: raise AppError('Connect the parent account and choose Student first.', 409)
        nonce = secrets.token_urlsafe(32)
        redirect = base + CALLBACK
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > time.time()}
        self.pending[nonce] = {'role': role, 'session': session, 'expires': time.time() + 600, 'redirectUri': redirect}
        return {'url': ORIGIN + '/login/oauth2/auth?' + urlencode({'client_id': private['oauth']['clientId'], 'response_type': 'code',
                    'redirect_uri': redirect, 'state': nonce, 'scope': ' '.join(SCOPES), 'force_login': '1', 'purpose': 'Student after-school planning'})}

    def callback(self, query):
        with self.lock:
            state = self.pending.pop(query.get('state', [''])[0], None)
            if not state or state['expires'] < time.time(): raise AppError('This Canvas sign-in expired. Start again from Parent review.', 409)
            self.require_parent(state['session'])
            if query.get('error') or not query.get('code'): raise AppError('Canvas sign-in was not completed. Return to Parent review to try again.', 409)
            oauth = self.vault.read()['oauth']
            response, _ = self.transport.request(ORIGIN + '/login/oauth2/token', form={'grant_type': 'authorization_code',
                'client_id': oauth['clientId'], 'client_secret': oauth['clientSecret'], 'redirect_uri': state['redirectUri'], 'code': query['code'][0]})
            account = {'mode': 'oauth', 'redirectUri': state['redirectUri']}
            self.update_tokens(account, response)
            self.connect(state['role'], account, state['session'])

    def action(self, action, body, session, base):
        self.require_parent(session)
        if action == 'sync': return self.sync(session)
        if action == 'import':
            with self.store.lock: return self.store.transaction(lambda: self.import_item(body))
        # A running refresh owns this lock. Fail quickly instead of blocking the UI.
        if not self.lock.acquire(blocking=False): raise AppError('Wait for the Canvas refresh to finish first.', 409)
        try:
            if action == 'test-connect':
                if body.get('ownAccount') is not True: raise AppError('Use a token from your own parent account for this personal test.')
                token = required(body, 'token', 8000)
                if re.search(r'\s', token): raise AppError('The Canvas token cannot contain spaces or line breaks.')
                self.connect('parent', {'mode': 'personal_test', 'access_token': token}, session)
                return {'ok': True}
            if action == 'student':
                with self.store.lock:
                    def choose():
                        state = copy.deepcopy(cached_state(self.store))
                        choice = next((u for u in state.get('observees', []) if u['id'] == body.get('id')), None)
                        if not choice: raise AppError('Choose one of the linked students.')
                        if state['student'] and state['student']['id'] != choice['id']:
                            raise AppError('This app is already linked to another student. Use a separate app data folder.', 409)
                        state['student'] = choice; self.store.state['canvas'] = state
                    self.store.transaction(choose)
                return {'ok': True}
            if action == 'oauth-config':
                private = self.vault.read()
                if any(a.get('mode') == 'oauth' for a in private.get('accounts', {}).values()):
                    raise AppError('Disconnect the existing OAuth accounts before replacing app credentials.', 409)
                private['oauth'] = {'clientId': required(body, 'clientId', 250), 'clientSecret': required(body, 'clientSecret', 8000)}
                self.vault.write(private)
                self.pending.clear()
                return {'ok': True}
            if action == 'oauth-start': return self.begin_oauth(body.get('role'), session, base)
            if action == 'disconnect':
                role = body.get('role')
                if role not in ('parent', 'student'): raise AppError('Choose an account to disconnect.')
                private = self.vault.read(); private.get('accounts', {}).pop(role, None); self.vault.write(private)
                self.pending.clear()
                with self.store.lock:
                    def remove():
                        state = copy.deepcopy(cached_state(self.store)); state['accounts'].pop(role, None); self.store.state['canvas'] = state
                    self.store.transaction(remove)
                return {'ok': True}
            if action == 'submission':
                snapshot = copy.deepcopy(cached_state(self.store))
                item = next((i for i in snapshot['items'] if i['key'] == body.get('key')), None)
                if not item or not item['available']: raise AppError('Refresh this assignment first.', 409)
                private = self.vault.read()
                role = 'student' if 'student' in private.get('accounts', {}) else 'parent'
                raw = self.get('/api/v1/courses/' + item['courseId'] + '/assignments/' + item['assignmentId'] + '/submissions/' + item['studentId'],
                               self.access_token(role), {'include[]': ['submission_comments', 'submission_history']})
                data = submission_data(raw, item['studentId'])
                if data is None: raise AppError('Canvas did not return this student’s submission.', 502)
                self.require_parent(session)
                # Separate observation, never overwrites a task's verified submission.
                return {'submission': data, 'observedAt': self.store.stamp(), 'accountRole': role}
            raise AppError('Canvas action not found.', 404)
        finally: self.lock.release()


def handle_request(handler, method, parsed):
    connection = handler.server.canvas
    if parsed.path == CALLBACK:
        if method != 'GET': raise AppError('Method not available.', 405)
        # Single-use OAuth state is bound to a still-valid parent session. The
        # SameSite=Strict cookie intentionally is not required on the redirect.
        try:
            connection.callback(parse_qs(parsed.query))
            outcome = 'connected'
        except AppError:
            outcome = 'failed'
        handler.send_response(303)
        handler.send_header('Location', '/#canvas-' + outcome)
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('Content-Length', '0')
        handler.end_headers()
        return
    connection.require_parent(handler.session())
    if method == 'GET' and parsed.path == '/api/parent/canvas':
        handler.output(connection.status()); return
    if method != 'POST': raise AppError('Method not available.', 405)
    try:
        size = int(handler.headers.get('Content-Length', '0'))
        if size < 0 or size > 65536: raise AppError('This Canvas update is too large.', 413)
        body = json.loads(handler.rfile.read(size) or b'{}')
        if not isinstance(body, dict): raise ValueError()
    except (ValueError, json.JSONDecodeError): raise AppError('Please send a valid Canvas update.')
    handler.output(connection.action(parsed.path.removeprefix('/api/parent/canvas/'), body, handler.session(), handler.server.store.cloud_auth.origin if getattr(handler.server.store, 'cloud_auth', None) else 'http://' + handler.headers['Host']))
