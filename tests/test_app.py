"""Behavior tests for the local app, using isolated, synthetic school records.

Run from the app directory: python3 -m unittest discover -s tests -v
No school portal is contacted and no actual assignment is submitted.
"""

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
from server import create_server


class AppBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="river-app-test-")
        self.data_dir = Path(self.tmp.name)
        self.now = datetime(2026, 9, 8, 15, 30, tzinfo=ZoneInfo("America/Indiana/Indianapolis"))
        self.pin = f"{secrets.randbelow(10000):04d}"
        self.cookie = ""
        self.start_server()

    def start_server(self):
        self.server = create_server(self.data_dir, port=0, now=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = (self.data_dir / ".assistant-token").read_text().strip()

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.server.store.db.close()

    def tearDown(self):
        self.stop_server()
        self.tmp.cleanup()

    def request(self, method, path, body=None, *, parent=False, assistant=False,
                csrf=True, origin=None):
        headers = {}
        if method != "GET" and csrf:
            headers["X-Student-Request"] = "1"
        if method != "GET" and not assistant:
            headers["Origin"] = origin or self.base
        if origin:
            headers["Origin"] = origin
        if parent and self.cookie:
            headers["Cookie"] = self.cookie
        if assistant:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            parsed = json.loads(raw) if raw else {}
            if response.headers.get("Set-Cookie"):
                cookies = SimpleCookie()
                cookies.load(response.headers["Set-Cookie"])
                self.cookie = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
            return response.status, parsed, response.headers

    def ok(self, method, path, body=None, **kwargs):
        status, data, _ = self.request(method, path, body, **kwargs)
        self.assertIn(status, (200, 201), (method, path, status, data))
        return data

    def denied(self, method, path, body=None, **kwargs):
        status, data, _ = self.request(method, path, body, **kwargs)
        self.assertIn(status, (400, 401, 403, 409), (method, path, status, data))
        self.assertTrue(data.get("error"), data)
        return status

    def setup_parent(self):
        return self.ok("POST", "/api/auth/setup", {"pin": self.pin})

    def state(self, date=None, assistant=False):
        path = "/api/assistant/state" if assistant else "/api/state"
        if date:
            path += "?date=" + date
        return self.ok("GET", path, assistant=assistant)

    def task(self, task_id, date=None):
        return next(task for task in self.state(date)["tasks"] if task["id"] == task_id)

    def create_task(self, **changes):
        fields = {
            "subject": "Synthetic math fixture",
            "title": "Practice a fresh fraction example",
            "instructions": "Explain one example in your own words.",
            "dueDate": "",
            "sourceUrl": "",
            "planDate": self.now.date().isoformat(),
            "requirements": {"understanding": False, "submission": False, "parent": False},
            "criteria": "Finish the brief practice.",
            "sourceNote": "Synthetic test only; no real school submission.",
        }
        fields.update(changes)
        before = {task["id"] for task in self.state(fields["planDate"])["tasks"]}
        self.ok("POST", "/api/parent/tasks", fields, parent=True)
        created = [task for task in self.state(fields["planDate"])["tasks"] if task["id"] not in before]
        self.assertEqual(len(created), 1)
        return created[0]

    def understanding(self, task_id, **changes):
        body = {
            "goal": "Add fractions with equal denominators",
            "evidence": "Synthetic fixture: learner explained why the denominator stays the same.",
            "help": "One reminder to look at the denominator; a fresh example followed.",
            "method": "fresh_example",
            "independent": True,
        }
        body.update(changes)
        return self.ok("POST", f"/api/assistant/tasks/{task_id}/understanding", body, assistant=True)

    def submission(self, task_id, **changes):
        body = {
            "evidence": "Synthetic fixture: required components show a portal receipt.",
            "sourceUrl": "https://school.example.invalid/assignments/synthetic-fixture",
            "observedAt": self.now.isoformat(),
            "observed": True,
            "completeWork": True,
        }
        body.update(changes)
        return self.ok("POST", f"/api/assistant/tasks/{task_id}/submission", body, assistant=True)

    def test_parent_authorization_setup_csrf_and_logout(self):
        self.assertFalse(self.ok("GET", "/api/auth")["configured"])
        self.denied("POST", "/api/auth/setup", {"pin": "abc"})
        status, _, headers = self.request("POST", "/api/auth/setup", {"pin": self.pin})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers.get("Set-Cookie", ""))
        self.assertTrue(self.ok("GET", "/api/auth", parent=True)["parent"])
        self.denied("POST", "/api/auth/setup", {"pin": self.pin}, parent=True)
        self.denied("POST", "/api/parent/day", {"afterSchool": False})
        self.denied("POST", "/api/parent/day", {"afterSchool": False}, assistant=True, origin=self.base)
        self.denied("POST", "/api/auth/login", {"pin": self.pin}, assistant=True, origin=self.base)
        self.denied("POST", "/api/parent/day", {"afterSchool": False}, parent=True, csrf=False)
        self.denied("POST", "/api/parent/day", {"afterSchool": False}, parent=True,
                    origin="https://unrelated.example.invalid")
        self.assertTrue(self.state()["afterSchool"])
        self.ok("POST", "/api/auth/logout", {}, parent=True)
        self.denied("POST", "/api/parent/day", {"afterSchool": False}, parent=True)
        wrong_pin = f"{(int(self.pin) + 1) % 10000:04d}"
        self.denied("POST", "/api/auth/login", {"pin": wrong_pin})
        self.ok("POST", "/api/auth/login", {"pin": self.pin})
        self.assertTrue(self.ok("GET", "/api/auth", parent=True)["parent"])

    def test_added_routine_requires_parent_session_and_same_origin(self):
        self.setup_parent()
        path='/api/parent/routine-chores'
        body={'title':'Synthetic tidy desk'}
        self.denied('POST',path,body)
        self.denied('POST',path,body,assistant=True,parent=True)
        self.denied('POST',path,body,parent=True,csrf=False)
        self.assertEqual(len([t for t in self.state()['tasks'] if t['kind']=='chore']),3)
        added=self.ok('POST',path,body,parent=True)['chore']
        self.assertEqual(self.task('2026-09-08-'+added['id'])['title'],body['title'])

    def test_weekday_schedule_changes_require_parent_session(self):
        self.setup_parent()
        path='/api/parent/routine-chores/dogs/schedule'
        body={'days':[6], 'effectiveDate':'2026-09-08'}
        self.denied('POST',path,body)
        self.denied('POST',path,body,parent=True,assistant=True)
        self.denied('POST',path,body,parent=True,csrf=False)
        self.ok('POST',path,body,parent=True)
        self.assertNotIn('2026-09-08-dogs',[t['id'] for t in self.state()['tasks']])
        self.assertIn('2026-09-13-dogs',[t['id'] for t in self.state('2026-09-13')['tasks']])

    def test_other_todos_require_parent_session_and_appear_in_history(self):
        self.setup_parent()
        path='/api/parent/todos'; body={'title':'Synthetic pack bag'}
        self.denied('POST',path,body)
        self.denied('POST',path,body,parent=True,assistant=True)
        self.denied('POST',path,body,parent=True,csrf=False)
        item=self.ok('POST',path,body,parent=True)['todo']
        self.ok('POST','/api/tasks/'+item['id']+'/report',{})
        self.denied('POST','/api/parent/tasks/'+item['id']+'/confirm',{})
        self.ok('POST','/api/parent/tasks/'+item['id']+'/confirm',{},parent=True)
        events=self.ok('GET','/api/parent/history',parent=True)['events']
        self.assertIn(item['id'],[e['taskId'] for e in events])

    def test_only_parent_approves_physical_chores_and_waiting_does_not_block_school(self):
        self.setup_parent()
        chores = [task for task in self.state()["tasks"] if task["kind"] == "chore"]
        self.assertEqual(len(chores), 3)
        for chore in chores:
            note = "No clean dishes to put away" if "dish" in chore["title"].lower() else "Ready for review"
            self.ok("POST", f"/api/tasks/{chore['id']}/report", {"note": note})
            reported = self.task(chore["id"])
            self.assertIsNotNone(reported["reportedAt"])
            self.assertIsNone(reported["parentConfirmed"])
            self.assertIsNone(reported["completedAt"])
            self.denied("POST", f"/api/tasks/{chore['id']}/complete", {})
            self.denied("POST", f"/api/parent/tasks/{chore['id']}/confirm", {})
            self.denied("POST", f"/api/parent/tasks/{chore['id']}/confirm", {}, assistant=True,
                        origin=self.base)
        school = self.create_task()
        self.ok("POST", f"/api/tasks/{school['id']}/report", {"note": "Practice finished"})
        self.ok("POST", f"/api/tasks/{school['id']}/complete", {})
        self.assertIsNotNone(self.task(school["id"])["completedAt"])
        for chore in chores:
            self.ok("POST", f"/api/parent/tasks/{chore['id']}/confirm", {}, parent=True)
            approved = self.task(chore["id"])
            self.assertEqual(approved["parentConfirmed"]["note"], "")
            self.assertEqual(approved["parentConfirmed"]["actor"], "parent")
            self.assertIsNotNone(approved["parentConfirmed"]["at"])
            self.assertIsNotNone(approved["completedAt"])

    def test_parent_final_review_preserves_independent_evidence_honestly(self):
        self.setup_parent()
        task = self.create_task(requirements={"understanding": True, "submission": True, "parent": True})
        task_id = task["id"]
        self.denied("POST", f"/api/tasks/{task_id}/complete", {})
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Ready"})
        self.understanding(task_id)
        understood = self.task(task_id)
        self.assertIsNotNone(understood["understanding"])
        self.assertIsNone(understood["submission"])
        self.assertFalse(understood["canComplete"])
        self.assertTrue(understood["needsReview"])
        self.ok("POST", f"/api/parent/tasks/{task_id}/confirm", {}, parent=True)
        completed = self.task(task_id)
        self.assertIsNotNone(completed["completedAt"])
        self.assertEqual(completed["completedAt"],completed["parentConfirmed"]["at"])
        self.assertEqual(completed["needsReview"],[])
        self.assertIsNone(completed["submission"])
        self.assertEqual(completed["understanding"],understood["understanding"])
        self.assertEqual(completed["completionBasis"]["type"],"parent_outside_app_review")
        self.ok("POST", f"/api/tasks/{task_id}/complete", {})

    def test_review_requires_independent_understanding_and_observed_portal_evidence(self):
        self.setup_parent()
        task_id = self.create_task(requirements={"understanding": True, "submission": True, "parent": False})["id"]
        self.denied("POST", f"/api/assistant/tasks/{task_id}/understanding", {
            "goal": "Fractions", "evidence": "Copied an answer", "method": "own_words", "independent": False,
        }, assistant=True)
        for body in (
            {"evidence": "Student said it was sent", "observed": False, "completeWork": True},
            {"evidence": "A local file exists", "observed": True, "completeWork": True,
             "sourceUrl": "file:///tmp/example.txt", "observedAt": self.now.isoformat()},
            {"evidence": "Only one of the required components", "observed": True, "completeWork": False,
             "sourceUrl": "https://school.example.invalid/test", "observedAt": self.now.isoformat()},
        ):
            self.denied("POST", f"/api/assistant/tasks/{task_id}/submission", body, assistant=True)
        self.assertIsNone(self.task(task_id)["understanding"])
        self.assertIsNone(self.task(task_id)["submission"])
        self.denied("GET", "/api/assistant/state")
        self.denied("POST", f"/api/assistant/tasks/{task_id}/understanding", {
            "goal": "Fractions", "evidence": "Uncredentialed request", "method": "own_words", "independent": True,
        })
        self.assertEqual(self.state(assistant=True)["date"], self.state()["date"])

    def test_saturday_catchup_requires_parent_session_and_csrf(self):
        self.setup_parent()
        route='/api/parent/rewards/catchup'
        body={'date':'2026-09-08','enabled':True}
        self.denied('POST',route,body)
        self.denied('POST',route,body,parent=True,assistant=True)
        self.denied('POST',route,body,parent=True,csrf=False)
        self.ok('POST',route,body,parent=True)
        self.assertTrue(self.state()['rewards']['week']['catchup']['enabled'])
        self.ok('POST',route,{'enabled':False},parent=True)
        self.assertFalse(self.state()['rewards']['week']['catchup']['enabled'])

    def test_school_time_correction_requires_parent_session_and_preserves_history(self):
        self.setup_parent()
        self.ok('POST','/api/school-clock/start',{'kind':'work'})
        self.now += timedelta(minutes=20)
        path = '/api/parent/school-clock/correct'
        body = {'removeMinutes':10}
        self.denied('POST',path,body)
        self.denied('POST',path,body,assistant=True,parent=True)
        self.denied('POST',path,body,parent=True,csrf=False)
        result = self.ok('POST',path,body,parent=True)
        self.assertEqual(result['elapsedSeconds'],600)
        self.assertFalse(result['running'])
        history=self.ok('GET','/api/parent/history?date=2026-09-08',parent=True)
        self.assertIn('school_time_corrected',json.dumps(history))
        self.ok('POST','/api/school-clock/start',{})
        self.now += timedelta(minutes=20)
        self.assertEqual(self.state()['schoolClock']['elapsedSeconds'],1800)

    def test_corrections_and_revocations_preserve_history_and_other_evidence(self):
        self.setup_parent()
        task_id = self.create_task(requirements={"understanding": True, "submission": True, "parent": False})["id"]
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Ready"})
        self.understanding(task_id)
        self.submission(task_id)
        self.ok("POST", f"/api/tasks/{task_id}/complete", {})
        original = self.task(task_id)
        self.now += timedelta(minutes=1)
        self.ok("POST", f"/api/tasks/{task_id}/correct", {"note": "I noticed an unfinished part."})
        corrected = self.task(task_id)
        self.assertIsNone(corrected["reportedAt"])
        self.assertIsNone(corrected["completedAt"])
        self.assertFalse(corrected["canComplete"])
        self.assertEqual(corrected["understanding"], original["understanding"])
        self.assertEqual(corrected["submission"], original["submission"])
        self.assertGreater(len(corrected["history"]), len(original["history"]))
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Finished the remaining part"})
        self.denied("POST", f"/api/tasks/{task_id}/complete", {})
        self.denied("POST", f"/api/assistant/tasks/{task_id}/resolve", {"note": "Uncredentialed resolution"})
        self.ok("POST", f"/api/assistant/tasks/{task_id}/resolve", {
            "note": "Reviewed the correction and confirmed the remaining work against the evidence.",
        }, assistant=True)
        self.ok("POST", f"/api/tasks/{task_id}/complete", {})
        self.ok("POST", f"/api/assistant/tasks/{task_id}/revoke", {
            "field": "submission", "note": "Receipt did not include all required work.",
        }, assistant=True)
        revoked = self.task(task_id)
        self.assertIsNone(revoked["submission"])
        self.assertIsNone(revoked["completedAt"])
        self.assertIsNotNone(revoked["understanding"])
        self.assertIsNotNone(revoked["reportedAt"])
        self.assertTrue(revoked["needsReview"])
        self.assertGreater(len(revoked["history"]), len(corrected["history"]))
        self.denied("POST", f"/api/assistant/tasks/{task_id}/revoke", {
            "field": "parentConfirmed", "note": "Cannot impersonate a parent",
        }, assistant=True)
        history = self.ok("GET", "/api/parent/history?date=2026-09-08", parent=True)
        self.assertIn("Receipt did not include all required work.", json.dumps(history))

    def test_paper_assignment_can_complete_without_portal_submission(self):
        self.setup_parent()
        task_id = self.create_task(title="Paper practice", requirements={
            "understanding": True, "submission": False, "parent": False,
        })["id"]
        self.ok("POST", f"/api/tasks/{task_id}/help", {"note": "Need help choosing a first step."})
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Paper work ready"})
        self.understanding(task_id)
        self.assertTrue(self.task(task_id)["canComplete"])
        self.ok("POST", f"/api/tasks/{task_id}/complete", {})
        completed = self.task(task_id)
        self.assertIsNone(completed["submission"])
        self.assertIn(completed["understanding"]["help"], json.dumps(self.state()["summary"]))
        self.assertIn("Need help choosing a first step.", json.dumps(completed["history"]))

    def test_progress_persists_across_server_restart(self):
        self.setup_parent()
        task_id = self.create_task(requirements={"understanding": True, "submission": False, "parent": False})["id"]
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Saved report"})
        self.understanding(task_id)
        before = self.task(task_id)
        token = self.token
        self.stop_server()
        self.start_server()
        self.assertTrue(self.ok("GET", "/api/auth")["configured"])
        self.assertEqual(self.token, token)
        after = self.task(task_id)
        for field in ("reportedAt", "reportedNote", "understanding", "history", "requirements"):
            self.assertEqual(after[field], before[field])
        self.ok("POST", "/api/auth/login", {"pin": self.pin})
        self.assertTrue(self.ok("GET", "/api/auth", parent=True)["parent"])

    def test_daily_rollover_preserves_unfinished_work_and_avoids_weekend_chores(self):
        self.setup_parent()
        task_id = self.create_task(title="Still unfinished")["id"]
        future_id = self.create_task(title="Friday plan", planDate="2026-09-11")["id"]
        self.assertNotIn(future_id, {task["id"] for task in self.state()["tasks"]})
        old_chores = [task for task in self.state()["tasks"] if task["kind"] == "chore"]
        self.ok("POST", f"/api/tasks/{old_chores[0]['id']}/report", {"note": "Ready yesterday"})
        self.ok("POST", f"/api/parent/tasks/{old_chores[0]['id']}/confirm", {"note": "Checked yesterday"}, parent=True)
        self.now += timedelta(days=1)
        tomorrow = self.state()
        self.assertEqual(tomorrow["date"], "2026-09-09")
        self.assertIn(task_id, {task["id"] for task in tomorrow["tasks"]})
        new_chores = [task for task in tomorrow["tasks"] if task["kind"] == "chore"]
        self.assertEqual(len(new_chores), 3)
        self.assertTrue(all(task["parentConfirmed"] is None and task["completedAt"] is None for task in new_chores))
        self.assertIsNotNone(self.task(old_chores[0]["id"], "2026-09-08")["completedAt"])
        self.denied("POST", f"/api/tasks/{old_chores[0]['id']}/report", {"note": "Cannot rewrite yesterday"})
        self.now += timedelta(days=3)
        weekend = self.state()
        self.assertEqual(weekend["date"], "2026-09-12")
        self.assertFalse(weekend["afterSchool"])
        self.assertEqual([task for task in weekend["tasks"] if task["kind"] == "chore"], [])
        self.assertIn(task_id, {task["id"] for task in weekend["tasks"]})
        self.assertEqual(weekend["braces"]["date"], "2026-09-12")
        self.ok("POST", "/api/auth/login", {"pin": self.pin})
        self.ok("POST", "/api/parent/day", {"date": "2026-09-12", "afterSchool": True}, parent=True)
        self.assertEqual(len([task for task in self.state()["tasks"] if task["kind"] == "chore"]), 3)

    def test_braces_timer_persists_and_elapsed_does_not_confirm_wearing(self):
        self.setup_parent()
        self.ok("POST", "/api/braces/start", {})
        self.now += timedelta(minutes=10)
        self.assertAlmostEqual(self.state()["braces"]["elapsedSeconds"], 600, delta=1)
        self.stop_server()
        self.start_server()
        self.now += timedelta(minutes=20)
        running = self.state()["braces"]
        self.assertTrue(running["running"])
        self.assertAlmostEqual(running["elapsedSeconds"], 1800, delta=1)
        self.ok("POST", "/api/braces/pause", {})
        self.now += timedelta(minutes=15)
        self.assertAlmostEqual(self.state()["braces"]["elapsedSeconds"], 1800, delta=1)
        self.ok("POST", "/api/braces/start", {})
        self.now += timedelta(minutes=30)
        elapsed = self.state()["braces"]
        self.assertTrue(elapsed["timerElapsed"])
        self.assertIsNone(elapsed["parentConfirmed"])
        self.denied("POST", "/api/parent/braces/confirm", {"note": "Timer ran"})
        self.denied("POST", "/api/parent/braces/confirm", {"note": "Timer ran"}, assistant=True, origin=self.base)
        self.ok("POST", "/api/auth/login", {"pin": self.pin})
        self.ok("POST", "/api/parent/braces/confirm", {}, parent=True)
        self.assertIsNotNone(self.state()["braces"]["parentConfirmed"])
        self.assertEqual(self.state()["braces"]["parentConfirmed"]["note"], "")
        self.denied("POST", "/api/braces/reset", {})
        self.ok("POST", "/api/braces/reset", {"note": "Timer was started accidentally"})
        self.assertEqual(self.state()["braces"]["elapsedSeconds"], 0)
        self.ok("POST", "/api/parent/braces/revoke", {"note": "Correcting earlier observation"}, parent=True)
        self.assertIsNone(self.state()["braces"]["parentConfirmed"])
        self.now += timedelta(days=1)
        self.assertEqual(self.state()["braces"]["elapsedSeconds"], 0)
        self.assertFalse(self.state()["braces"]["running"])


if __name__ == "__main__":
    unittest.main()
