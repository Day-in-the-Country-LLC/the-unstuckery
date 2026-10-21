"""Canvas transport, identity, private data, refresh and planning boundaries.

Every portal response is synthetic; tests never contact school services.
"""
import copy
import json
import os
import secrets
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from server import RiverStore, AppError, create_server
from canvas import CanvasConnection, CanvasTransport, Vault, ORIGIN, NoRedirect, normalize_assignment, instructions


class FakeCanvas:
    def __init__(self):
        self.calls = []
        self.student = '42'
        self.denied = set()
        self.next = ''
        self.rows = [{'id': 7, 'name': 'Synthetic worksheet', 'description': '<p>Answer questions 1–3.</p>',
                      'due_at': '2026-09-12T02:00:00Z', 'points_possible': 10, 'submission_types': ['online_upload'],
                      'submissions': [{'user_id': 999, 'score': 100, 'workflow_state': 'graded'},
                                      {'user_id': 42, 'score': 0, 'workflow_state': 'submitted', 'submitted_at': '2026-09-10T18:00:00Z',
                                       'cached_due_date': '2026-09-13T02:00:00Z', 'missing': False}]}]

    def request(self, url, token=None, form=None):
        p = urlparse(url); self.calls.append((url, token, form))
        if p.path in self.denied: raise AppError('Canvas does not allow this account to read these records.', 403)
        if form is not None:
            return {'access_token': 'student-secret' if form.get('code') == 'student-code' else 'parent-secret', 'expires_in': 3600, 'refresh_token': 'refresh-secret'}, ''
        if p.path.endswith('/profile'):
            return {'id': int(self.student) if token == 'student-secret' else 8, 'name': 'Synthetic student' if token == 'student-secret' else 'Synthetic parent'}, ''
        if p.path.endswith('/observees'): return [{'id': 42, 'name': 'Synthetic student'}], ''
        if p.path.endswith('/courses'): return [{'id': 3, 'name': 'Synthetic science'}], ''
        if p.path.endswith('/assignments'):
            if 'page=2' in p.query: return [dict(self.rows[0], id=9, name='Second page task')], ''
            return copy.deepcopy(self.rows), self.next
        if '/submissions/' in p.path:
            return {'user_id': 42, 'submitted_at': '2026-09-10T18:00:00Z', 'workflow_state': 'submitted',
                    'submission_comments': [{'author_name': 'Synthetic teacher', 'comment': 'Check question 3.'}],
                    'attachments': [{'display_name': 'worksheet.pdf', 'url': 'https://files.example/worksheet.pdf'}]}, ''
        raise AssertionError(p.path)


class CanvasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 9, 11, 17, tzinfo=timezone.utc)
        self.store = RiverStore(Path(self.tmp.name) / 'data', now=lambda: self.now)
        self.session = self.store.authenticate(f'{secrets.randbelow(10000):04d}', setup=True)
        self.fake = FakeCanvas()
        self.connection = CanvasConnection(self.store, self.fake, Path(self.tmp.name) / 'private' / 'canvas.json')

    def tearDown(self):
        self.connection.close(); self.store.db.close(); self.tmp.cleanup()

    def action(self, name, body=None):
        return self.connection.action(name, body or {}, self.session, 'http://127.0.0.1:4317')

    def connect(self):
        self.action('test-connect', {'token': 'parent-secret', 'ownAccount': True})

    def refresh(self):
        self.action('sync'); self.connection.worker.join(3)
        self.assertFalse(self.connection.job['running'])

    def plan(self, key='3:7'):
        return self.action('import', {'key': key, 'revision': self.connection.status()['revision'],
            'task': {'title': 'Finish questions 1–3', 'subject': 'Science', 'instructions': 'Answer the next three questions.',
                     'scope': 'work_block', 'planDate': '2026-09-11', 'dueDate': '2026-09-12', 'plannedMinutes': 20}})

    def test_connect_validates_identity_and_keeps_credentials_out_of_state(self):
        before = copy.deepcopy(self.store.view())
        self.connect()
        self.assertEqual(before, self.store.view())
        status = self.connection.status()
        self.assertEqual(status['student']['id'], '42')
        for output in (json.dumps(status), json.dumps(self.store.state), json.dumps(self.store.view())):
            self.assertNotIn('parent-secret', output)
        self.assertEqual(os.stat(self.connection.vault.path).st_mode & 0o777, 0o600)
        with self.assertRaises(AppError): self.action('test-connect', {'token': 'parent-secret'})
        with self.assertRaises(AppError): self.action('student', {'id': '999'})

    def test_parent_refresh_uses_only_selected_student_and_preserves_personal_deadline(self):
        self.connect(); self.refresh()
        item = self.connection.status()['items'][0]
        self.assertEqual(item['submission']['score'], 0)
        self.assertEqual(item['submission']['workflow_state'], 'submitted')
        self.assertEqual(item['dueAt'], '2026-09-13T02:00:00Z')
        self.assertEqual(item['dueBasis'], 'student')
        self.assertIn('/users/42/courses', self.fake.calls[-2][0])
        self.assertEqual(len(self.store.state['tasks']), 0)

    def test_unknown_submission_and_course_deadline_are_not_missing_or_personalized(self):
        self.fake.rows[0].pop('submissions')
        self.connect(); self.refresh()
        item = self.connection.status()['items'][0]
        self.assertIsNone(item['submission']); self.assertEqual(item['dueBasis'], 'course')
        self.fake.rows[0]['submissions'] = [{'user_id': 42, 'cached_due_date': None}]
        self.refresh(); item = self.connection.status()['items'][0]
        self.assertIsNone(item['dueAt']); self.assertEqual(item['dueBasis'], 'student')

    def test_import_is_parent_reviewed_idempotent_and_never_completes_work(self):
        self.connect(); self.refresh()
        result = self.plan(); second = self.plan()
        self.assertEqual(result['taskId'], second['taskId']); self.assertTrue(second['alreadyPlanned'])
        task = self.store.task(result['taskId'])
        self.assertEqual(task['scope'], 'work_block'); self.assertEqual(task['plannedMinutes'], 20)
        self.assertEqual(task['requirements'], {'parent': True, 'submission': False, 'understanding': False})
        for field in ('submission', 'understanding', 'reportedAt', 'parentConfirmed', 'completedAt'): self.assertIsNone(task[field])
        self.assertEqual(task['canvasSource']['submission']['score'], 0)
        self.assertEqual(len(self.store.state['tasks']), 1)
        self.assertEqual(self.connection.status()['items'][0]['plannedTasks'][0]['id'], task['id'])
        self.store.transaction(lambda: self.store.mutate('/api/tasks/' + task['id'] + '/report', {}, 'river'))
        self.store.transaction(lambda: self.store.mutate('/api/parent/tasks/' + task['id'] + '/confirm', {}, 'parent'))
        self.assertTrue(task['completedAt']); self.assertIsNone(task['submission'])

    def test_existing_manual_task_link_prevents_second_import_and_refresh_preserves_history(self):
        existing = self.store.create_task({'title': 'Existing worksheet', 'subject': 'Science', 'instructions': 'Earlier plan.',
                                           'sourceUrl': ORIGIN + '/courses/3/assignments/7/submissions/42'})
        before = copy.deepcopy(self.store.state['tasks'])
        self.connect(); self.refresh(); result = self.plan()
        self.assertEqual(existing['id'], result['taskId'])
        self.fake.rows[0]['description'] = '<p>Teacher changed instructions.</p>'
        self.refresh()
        self.assertEqual(before, self.store.state['tasks'])

    def test_stale_preview_rejected_and_source_changes_flagged_without_overwriting_plan(self):
        self.connect(); self.refresh(); result = self.plan()
        old = copy.deepcopy(self.store.task(result['taskId']))
        self.fake.rows[0]['description'] = '<p>New instructions.</p>'; self.refresh()
        self.assertTrue(self.connection.status()['items'][0]['changedSinceImport'])
        self.assertEqual(old, self.store.task(result['taskId']))
        with self.assertRaises(AppError): self.action('import', {'key': '3:7', 'revision': 1, 'task': {}})

    def test_practice_using_same_source_does_not_replace_the_actual_assignment(self):
        self.store.create_task({'title': 'Extra practice', 'subject': 'Science', 'instructions': 'Review a concept.',
                                'scope': 'practice', 'sourceUrl': ORIGIN + '/courses/3/assignments/7'})
        self.connect(); self.refresh(); self.plan()
        self.assertEqual(len(self.store.state['tasks']), 2)

    def test_refresh_after_parent_logout_cannot_commit_new_records(self):
        self.connect(); self.refresh()
        before = copy.deepcopy(self.store.state['canvas'])
        original = self.fake.request
        def revoke(url, **kwargs):
            result = original(url, **kwargs)
            if urlparse(url).path.endswith('/assignments'): self.store.sessions.clear()
            return result
        self.fake.request = revoke
        self.refresh()
        self.assertEqual(before, self.store.state['canvas'])
        self.assertTrue(self.connection.job['error'])

    def test_pagination_is_complete_and_foreign_next_link_never_gets_a_token(self):
        self.connect()
        self.fake.next = '<' + ORIGIN + '/api/v1/courses/3/assignments?page=2>; rel="next"'
        self.refresh(); self.assertEqual(len(self.connection.status()['items']), 2)
        before = copy.deepcopy(self.store.state['canvas'])
        self.fake.next = '<https://evil.example/steal>; rel="next"'
        self.refresh()
        self.assertTrue(self.connection.job['error']); self.assertEqual(before, self.store.state['canvas'])
        self.assertFalse(any('evil.example' in call[0] for call in self.fake.calls))

    def test_unavailable_records_are_retained_without_deleting_tasks(self):
        self.connect(); self.refresh(); self.plan()
        before = copy.deepcopy(self.store.state['tasks'])
        self.fake.denied.add('/api/v1/courses/3/assignments'); self.refresh()
        status = self.connection.status()
        self.assertEqual(len(status['warnings']), 1); self.assertFalse(status['items'][0]['available'])
        self.assertEqual(before, self.store.state['tasks'])
        with self.assertRaises(AppError): self.plan()

    def test_submission_feedback_is_read_only_and_identity_checked(self):
        self.connect(); self.refresh(); result = self.plan()
        before = copy.deepcopy(self.store.state)
        result = self.action('submission', {'key': '3:7'})
        self.assertEqual(result['submission']['comments'][0]['text'], 'Check question 3.')
        self.assertEqual(before, self.store.state)
        self.assertNotIn('https://files.example', [c[0] for c in self.fake.calls])

    def test_oauth_nonce_is_single_use_bound_to_parent_and_student_identity(self):
        self.connect(); self.action('oauth-config', {'clientId': 'fake-client', 'clientSecret': 'fake-secret'})
        start = self.action('oauth-start', {'role': 'student'}); query = parse_qs(urlparse(start['url']).query)
        self.assertNotIn('fake-secret', start['url'])
        self.assertTrue(all(scope.startswith('url:GET|') for scope in query['scope'][0].split()))
        self.connection.callback({'state': query['state'], 'code': ['student-code']})
        self.assertTrue(self.connection.status()['accounts']['student']['connected'])
        with self.assertRaises(AppError): self.connection.callback({'state': query['state'], 'code': ['student-code']})
        start = self.action('oauth-start', {'role': 'student'}); query = parse_qs(urlparse(start['url']).query)
        self.fake.student = '99'
        with self.assertRaises(AppError): self.connection.callback({'state': query['state'], 'code': ['student-code']})
        self.assertEqual(self.connection.status()['accounts']['student']['id'], '42')
        start = self.action('oauth-start', {'role': 'parent'}); query = parse_qs(urlparse(start['url']).query)
        self.store.sessions.clear()
        with self.assertRaises(AppError): self.connection.callback({'state': query['state'], 'code': ['parent-code']})

    def test_oauth_refresh_and_disconnect(self):
        self.connect(); self.action('oauth-config', {'clientId': 'fake-client', 'clientSecret': 'fake-secret'})
        start = self.action('oauth-start', {'role': 'parent'}); query = parse_qs(urlparse(start['url']).query)
        self.connection.callback({'state': query['state'], 'code': ['parent-code']})
        private = self.connection.vault.read(); private['accounts']['parent']['expiresAt'] = 0; self.connection.vault.write(private)
        self.assertEqual(self.connection.access_token('parent'), 'parent-secret')
        self.assertEqual(self.fake.calls[-1][2]['grant_type'], 'refresh_token')
        self.refresh(); self.plan(); tasks = copy.deepcopy(self.store.state['tasks'])
        self.action('disconnect', {'role': 'parent'})
        self.assertNotIn('parent', self.connection.vault.read()['accounts'])
        self.assertEqual(tasks, self.store.state['tasks'])
        with self.assertRaises(AppError): self.action('sync')

    def test_untrusted_html_is_text_and_unsafe_links_are_removed(self):
        text, links = instructions('<script>steal()</script><p>Read &amp; explain.</p><a href="javascript:steal()">click</a><iframe src="https://docs.google.com/example"></iframe>')
        self.assertNotIn('steal', text); self.assertIn('Read & explain.', text)
        self.assertEqual(links, ['https://docs.google.com/example'])
        transport = CanvasTransport()
        for url, form in [('https://evil.example/api/v1/courses', None), (ORIGIN + '/api/v1/courses/3/assignments', {'x': 'y'}),
                          (ORIGIN + '/api/v1/courses/3/students', None)]:
            with self.assertRaises(AppError): transport.request(url, token='test', form=form)
        self.assertIsNone(NoRedirect().redirect_request(Request(ORIGIN), None, 302, '', {}, 'https://evil.example'))

    def test_private_vault_rejects_loose_permissions_and_has_data_directory_isolation(self):
        self.connect(); os.chmod(self.connection.vault.path, 0o644)
        with self.assertRaises(AppError): self.connection.vault.read()
        self.assertIn('settingsError', self.connection.status())
        a = Vault('/tmp/a'); b = Vault('/tmp/b')
        self.assertNotEqual(a.path, b.path); self.assertNotIn('GoogleDrive', str(a.path))


class CanvasHTTPTests(unittest.TestCase):
    def test_http_parent_csrf_assistant_boundaries_and_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(Path(directory)/'data', 0, canvas_transport=FakeCanvas(), canvas_vault=Path(directory)/'private.json')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            token = server.store.authenticate(f'{secrets.randbelow(10000):04d}', setup=True)
            def call(path, method='GET', headers=None, body=None):
                try:
                    with urlopen(Request(base+path, data=json.dumps(body or {}).encode() if method=='POST' else None, headers=headers or {}, method=method)) as r:
                        return r.status, json.load(r)
                except HTTPError as e: return e.code, json.load(e)
            try:
                self.assertEqual(call('/api/parent/canvas')[0], 401)
                self.assertEqual(call('/api/parent/canvas', headers={'Authorization':'Bearer '+server.store.assistant_token})[0], 403)
                headers = {'Cookie':'river_parent='+token}
                self.assertEqual(call('/api/parent/canvas', headers=headers)[0], 200)
                self.assertEqual(call('/api/parent/canvas/test-connect','POST',headers,{'token':'x','ownAccount':True})[0], 403)
                headers.update({'Origin':base,'X-Student-Request':'1'})
                self.assertEqual(call('/api/parent/canvas/test-connect','POST',headers,{'token':'parent-secret','ownAccount':True})[0], 200)
                self.assertNotIn('parent-secret', json.dumps(call('/api/parent/canvas', headers=headers)[1]))
                self.assertNotIn('canvas', call('/api/state')[1])
                self.assertEqual(call('/api/parent/canvas','POST',headers)[0], 404)
            finally:
                server.shutdown(); thread.join(3); server.server_close(); server.store.db.close()


if __name__ == '__main__': unittest.main()
