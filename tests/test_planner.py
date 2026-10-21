"""Planner boundaries and durable retries, using only synthetic student data."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from server import create_server
from practice import PracticeError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {'origin': 'https://learning.example.com', 'googleClientId': 'synthetic.apps.googleusercontent.com',
          'parentEmails': ['parent@example.com']}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.start()
        self.token = self.server.store.planner_token
        self.quiz = {'requestId': 'synthetic-first', 'title': 'Synthetic source investigation', 'subject': 'Research',
                     'planDate': self.server.store.today(), 'reviewMode': 'mastery', 'sequential': True,
                     'sourceMaterial': 'Synthetic exercise; no real student data.',
                     'concepts': [{'id': 'sources', 'label': 'Find evidence', 'targetCorrect': 1, 'targetSessions': 1}],
                     'questions': [{'id': 'q1', 'conceptId': 'sources', 'prompt': 'Write a research question.',
                                    'type': 'rubric', 'rubric': 'A focused, neutral question.', 'required': True}]}
        self.publication = {'expectedRevision': 1, 'plannedMinutes': 20, 'instructions': 'Extra practice in this app. Pause after 20 minutes.'}

    def start(self, enabled=True, cloud=True):
        with patch.dict(os.environ, {'UNSTUCKERY_PLANNER_ENABLED': '1' if enabled else '0'}):
            self.server = create_server(self.tmp.name, port=0, cloud_config=CONFIG if cloud else None)
        self.cloud = cloud
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown(); self.thread.join(timeout=3)
        self.server.server_close(); self.server.store.db.close()

    def tearDown(self):
        self.stop(); self.tmp.cleanup()

    def call(self, path, body=None, token=True, method=None, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Host': 'learning.example.com' if self.cloud else f'127.0.0.1:{self.server.server_port}'}
        if token:
            headers['Authorization'] = 'Bearer ' + (self.token if token is True else token)
        if origin:
            headers['Origin'] = origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request(method or ('GET' if body is None else 'POST'), path,
                     None if body is None else json.dumps(body), headers)
        response = conn.getresponse(); status = response.status; raw = response.read(); conn.close()
        self.assertNotIn(self.token.encode(), raw)
        return status, json.loads(raw)

    def draft(self, body=None):
        status, result = self.call('/api/planner/practice/drafts', body or self.quiz)
        self.assertEqual(status, 200, result)
        return result['quizId']

    def test_opt_in_revocation_and_private_key(self):
        path = Path(self.tmp.name) / '.planner-token'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.token, self.server.store.assistant_token)
        self.assertEqual(self.call('/api/planner/state')[0], 200)
        self.stop(); self.start(enabled=False)
        self.assertIsNone(self.server.store.planner_token)
        self.assertEqual(self.call('/api/planner/state')[0], 401)

    def test_bearer_has_no_parent_student_or_assistant_privileges(self):
        for path in ('/api/parent/accounts', '/api/parent/practice', '/api/state', '/api/assistant/state',
                     '/api/ai-review', '/api/photos/synthetic', '/api/browser-memory'):
            self.assertEqual(self.call(path)[0], 403, path)
        for path in ('/api/auth/google', '/api/parent/rewards/award', '/api/tasks/synthetic/complete',
                     '/api/tasks/synthetic/report', '/api/parent/braces/confirm', '/api/school-clock/start',
                     '/api/practice/mastery/synthetic/answer', '/api/assistant/practice/attempts/synthetic/review'):
            self.assertEqual(self.call(path, {'actor': 'parent', 'parent': True})[0], 403, path)
        self.assertEqual(self.call('/api/planner/state', token=False)[0], 401)
        self.assertEqual(self.call('/api/planner/state', token=self.server.store.assistant_token)[0], 401)
        self.assertEqual(self.call('/api/planner/state', origin='https://other.example.com')[0], 403)
        self.assertEqual(self.call('/api/planner/practice/drafts', self.quiz, method='PUT')[0], 403)

    def test_durable_retries_publish_once_and_do_not_approve_work(self):
        ident = self.draft(); self.assertEqual(self.draft(), ident)
        changed = dict(self.quiz, title='Different content')
        self.assertEqual(self.call('/api/planner/practice/drafts', changed)[0], 409)
        route = f'/api/planner/practice/quizzes/{ident}/publish'
        status, result = self.call(route, self.publication)
        self.assertEqual(status, 200, result)
        task_id = result['taskId']
        self.stop(); self.start()
        self.assertEqual(self.draft(), ident)
        self.assertEqual(self.call(route, self.publication)[1]['taskId'], task_id)
        store = self.server.store
        self.assertEqual(len(store.state['tasks']), 1)
        task = store.task(task_id)
        self.assertEqual(task['scope'], 'practice')
        self.assertEqual(task['history'][0]['actor'], 'planner')
        self.assertTrue(task['requirements']['parent'])
        for key in ('reportedAt', 'completedAt', 'parentConfirmed', 'submission', 'understanding'):
            self.assertIsNone(task[key])
        day = store.state['rewards']['days'][self.quiz['planDate']]
        self.assertEqual(day['expectedSchoolIds'], [task_id])
        self.assertFalse(day.get('reviewedAt'))
        self.assertEqual(day['history'][-1]['actor'], 'planner')
        self.assertEqual(self.call(route, dict(self.publication, plannedMinutes=30))[0], 409)

    def test_revision_ownership_and_started_work_locks(self):
        ident = self.draft()
        edit = dict(self.quiz, expectedRevision=1, title='Revised synthetic exercise')
        path = f'/api/planner/practice/drafts/{ident}'
        self.assertEqual(self.call(path, edit)[0], 200)
        self.assertEqual(self.call(path, edit)[0], 409)
        pub = f'/api/planner/practice/quizzes/{ident}/publish'
        self.assertEqual(self.call(pub, self.publication)[0], 409)
        self.assertEqual(self.call(pub, dict(self.publication, expectedRevision=2))[0], 200)
        self.assertEqual(self.call(path, dict(edit, expectedRevision=2))[0], 409)
        store = self.server.store
        original = store.transaction(lambda: store.mutate('/api/assistant/practice/drafts', self.quiz, 'assistant'))['quizIds'][0]
        self.assertEqual(self.call(f'/api/planner/practice/quizzes/{original}/publish', self.publication)[0], 403)
        self.assertEqual(self.call(f'/api/planner/practice/drafts/{original}', edit)[0], 403)

    def test_limits_and_validation_do_not_partially_publish(self):
        for bad in (dict(self.quiz, requestId=''), dict(self.quiz, reviewMode='practice', sequential=False),
                    dict(self.quiz, questions=[dict(self.quiz['questions'][0], required=False)])):
            self.assertEqual(self.call('/api/planner/practice/drafts', bad)[0], 400)
        ident = self.draft()
        route = f'/api/planner/practice/quizzes/{ident}/publish'
        for bad in (dict(self.publication, plannedMinutes=True), dict(self.publication, plannedMinutes=91),
                    dict(self.publication, instructions=''), dict(self.publication, existingTaskId='synthetic')):
            self.assertEqual(self.call(route, bad)[0], 400)
        self.server.store.transaction(lambda: self.server.store.create_task({
            'subject': 'Synthetic', 'title': 'Existing obligation', 'instructions': 'Synthetic work.',
            'planDate': self.quiz['planDate'], 'plannedMinutes': 75}))
        self.assertEqual(self.call(route, self.publication)[0], 409)
        self.assertEqual(len(self.server.store.state['tasks']), 1)
        self.assertFalse(self.call('/api/planner/practice')[1]['quizzes'][0]['published'])
        old = self.draft(dict(self.quiz, requestId='old', planDate='2000-01-01'))
        self.assertEqual(self.call(f'/api/planner/practice/quizzes/{old}/publish', self.publication)[0], 400)

    def test_parent_revision_and_prior_work_are_preserved(self):
        ident = self.draft()
        store = self.server.store
        quiz = next(q for q in store.state['practice']['quizzes'] if q['id'] == ident)
        before = json.dumps(quiz, sort_keys=True)
        for actor in ('parent', 'assistant', 'river'):
            with self.assertRaises(PracticeError):
                store.transaction(lambda: store.mutate(f'/api/planner/practice/quizzes/{ident}/publish', self.publication, actor))
        self.assertEqual(json.dumps(quiz, sort_keys=True), before)
        # Simulate a draft with retained earlier work: publication must not
        # reopen or replace it, even when a caller knows its current revision.
        store.state['practice']['attempts'].append({'quizId': ident, 'id': 'synthetic-prior'})
        self.assertEqual(self.call(f'/api/planner/practice/quizzes/{ident}/publish', self.publication)[0], 409)
        self.assertEqual(len(store.state['tasks']), 0)

    def test_cli_uses_separate_credential_and_rejects_other_actions(self):
        self.stop(); self.start(cloud=False)
        base = f'http://127.0.0.1:{self.server.server_port}'
        source = Path(self.tmp.name) / 'synthetic.json'; source.write_text(json.dumps(self.quiz))
        command = [sys.executable, str(ROOT / 'assistant.py'), '--planner', '--url', base, '--data-dir', self.tmp.name]
        result = subprocess.run(command + ['practice-draft', '--json-file', str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        ident = json.loads(result.stdout)['quizId']
        source.write_text(json.dumps(self.publication))
        result = subprocess.run(command + ['practice-publish', ident, '--json-file', str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['published'])
        self.assertNotIn(self.token, result.stdout + result.stderr)
        result = subprocess.run(command + ['backlog-state'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
