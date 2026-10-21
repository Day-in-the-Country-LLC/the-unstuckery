"""Focused security/regression audit; only isolated synthetic app data is used."""
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import AppError, RiverStore, create_server
from rewards import day_progress, reward_mutate, reward_state


class RewardEvidenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='river-security-store-')
        self.now = datetime(2026, 9, 11, 16, 0, tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store = RiverStore(self.tmp.name, now=lambda: self.now)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def review_plan(self, day):
        return reward_mutate(self.store, '/api/parent/rewards/day', {
            'date': day, 'expectedSchoolIds': [], 'note': 'Synthetic explicitly empty school plan.'}, 'parent')

    def test_future_daily_confirmation_cannot_become_verified_on_rollover(self):
        tomorrow = '2026-09-12'
        self.review_plan(tomorrow)
        # Simulate a preexisting record from a prior server version. New writes
        # should also be rejected at the HTTP boundary.
        b = self.store.day(tomorrow)['braces']
        b['parentConfirmed'] = {'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic premature confirmation.'}
        self.now += timedelta(days=1)
        day = day_progress(self.store, tomorrow, True)
        self.assertFalse(day['complete'])
        self.assertEqual(day['items'][0]['status'], 'awaiting_review')
        self.assertIn('does not match this day', day['items'][0]['reason'])
        b['parentConfirmed'] = {'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic actual same-day review.'}
        self.assertTrue(day_progress(self.store, tomorrow, True)['complete'])

    def test_future_chore_confirmation_and_malformed_evidence_never_count(self):
        tomorrow = '2026-09-12'
        self.review_plan(tomorrow)
        d = self.store.day(tomorrow)
        d['afterSchool'] = True
        for chore in d['chores']:
            chore.update(reportedAt=self.store.stamp(), completedAt=self.store.stamp(), parentConfirmed={'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic early check.'})
        self.now += timedelta(days=1)
        d['braces']['parentConfirmed'] = {'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic correct day.'}
        result = day_progress(self.store, tomorrow, True)
        self.assertFalse(result['complete'])
        self.assertTrue(all(i['status'] != 'complete' for i in result['items'] if i['kind'] == 'chore'))
        d['braces']['parentConfirmed']['at'] = 'not a timestamp'
        self.assertEqual(day_progress(self.store, tomorrow, True)['items'][-1]['status'], 'awaiting_review')

    def test_transaction_rolls_back_bad_expectations_and_duplicate_awards(self):
        self.store.transaction(lambda: self.review_plan('2026-09-11'))
        before = json.dumps(self.store.state, sort_keys=True)
        with self.assertRaises(AppError):
            self.store.transaction(lambda: reward_mutate(self.store, '/api/parent/rewards/day', {
                'date': '2026-09-11', 'expectedSchoolIds': [], 'excusedItems': {'braces': ''}, 'note': 'Invalid exception.'}, 'parent'))
        self.assertEqual(json.dumps(self.store.state, sort_keys=True), before)
        with self.assertRaises(AppError):
            self.store.transaction(lambda: reward_mutate(self.store, '/api/parent/rewards/award', {'date': '2026-09-11', 'note': 'Missing evidence.'}, 'parent'))
        self.assertEqual(json.dumps(self.store.state, sort_keys=True), before)

    def test_later_parent_review_counts_and_completed_estimate_edits_expose_overbudget(self):
        self.review_plan('2026-09-10')
        d = self.store.day('2026-09-10')
        for chore in d['chores']:
            chore.update(reportedAt=self.store.stamp(), completedAt=self.store.stamp(), parentConfirmed={'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic later review of yesterday.'})
        d['braces']['parentConfirmed'] = {'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic later review of yesterday.'}
        self.assertTrue(day_progress(self.store, '2026-09-10', True)['complete'])
        tasks = []
        for number in range(2):
            t = self.store.create_task({'subject': 'Synthetic', 'title': f'Session {number}', 'instructions': 'One planned practice block.', 'planDate': '2026-09-11', 'scope': 'work_block', 'plannedMinutes': 30})
            tasks.append(self.store.task(t['id']))
        self.assertTrue(reward_state(self.store)['day']['planReviewed'])
        tasks[0]['plannedMinutes'] = 70
        self.assertFalse(reward_state(self.store)['day']['planReviewed'])
        self.assertIn('90 minutes', ' '.join(reward_state(self.store)['day']['planIssues']))

    def test_parent_redemption_confirms_the_exact_displayed_choice(self):
        for number in range(7, 12):
            day = f'2026-09-{number:02d}'
            self.review_plan(day)
            d = self.store.day(day)
            for chore in d['chores']:
                chore.update(reportedAt=self.store.stamp(), completedAt=self.store.stamp(), parentConfirmed={'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic review.'})
            d['braces']['parentConfirmed'] = {'at': self.store.stamp(), 'actor': 'parent', 'note': 'Synthetic review.'}
        def change(path, body, actor='parent'):
            return self.store.transaction(lambda: reward_mutate(self.store, path, body, actor))['award']
        award = change('/api/parent/rewards/award', {'date': '2026-09-11', 'note': 'Synthetic parent award.'})
        self.assertEqual(award['requestRevision'], 0)
        first = change('/api/rewards/request', {'awardId': award['id'], 'prizeId': 'doordash'}, 'river')
        self.assertEqual(first['requestRevision'], 1)
        second = change('/api/rewards/request', {'awardId': award['id'], 'prizeId': 'mom-chore', 'chore': 'dogs'}, 'river')
        self.assertEqual(second['requestRevision'], 2)
        for stale in ({}, {'requestRevision': first['requestRevision']}, {'requestRevision': True}):
            with self.assertRaises(AppError):
                change('/api/parent/rewards/redeem', dict(awardId=award['id'], note='Parent still sees the first choice.', **stale))
        saved = self.store.state['rewards']['awards'][0]
        self.assertIsNone(saved['redeemedAt'])
        self.assertIsNone(saved['swap'])
        final = change('/api/parent/rewards/redeem', {'awardId': award['id'], 'requestRevision': second['requestRevision'], 'note': 'Parent reviewed the changed chore choice.'})
        self.assertEqual(final['selectedPrize']['id'], 'mom-chore')
        self.assertEqual(final['swap']['chore'], 'dogs')
        self.assertEqual(final['history'][-1]['details']['requestRevision'], 2)


class HttpAccessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='river-security-http-')
        self.now = datetime(2026, 9, 11, 16, 0, tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.server = create_server(self.tmp.name, port=0, now=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.server.store.assistant_token
        self.cookie = ''
        self.pin = f'{secrets.randbelow(10000):04d}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.server.store.db.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, *, assistant=False, parent=False, origin=None, host=None, csrf=True):
        headers = {}
        if method != 'GET':
            headers['Origin'] = origin or self.base
            if csrf:
                headers['X-Student-Request'] = '1'
        if host:
            headers['Host'] = host
        if assistant:
            headers['Authorization'] = 'Bearer ' + self.token
        if parent:
            headers['Cookie'] = self.cookie
        if body is not None:
            headers['Content-Type'] = 'application/json'
        raw = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            data = json.loads(response.read())
            if response.headers.get('Set-Cookie'):
                cookie = SimpleCookie(response.headers['Set-Cookie'])
                self.cookie = '; '.join(k + '=' + v.value for k, v in cookie.items())
            return response.status, data

    def setup_parent(self):
        self.assertEqual(self.call('POST', '/api/auth/setup', {'pin': self.pin})[0], 200)

    def test_writing_expectations_require_parent_session_and_csrf(self):
        route='/api/parent/practice/writing'
        body={'level':'building','automatic':False,'expectedRevision':1}
        self.assertEqual(self.call('POST',route,body)[0],401)
        self.setup_parent()
        self.assertEqual(self.call('POST',route,body,parent=True,assistant=True)[0],403)
        self.assertEqual(self.call('POST',route,body,parent=True,csrf=False)[0],403)
        self.assertEqual(self.call('POST',route,body,parent=True)[0],200)
        state=self.call('GET','/api/parent/practice',parent=True)[1]
        self.assertEqual(state['writingCoach']['level'],'building')
        self.assertFalse(state['writingCoach']['automatic'])
    def test_ai_review_status_is_public_but_private_job_fields_are_not(self):
        self.server.store.ai_review_enabled=True
        self.server.store.state['aiReviewJobs']={'synthetic':{'status':'failed','message':'Try again later.','tries':1,
            'sessionId':'PRIVATE_SESSION','fingerprint':'PRIVATE_FINGERPRINT','calls':{'PRIVATE_CALL':{'output':'PRIVATE_EVIDENCE'}}}}
        status,body=self.call('GET','/api/ai-review')
        self.assertEqual(status,200);self.assertTrue(body['enabled'])
        self.assertNotIn('PRIVATE',json.dumps(body))
        route='/api/practice/mastery/synthetic/review-retry'
        self.assertEqual(self.call('POST',route,{'attemptId':'synthetic'},csrf=False)[0],403)
        self.assertEqual(self.call('POST',route,{'attemptId':'synthetic'},assistant=True)[0],403)
        self.assertEqual(self.call('GET','/review.env')[0],404)

    def test_backlog_is_private_and_only_parent_can_schedule(self):
        with urlopen(self.base+"/backlog.js") as response:
            self.assertEqual(response.status,200)
            self.assertIn(b"Activity backlog",response.read())
        self.setup_parent()
        body={'title':'Synthetic hidden activity','subject':'Test','estimatedMinutes':10}
        self.assertEqual(self.call('GET','/api/parent/backlog')[0],401)
        self.assertEqual(self.call('GET','/api/assistant/backlog')[0],401)
        status,result=self.call('POST','/api/assistant/backlog',body,assistant=True)
        self.assertEqual(status,200)
        ident=result['itemId'];route='/api/parent/backlog/'+ident+'/schedule'
        self.assertNotIn('Synthetic hidden activity',json.dumps(self.call('GET','/api/state')[1]))
        self.assertEqual(self.call('POST',route,{'expectedRevision':1},parent=True,csrf=False)[0],403)
        self.assertEqual(self.call('POST',route,{'expectedRevision':1},parent=True,assistant=True)[0],403)
        self.assertEqual(self.call('POST',route,{'expectedRevision':1},parent=True)[0],200)
        self.assertIn('Synthetic hidden activity',json.dumps(self.call('GET','/api/state')[1]))

    def test_reward_privileges_ignore_body_claims_and_reject_assistant_with_parent_cookie(self):
        self.setup_parent()
        body = {'weekStart': 6, 'qualifyingDays': [0], 'actor': 'parent', 'parent': True}
        before = self.call('GET', '/api/state')[1]['rewards']['settings']
        self.assertEqual(self.call('POST', '/api/parent/rewards/settings', body)[0], 401)
        self.assertEqual(self.call('POST', '/api/parent/rewards/settings', body, assistant=True, parent=True)[0], 403)
        self.assertEqual(self.call('GET', '/api/state')[1]['rewards']['settings'], before)
        self.assertEqual(self.call('POST', '/api/parent/rewards/settings', body, parent=True)[0], 200)
        self.assertEqual(self.call('POST', '/api/rewards/request', {'awardId': 'unknown', 'prizeId': 'doordash'}, assistant=True)[0], 403)
        self.assertEqual(self.call('POST', '/api/parent/rewards/redeem', {'awardId': 'unknown', 'note': 'Attempt'}, assistant=True, parent=True)[0], 403)
        self.assertEqual(self.call('POST', '/api/parent/braces/confirm', {'note': 'Attempt'}, assistant=True, parent=True)[0], 403)

    def test_holiday_freebie_requires_authenticated_parent(self):
        self.setup_parent()
        path = '/api/parent/rewards/holiday'
        body = {'date': '2026-09-07', 'enabled': True}
        self.assertEqual(self.call('POST', path, body)[0], 401)
        self.assertEqual(self.call('POST', path, body, assistant=True, parent=True)[0], 403)
        self.assertEqual(self.call('POST', path, body, parent=True, csrf=False)[0], 403)
        self.assertEqual(self.call('GET', '/api/state')[1]['rewards']['week']['completedDays'], 0)
        status, result = self.call('POST', path, body, parent=True)
        self.assertEqual(status, 200)
        self.assertEqual(result['day']['status'], 'holiday')
        self.assertEqual(self.call('GET', '/api/state')[1]['rewards']['week']['completedDays'], 1)

    def test_same_origin_and_host_checks_precede_privileged_writes(self):
        self.setup_parent()
        body = {'date': '2026-09-11', 'expectedSchoolIds': [], 'note': 'Synthetic school plan.'}
        path = '/api/parent/rewards/day'
        self.assertEqual(self.call('POST', path, body, parent=True, origin='https://unrelated.example.invalid')[0], 403)
        self.assertEqual(self.call('POST', path, body, parent=True, csrf=False)[0], 403)
        self.assertEqual(self.call('POST', path, body, parent=True, host='unrelated.example.invalid')[0], 403)
        self.assertFalse(self.call('GET', '/api/state')[1]['rewards']['day']['planReviewed'])
        self.assertEqual(self.call('POST', path, body, parent=True)[0], 200)
        state = self.call('GET', '/api/state')[1]
        self.assertTrue(state['rewards']['day']['planReviewed'])
        serialized = json.dumps(state)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn(self.cookie.split('=', 1)[-1], serialized)
        self.assertNotIn('digest', serialized)

    def test_session_expiry_and_failed_logins_cannot_change_parent_state(self):
        self.setup_parent()
        self.now += timedelta(minutes=31)
        body = {'weekStart': 6, 'qualifyingDays': [0]}
        self.assertEqual(self.call('POST', '/api/parent/rewards/settings', body, parent=True)[0], 401)
        wrong_pin = f'{(int(self.pin) + 1) % 10000:04d}'
        for _ in range(5):
            self.assertEqual(self.call('POST', '/api/auth/login', {'pin': wrong_pin})[0], 401)
        self.assertEqual(self.call('POST', '/api/auth/login', {'pin': self.pin})[0], 429)
        self.now += timedelta(minutes=11)
        self.assertEqual(self.call('POST', '/api/auth/login', {'pin': self.pin})[0], 200)
        self.assertEqual(self.call('POST', '/api/parent/rewards/settings', body, parent=True)[0], 200)


if __name__ == '__main__':
    unittest.main()
