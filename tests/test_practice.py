"""Quiz behavior with temporary, clearly synthetic content; no network required."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from practice import PracticeError, practice_mutate, practice_state, practice_task_review
from server import RiverStore


class PracticeBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="river-practice-test-")
        self.now = datetime(2026, 9, 8, 15, 0, tzinfo=ZoneInfo("America/Indiana/Indianapolis"))
        self.store = RiverStore(Path(self.tmp.name), now=lambda: self.now)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def mutate(self, path, body=None, actor="river"):
        return self.store.transaction(lambda: practice_mutate(self.store, path, body or {}, actor))

    def body(self, count=3, target=3, sessions=2):
        return {"title": "Synthetic fixture quiz", "subject": "Synthetic math", "planDate": "2026-09-08",
                "sourceMaterial": "Synthetic test source only; these are not actual missed school questions.",
                "sourceNote": "Temporary test record; no portal access or submission.", "sourceUrl": "",
                "concepts": [{"id": "c1", "label": "Addition", "goal": "Explain one small addition example",
                              "targetCorrect": target, "targetSessions": sessions}],
                "questions": [{"id": f"q{i}", "conceptId": "c1", "prompt": f"Synthetic item {i}: {i}+1",
                               "type": "exact", "acceptedAnswers": [str(i+1)], "rubric": "",
                               "explanation": "Combine the two amounts.", "hint": "Start with the first amount.",
                               "options": [], "required": True, "origin": "missed" if i == 1 else "variation"}
                              for i in range(1, count+1)]}

    def create(self, body=None, actor="parent", publish=True):
        route = "/api/assistant/practice/drafts" if actor == "assistant" else "/api/parent/practice/quizzes"
        ident = self.mutate(route, body or self.body(), actor)["quizIds"][0]
        if publish:
            self.publish(ident)
        return ident

    def publish(self, ident):
        return self.mutate(f"/api/parent/practice/quizzes/{ident}/publish", {"reviewed": True}, "parent")

    def quiz(self, ident, actor="parent"):
        return next(q for q in practice_state(self.store, actor)["quizzes"] if q["id"] == ident)

    def start(self, ident):
        return self.mutate("/api/practice/sessions/start", {"quizId": ident})["session"]

    def answer(self, session, value=None, used_help=False):
        current = practice_state(self.store)["activeSession"]["currentQuestion"]
        if value is None:
            value = str(int(current["id"][1:])+1)
        return self.mutate(f"/api/practice/sessions/{session['id']}/answer",
                           {"questionId": current["id"], "answer": value, "usedHelp": used_help})

    def next(self, session):
        return self.mutate(f"/api/practice/sessions/{session['id']}/next")["session"]

    def end(self, session):
        return self.mutate(f"/api/practice/sessions/{session['id']}/end")

    def rubric_body(self):
        body = self.body(1, 1, 1)
        body["questions"][0].update(type="rubric", acceptedAnswers=[],
                                    rubric="Explain both amounts and how they combine; a copied answer is insufficient.")
        return body

    def review(self, attempt, correct=True, independent=True, help_note="", actor="assistant"):
        return self.mutate(f"/api/{actor}/practice/attempts/{attempt['id']}/review", {
            "correct": correct, "independent": independent,
            "rubricEvidence": "Synthetic review: checked the explanation against the listed rubric.", "help": help_note,
        }, actor)

    def test_drafts_require_parent_review_and_keys_stay_private(self):
        body = self.body()
        body["questions"][0]["acceptedAnswers"] = ["PRIVATE_KEY_SENTINEL"]
        body["sourceMaterial"] = "PRIVATE_SOURCE_SENTINEL, synthetic source only"
        ident = self.create(body, actor="assistant", publish=False)
        self.assertEqual(practice_state(self.store)["quizzes"], [])
        self.assertEqual(len(self.store.state["tasks"]), 0)
        for actor in ("river", "assistant"):
            with self.assertRaises(PracticeError):
                self.mutate(f"/api/parent/practice/quizzes/{ident}/publish", {"reviewed": True}, actor)
            with self.assertRaises(PracticeError):
                self.mutate("/api/parent/practice/settings", {"sessionQuestions": 10}, actor)
        self.publish(ident)
        self.publish(ident)
        self.assertEqual(len(self.store.state["tasks"]), 1)
        public = json.dumps(practice_state(self.store))
        self.assertNotIn("PRIVATE_KEY_SENTINEL", public)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", public)
        self.assertNotIn("acceptedAnswers", public)
        self.assertIn("PRIVATE_KEY_SENTINEL", json.dumps(practice_state(self.store, "assistant")))
        self.assertEqual(practice_state(self.store)["attempts"], [])

    def test_publish_approves_plan_without_per_question_attestation_and_checks_revision(self):
        ident = self.create(actor="assistant", publish=False)
        route = f"/api/parent/practice/quizzes/{ident}/publish"
        for actor in ("river", "assistant"):
            with self.assertRaises(PracticeError):
                self.mutate(route, {"approved": True, "expectedRevision": 1}, actor)
        for body in ({}, {"approved": True, "expectedRevision": 0}, {"approved": True, "expectedRevision": True}):
            with self.assertRaises(PracticeError):
                self.mutate(route, body, "parent")
        self.assertFalse(self.quiz(ident)["published"])
        self.assertEqual(len(self.store.state["tasks"]), 0)
        self.mutate(route, {"approved": True, "expectedRevision": 1}, "parent")
        self.mutate(route, {"approved": True, "expectedRevision": 1}, "parent")
        self.assertTrue(self.quiz(ident)["published"])
        self.assertEqual(len(self.store.state["tasks"]), 1)
        self.assertEqual(practice_state(self.store)["attempts"], [])

    def test_publish_can_replace_unstarted_task_without_extra_work(self):
        task = self.store.transaction(lambda: self.store.create_task({
            "title": "Synthetic old plan", "subject": "Synthetic math", "instructions": "Synthetic fixture",
            "planDate": "2026-09-08", "plannedMinutes": 45, "scope": "work_block",
            "sourceUrl": "https://example.com/old", "requirements": {"parent": True}}))
        ident = self.create(actor="assistant", publish=False)
        route = f"/api/parent/practice/quizzes/{ident}/publish"
        payload = {"approved": True, "expectedRevision": 1, "existingTaskId": task["id"]}
        for actor in ("assistant", "river"):
            with self.assertRaises(PracticeError): self.mutate(route, payload, actor)
        self.mutate(route, payload, "parent")
        self.mutate(route, payload, "parent")
        self.assertEqual(len(self.store.state["tasks"]), 1)
        updated = self.store.task(task["id"])
        self.assertEqual(updated["plannedMinutes"], 45)
        self.assertEqual(updated["practiceQuizId"], ident)
        self.assertEqual(updated["sourceUrl"], "")
        self.assertFalse(updated.get("completedAt"))
        preserved = next(h for h in updated["history"] if h["action"] == "practice_attached")
        self.assertEqual(preserved["details"]["before"]["title"], "Synthetic old plan")

    def test_publish_replacement_rejects_completed_or_wrong_day_task(self):
        task = self.store.transaction(lambda: self.store.create_task({
            "title": "Synthetic existing", "subject": "Synthetic math", "instructions": "Fixture",
            "planDate": "2026-09-09"}))
        ident = self.create(actor="assistant", publish=False)
        route = f"/api/parent/practice/quizzes/{ident}/publish"
        payload = {"approved": True, "existingTaskId": task["id"]}
        with self.assertRaises(PracticeError): self.mutate(route, payload, "parent")
        self.store.task(task["id"])["planDate"] = "2026-09-08"
        self.store.task(task["id"])["reportedAt"] = self.store.stamp()
        with self.assertRaises(PracticeError): self.mutate(route, payload, "parent")
        self.assertFalse(self.quiz(ident)["published"])
        self.assertEqual(len(self.store.state["tasks"]), 1)

    def test_whole_quiz_requires_varied_answers_across_sessions_and_never_autocompletes(self):
        ident = self.create()
        self.mutate("/api/parent/practice/settings", {"sessionQuestions": 10}, "parent")
        first = self.start(ident)
        self.answer(first)
        self.next(first)
        self.answer(first)
        reserved = self.next(first)
        self.assertIsNone(reserved["currentQuestion"])
        self.assertTrue(reserved["needsBreak"])
        self.assertFalse(self.quiz(ident)["progress"]["criteriaMet"])
        self.end(first)
        second = self.start(ident)
        self.assertNotEqual(first["id"], second["id"])
        self.answer(second)
        quiz = self.quiz(ident)
        self.assertTrue(quiz["progress"]["criteriaMet"])
        self.assertEqual(quiz["concepts"][0]["correctCount"], 3)
        self.assertEqual(quiz["concepts"][0]["sessionCount"], 2)
        task = self.store.task(quiz["taskId"])
        self.assertEqual(practice_task_review(self.store, task), [])
        self.assertIsNone(task["completedAt"])
        self.assertIsNone(task["reportedAt"])
        self.assertEqual(len(self.store.state["tasks"]), 1)

    def test_all_concepts_and_required_reviews_gate_the_single_task(self):
        body = self.body(2, 1, 1)
        body["concepts"].append({"id": "c2", "label": "Explain reasoning", "goal": "Explain a fresh example", "targetCorrect": 1, "targetSessions": 1})
        body["questions"][1].update(conceptId="c2", type="rubric", acceptedAnswers=[], rubric="Explanation connects both amounts.")
        ident = self.create(body)
        session = self.start(ident)
        self.answer(session)
        self.next(session)
        pending = self.answer(session, "I combined both amounts in this synthetic example.")["attempt"]
        quiz = self.quiz(ident)
        self.assertTrue(quiz["concepts"][0]["targetMet"])
        self.assertFalse(quiz["concepts"][1]["targetMet"])
        self.assertEqual(quiz["progress"]["pendingReviews"], 1)
        self.assertFalse(quiz["progress"]["criteriaMet"])
        self.assertEqual(len(self.store.state["tasks"]), 1)
        with self.assertRaises(PracticeError):
            self.review(pending, actor="river")
        self.review(pending)
        self.assertTrue(self.quiz(ident)["progress"]["criteriaMet"])
        self.assertIsNone(self.store.task(quiz["taskId"])["completedAt"])

    def test_hints_and_supported_answers_do_not_count_as_independent(self):
        ident = self.create(self.body(1, 1, 1))
        session = self.start(ident)
        hint = self.mutate(f"/api/practice/sessions/{session['id']}/hint", {"questionId": "q1"})
        self.assertTrue(hint["hint"])
        result = self.answer(session)
        self.assertEqual(result["attempt"]["grade"], "correct")
        self.assertTrue(result["attempt"]["supported"])
        self.assertFalse(result["attempt"]["independent"])
        quiz = self.quiz(ident)
        self.assertEqual(quiz["progress"]["requiredDone"], 1)
        self.assertEqual(quiz["concepts"][0]["correctCount"], 0)
        self.assertFalse(quiz["progress"]["criteriaMet"])

    def test_assistant_supplied_free_response_does_not_count_as_independent(self):
        ident = self.create(self.rubric_body())
        session = self.start(ident)
        pending = self.answer(session, "A saved free response.")["attempt"]
        self.assertEqual(pending["grade"], "pending")
        self.review(pending, independent=False, help_note="Assistant supplied the explanation; independent check still needed.")
        quiz = self.quiz(ident)
        self.assertEqual(quiz["progress"]["requiredDone"], 1)
        self.assertFalse(quiz["concepts"][0]["targetMet"])
        self.assertFalse(quiz["progress"]["criteriaMet"])

    def test_wrong_response_requires_fresh_followup_and_retry_is_supported(self):
        ident = self.create(self.body(2, 1, 1))
        session = self.start(ident)
        wrong = self.answer(session, "wrong")["attempt"]
        self.assertEqual(wrong["grade"], "incorrect")
        self.assertTrue(self.quiz(ident)["concepts"][0]["needsFollowup"])
        self.next(session)
        self.answer(session)
        self.assertFalse(self.quiz(ident)["concepts"][0]["needsFollowup"])
        self.end(session)
        retry = self.start(ident)
        self.assertEqual(retry["currentQuestion"]["id"], "q1")
        retried = self.answer(retry)["attempt"]
        self.assertTrue(retried["supported"])
        self.assertFalse(retried["independent"])
        self.assertTrue(self.quiz(ident)["progress"]["criteriaMet"])

    def test_pause_resume_survives_restart_without_a_new_session_credit(self):
        ident = self.create()
        session = self.start(ident)
        self.answer(session)
        self.now += timedelta(minutes=2)
        self.mutate(f"/api/practice/sessions/{session['id']}/pause")
        self.now += timedelta(minutes=10)
        self.store.db.close()
        self.store = RiverStore(Path(self.tmp.name), now=lambda: self.now)
        saved = practice_state(self.store)["activeSession"]
        self.assertEqual(saved["id"], session["id"])
        self.assertEqual(saved["elapsedSeconds"], 120)
        self.assertIsNotNone(saved["currentAttempt"])
        self.mutate(f"/api/practice/sessions/{session['id']}/resume")
        self.now += timedelta(minutes=1)
        resumed = practice_state(self.store)["activeSession"]
        self.assertEqual(resumed["elapsedSeconds"], 180)
        self.assertEqual(self.quiz(ident)["concepts"][0]["sessionCount"], 1)

    def test_ninety_minute_limit_blocks_new_work_but_allows_review_and_next_day(self):
        ident = self.create(self.rubric_body())
        session = self.start(ident)
        pending = self.answer(session, "Saved explanation before the time limit.")["attempt"]
        for interval in range(3):
            if interval:
                self.store.transaction(lambda: self.store.mutate("/api/school-clock/start", {"kind":"work"}, "river"))
            self.now += timedelta(minutes=30)
        self.assertTrue(practice_state(self.store)["schoolClock"]["limitReached"])
        with self.assertRaises(PracticeError):
            self.next(session)
        self.review(pending)
        self.mutate(f"/api/practice/sessions/{session['id']}/pause")
        self.end(session)
        with self.assertRaises(PracticeError):
            self.start(ident)
        self.now += timedelta(days=1)
        next_day = self.start(ident)
        self.assertNotEqual(next_day["id"], session["id"])
        self.assertFalse(practice_state(self.store)["schoolClock"]["limitReached"])
        self.assertEqual(len(self.store.state["tasks"]), 1)
        self.assertIsNone(self.store.task(self.quiz(ident)["taskId"])["completedAt"])

    def test_adding_variations_preserves_credit_and_changing_key_preserves_history(self):
        body = self.body(1, 1, 1)
        ident = self.create(body)
        session = self.start(ident)
        first = self.answer(session)["attempt"]
        task_id = self.quiz(ident)["taskId"]
        body["questions"].append(self.body(2, 1, 1)["questions"][1])
        body["title"] = "Updated synthetic quiz"
        self.mutate(f"/api/parent/practice/quizzes/{ident}", body, "parent")
        self.assertIsNone(practice_state(self.store)["activeSession"])
        self.assertFalse(self.quiz(ident)["published"])
        self.publish(ident)
        self.assertEqual(self.quiz(ident)["progress"]["requiredDone"], 1)
        self.assertEqual(self.quiz(ident)["concepts"][0]["correctCount"], 1)
        self.assertEqual(self.store.task(task_id)["title"], "Updated synthetic quiz")
        body["questions"][0]["acceptedAnswers"] = ["corrected synthetic key"]
        body["planDate"] = "2026-09-09"
        self.mutate(f"/api/parent/practice/quizzes/{ident}", body, "parent")
        self.publish(ident)
        self.assertEqual(self.quiz(ident)["progress"]["requiredDone"], 0)
        self.assertEqual(self.store.task(task_id)["planDate"], "2026-09-09")
        self.assertEqual(len(self.store.state["tasks"]), 1)
        self.assertIn(first["id"], {a["id"] for a in practice_state(self.store, "parent")["attempts"]})

    def test_later_review_correction_reopens_completed_task_and_preserves_evidence(self):
        ident = self.create(self.rubric_body())
        session = self.start(ident)
        attempt = self.answer(session, "Synthetic explanation.")["attempt"]
        self.review(attempt)
        task = self.store.task(self.quiz(ident)["taskId"])
        task["completedAt"] = self.store.stamp()  # Isolated fixture representing Student's later checkoff.
        self.now += timedelta(minutes=1)
        self.review(attempt, correct=False, independent=False)
        self.assertIsNone(task["completedAt"])
        self.assertTrue(self.quiz(ident)["concepts"][0]["needsFollowup"])
        saved = practice_state(self.store, "parent")["attempts"][0]
        self.assertEqual(saved["grade"], "incorrect")
        self.assertGreaterEqual(len(saved["history"]), 3)

    def test_key_edit_cannot_turn_an_exposed_prompt_into_a_fresh_independent_example(self):
        body = self.body(1, 1, 1)
        ident = self.create(body)
        session = self.start(ident)
        self.answer(session, "initial wrong response")
        body["questions"][0]["acceptedAnswers"] = ["corrected synthetic key"]
        self.mutate(f"/api/parent/practice/quizzes/{ident}", body, "parent")
        self.publish(ident)
        fresh_session = self.start(ident)
        repeated = self.answer(fresh_session, "corrected synthetic key")["attempt"]
        self.assertEqual(repeated["grade"], "correct")
        self.assertTrue(repeated["supported"])
        self.assertFalse(repeated["independent"])
        self.assertFalse(self.quiz(ident)["progress"]["criteriaMet"])

    def test_withdrawn_credit_does_not_silently_leave_task_complete(self):
        ident = self.create(self.body(1, 1, 1))
        session = self.start(ident)
        attempt = self.answer(session)["attempt"]
        task = self.store.task(self.quiz(ident)["taskId"])
        task["completedAt"] = self.store.stamp()
        self.mutate(f"/api/assistant/practice/attempts/{attempt['id']}/revoke", {"note": "A later correction showed this response should not count."}, "assistant")
        self.assertIsNone(task["completedAt"])
        self.assertFalse(self.quiz(ident)["progress"]["criteriaMet"])
        self.assertEqual(practice_state(self.store)["attempts"][0]["grade"], "withdrawn")

    def test_replayed_answers_invalid_keys_and_unreasonable_settings_are_rejected(self):
        ident = self.create()
        session = self.start(ident)
        self.answer(session)
        with self.assertRaises(PracticeError):
            self.answer(session)
        with self.assertRaises(PracticeError):
            self.mutate("/api/parent/practice/settings", {"sessionMinutes": 91}, "parent")
        with self.assertRaises(PracticeError):
            self.mutate("/api/practice/sessions/start", {"quizId": ident}, "assistant")
        body = self.body(1, 1, 1)
        body["questions"][0].update(type="choice", options=["A", "B"], acceptedAnswers=["C"])
        invalid = self.create(body, publish=False)
        with self.assertRaises(PracticeError):
            self.publish(invalid)
        body = self.body(1, 1, 1)
        body["questions"].append(copy.deepcopy(body["questions"][0]))
        with self.assertRaises(PracticeError):
            self.create(body)


if __name__ == "__main__":
    unittest.main()
