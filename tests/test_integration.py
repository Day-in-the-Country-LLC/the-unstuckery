"""HTTP/CLI integration against isolated synthetic data, never the live app folder.

Requires permission to bind a loopback socket. The assistant subprocess uses the
documented CLI exactly as an existing Codex assistant does; no portal is contacted.
"""
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from server import create_server


class HttpCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="river-integration-")
        self.data_dir = Path(self.tmp.name)
        self.now = datetime(2026, 9, 8, 15, 0, tzinfo=ZoneInfo("America/Indiana/Indianapolis"))
        self.cookie = ""
        self.pin = f"{secrets.randbelow(10000):04d}"
        self.server = None
        self.start_server()

    def start_server(self):
        self.server = create_server(self.data_dir, port=0, now=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = (self.data_dir / ".assistant-token").read_text().strip()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(timeout=3)
            self.server.server_close()
            self.server.store.db.close()
            self.server = None

    def tearDown(self):
        self.stop_server()
        self.tmp.cleanup()

    def call(self, method, path, body=None, *, parent=False, assistant=False):
        headers = {}
        if method != "GET":
            headers.update({"Origin": self.base, "X-Student-Request": "1"})
        if parent:
            headers["Cookie"] = self.cookie
        if assistant:
            headers["Authorization"] = "Bearer " + self.token
        raw = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(body).encode()
        req = Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read())
            if response.headers.get("Set-Cookie"):
                cookies = SimpleCookie(response.headers["Set-Cookie"])
                self.cookie = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
            return response.status, payload

    def ok(self, method, path, body=None, **kwargs):
        status, payload = self.call(method, path, body, **kwargs)
        self.assertIn(status, (200, 201), (method, path, status, payload))
        return payload

    def setup_parent(self):
        self.ok("POST", "/api/auth/setup", {"pin": self.pin})

    def json_file(self, name, body):
        path = self.data_dir / name
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    def cli(self, command, *arguments, expect_success=True):
        result = subprocess.run(
            [sys.executable, str(APP_ROOT / "assistant.py"), "--url", self.base,
             "--data-dir", str(self.data_dir), command, *map(str, arguments)],
            text=True, capture_output=True, timeout=15, cwd=APP_ROOT,
        )
        self.assertNotIn(self.token, result.stdout + result.stderr)
        if expect_success:
            self.assertEqual(result.returncode, 0, (command, result.stderr))
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, command)
        return result

    def quiz_body(self, rubric=False):
        return {
            "title": "Synthetic integration quiz", "subject": "Synthetic math",
            "sourceMaterial": "PRIVATE_SOURCE_SENTINEL: artificial test source, not actual school performance.",
            "sourceNote": "Temporary integration test; no portal access or actual assignment submission.",
            "sourceUrl": "", "planDate": "2026-09-08",
            "concepts": [{"id": "concept1", "label": "Combine two amounts", "goal": "Explain a small addition example",
                          "targetCorrect": 1, "targetSessions": 1}],
            "questions": [{"id": "question1", "conceptId": "concept1", "prompt": "Explain the synthetic example: one plus one.",
                           "type": "rubric" if rubric else "exact", "options": [],
                           "acceptedAnswers": [] if rubric else ["PRIVATE_KEY_SENTINEL"],
                           "rubric": "PRIVATE_RUBRIC_SENTINEL: connect both amounts to their combined amount." if rubric else "",
                           "explanation": "PRIVATE_EXPLANATION_SENTINEL: combine the two amounts.",
                           "hint": "Consider each amount separately.", "required": True, "origin": "missed"}],
        }

    def task(self, ident):
        return next(task for task in self.ok("GET", "/api/state")["tasks"] if task["id"] == ident)

    def test_practice_http_authorization_and_public_views_hide_review_material(self):
        self.setup_parent()
        self.assertEqual(self.call("GET", "/api/parent/practice")[0], 401)
        self.assertEqual(self.call("GET", "/api/assistant/practice")[0], 401)
        self.assertEqual(self.call("GET", "/api/parent/practice", parent=True, assistant=True)[0], 403)
        body = self.quiz_body()
        self.assertEqual(self.call("POST", "/api/assistant/practice/drafts", body, parent=True)[0], 401)
        drafted = self.ok("POST", "/api/assistant/practice/drafts", body, assistant=True)
        quiz_id = drafted["quizIds"][0]
        self.assertEqual(self.ok("GET", "/api/practice")["quizzes"], [])
        publish_path = f"/api/parent/practice/quizzes/{quiz_id}/publish"
        self.assertEqual(self.call("POST", publish_path, {"reviewed": True, "actor": "parent"})[0], 401)
        self.assertEqual(self.call("POST", publish_path, {"reviewed": True}, parent=True, assistant=True)[0], 403)
        self.assertFalse(self.ok("GET", "/api/parent/practice", parent=True)["quizzes"][0]["published"])
        published = self.ok("POST", publish_path, {"reviewed": True}, parent=True)
        task_id = published["taskId"]
        for path in ("/api/practice", "/api/state"):
            public = json.dumps(self.ok("GET", path))
            for private in ("PRIVATE_KEY_SENTINEL", "PRIVATE_SOURCE_SENTINEL", "PRIVATE_EXPLANATION_SENTINEL", self.token):
                self.assertNotIn(private, public)
        private_view = self.ok("GET", "/api/assistant/practice", assistant=True)
        self.assertIn("PRIVATE_KEY_SENTINEL", json.dumps(private_view))
        self.assertEqual(len([t for t in self.ok("GET", "/api/state")["tasks"] if t["kind"] == "school"]), 1)
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Synthetic ready report before any quiz checks."})
        self.assertEqual(self.call("POST", f"/api/tasks/{task_id}/complete", {})[0], 409)
        self.assertFalse(self.task(task_id)["canComplete"])
        self.assertEqual(self.call("POST", "/api/practice/sessions/start", {"quizId": quiz_id}, assistant=True)[0], 403)

    def test_practice_validation_errors_are_400_and_leave_saved_state_unchanged(self):
        self.setup_parent()
        before = self.ok("GET", "/api/parent/practice", parent=True)
        bad_quiz = self.quiz_body()
        bad_quiz["sourceMaterial"] = ""
        status, payload = self.call("POST", "/api/assistant/practice/drafts", bad_quiz, assistant=True)
        self.assertEqual(status, 400, payload)
        self.assertIn("error", payload)
        status, payload = self.call("POST", "/api/parent/practice/settings", {"sessionQuestions": 0}, parent=True)
        self.assertEqual(status, 400, payload)
        after = self.ok("GET", "/api/parent/practice", parent=True)
        self.assertEqual(after["quizzes"], before["quizzes"])
        self.assertEqual(after["settings"], before["settings"])
        invalid_file = self.json_file("invalid-draft.json", bad_quiz)
        failure = self.cli("practice-draft", "--json-file", invalid_file, expect_success=False)
        self.assertIn("error", json.loads(failure.stderr))
        self.assertNotIn("Traceback", failure.stderr)

    def test_cli_draft_review_and_revocation_survive_restart_and_gate_whole_task(self):
        self.setup_parent()
        quiz_file = self.json_file("prepared-quiz.json", self.quiz_body(rubric=True))
        drafted = self.cli("practice-draft", "--json-file", quiz_file)
        quiz_id = drafted["quizIds"][0]
        self.assertFalse(self.cli("practice-state")["quizzes"][0]["published"])
        published = self.ok("POST", f"/api/parent/practice/quizzes/{quiz_id}/publish", {"reviewed": True}, parent=True)
        task_id = published["taskId"]
        session = self.ok("POST", "/api/practice/sessions/start", {"quizId": quiz_id})["session"]
        self.assertTrue(session["canAnswer"])
        result = self.ok("POST", f"/api/practice/sessions/{session['id']}/answer", {
            "questionId": "question1", "answer": "Synthetic response: I combined one amount with the other.", "usedHelp": False,
        })
        attempt_id = result["attempt"]["id"]
        self.assertEqual(result["attempt"]["grade"], "pending")
        public = json.dumps(self.ok("GET", "/api/practice"))
        self.assertNotIn("PRIVATE_RUBRIC_SENTINEL", public)
        assistant_state = self.cli("practice-state")
        saved_attempt = next(a for a in assistant_state["attempts"] if a["id"] == attempt_id)
        self.assertIn("PRIVATE_RUBRIC_SENTINEL", saved_attempt["reviewContext"]["rubric"])
        self.assertEqual(self.call("POST", f"/api/assistant/practice/attempts/{attempt_id}/review", {
            "correct": True, "independent": True, "rubricEvidence": "Uncredentialed claim.",
        })[0], 401)
        self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Synthetic whole quiz ready for checks."})
        self.assertEqual(self.call("POST", f"/api/tasks/{task_id}/complete", {})[0], 409)
        review_text = "Synthetic rubric check only.\nThe learner connected both amounts; café is a UTF-8 round-trip check."
        review_file = self.json_file("rubric-review.json", {
            "correct": True, "independent": True, "rubricEvidence": review_text, "help": "",
        })
        reviewed = self.cli("practice-review", attempt_id, "--json-file", review_file)
        self.assertEqual(reviewed["attempt"]["grade"], "correct")
        self.assertTrue(self.task(task_id)["canComplete"])
        self.assertIsNone(self.task(task_id)["completedAt"])
        self.ok("POST", f"/api/tasks/{task_id}/complete", {})
        completed_at = self.task(task_id)["completedAt"]
        self.assertIsNotNone(completed_at)
        self.stop_server()
        self.start_server()
        restored = self.cli("practice-state")
        restored_attempt = next(a for a in restored["attempts"] if a["id"] == attempt_id)
        self.assertEqual(restored_attempt["feedback"], review_text)
        self.assertEqual(restored_attempt["grade"], "correct")
        cli_state = self.cli("state")
        self.assertEqual(next(t for t in cli_state["tasks"] if t["id"] == task_id)["completedAt"], completed_at)
        correction_file = self.json_file("review-correction.json", {"note": "Synthetic later correction: this sample needs another review."})
        withdrawn = self.cli("practice-revoke", attempt_id, "--json-file", correction_file)
        self.assertEqual(withdrawn["attempt"]["grade"], "withdrawn")
        self.assertGreater(len(withdrawn["attempt"]["history"]), len(restored_attempt["history"]))
        self.assertIsNone(self.task(task_id)["completedAt"])
        self.assertFalse(self.task(task_id)["canComplete"])

    def test_data_directory_lock_rejects_another_process_and_releases_on_close(self):
        self.setup_parent()
        script = (
            "import sys\n"
            "from server import create_server\n"
            "try:\n"
            "    instance = create_server(sys.argv[1], port=0)\n"
            "except RuntimeError as error:\n"
            "    print(str(error))\n"
            "    raise SystemExit(0)\n"
            "else:\n"
            "    instance.server_close()\n"
            "    instance.store.db.close()\n"
            "    raise SystemExit(2)\n"
        )
        result = subprocess.run([sys.executable, "-c", script, str(self.data_dir)], cwd=APP_ROOT,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already open", result.stdout)
        self.assertEqual(self.ok("GET", "/api/health")["app"], "river-after-school")
        self.stop_server()
        self.start_server()
        self.assertTrue(self.ok("GET", "/api/auth")["configured"])

    def test_scope_and_duration_edits_reopen_completion_without_erasing_history(self):
        self.setup_parent()
        body = {"subject": "Synthetic subject", "title": "Synthetic task", "instructions": "One brief synthetic exercise.",
                "planDate": "2026-09-08", "dueDate": "", "sourceUrl": "", "criteria": "Explain the brief example.",
                "sourceNote": "Temporary test only.", "scope": "assignment", "assignmentId": "", "plannedMinutes": 20,
                "requirements": {"understanding": True, "submission": False, "parent": False}}
        task_id = self.ok("POST", "/api/parent/tasks", body, parent=True)["id"]
        for changed_field, new_value in (("scope", "work_block"), ("plannedMinutes", 30)):
            self.ok("POST", f"/api/tasks/{task_id}/report", {"note": "Synthetic exercise finished."})
            self.ok("POST", f"/api/assistant/tasks/{task_id}/understanding", {
                "goal": "Synthetic learning goal", "evidence": "Synthetic independent example explanation.",
                "help": "", "method": "fresh_example", "independent": True,
            }, assistant=True)
            self.ok("POST", f"/api/tasks/{task_id}/complete", {})
            previous = self.task(task_id)
            body[changed_field] = new_value
            self.ok("PUT", f"/api/parent/tasks/{task_id}", body, parent=True)
            updated = self.task(task_id)
            self.assertIsNone(updated["completedAt"])
            self.assertIsNone(updated["reportedAt"])
            self.assertIsNone(updated["understanding"])
            self.assertFalse(updated["canComplete"])
            self.assertGreater(len(updated["history"]), len(previous["history"]))
            self.assertIn("Synthetic independent example explanation.", json.dumps(updated["history"]))

    def test_daily_timers_stop_at_midnight_and_new_day_starts_at_zero(self):
        self.now = self.now.replace(hour=23, minute=50)
        self.ok("POST", "/api/braces/start", {})
        self.ok("POST", "/api/school-clock/start", {})
        self.now += timedelta(minutes=20)
        previous = self.ok("GET", "/api/state?date=2026-09-08")
        current = self.ok("GET", "/api/state")
        self.assertEqual(current["date"], "2026-09-09")
        for name in ("braces", "schoolClock"):
            self.assertEqual(previous[name]["elapsedSeconds"], 600)
            self.assertFalse(previous[name]["running"])
            self.assertEqual(current[name]["elapsedSeconds"], 0)
            self.assertFalse(current[name]["running"])
        self.now += timedelta(hours=6)
        still_previous = self.ok("GET", "/api/state?date=2026-09-08")
        self.assertEqual(still_previous["braces"]["elapsedSeconds"], 600)
        self.assertFalse(still_previous["braces"]["timerElapsed"])
        self.assertEqual(still_previous["schoolClock"]["elapsedSeconds"], 600)
        self.assertFalse(still_previous["schoolClock"]["limitReached"])


if __name__ == "__main__":
    unittest.main()
