"""Parent-reviewed quizzes with persistent sessions and independent evidence.

This module does not contact portals, generate questions, or complete school tasks.
The HTTP handler supplies the authenticated actor and wraps mutations in a store
transaction. Public views deliberately omit keys, rubrics, and source feedback.
"""
from __future__ import annotations

import os
import copy
from datetime import date, datetime, time, timedelta
import hashlib
import json
import re
import secrets
import unicodedata
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Indiana/Indianapolis")


class PracticeError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def _text(value, name, required=False, limit=12000):
    if not isinstance(value, str):
        raise PracticeError(f"Use text for {name}.")
    value = value.strip()
    if len(value) > limit:
        raise PracticeError(f"Please shorten {name} to {limit} characters.")
    if required and not value:
        raise PracticeError(f"Please include {name}.")
    return value


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise PracticeError(f"Choose {name} between {low} and {high}.")
    return value


def _bool(value, name):
    if type(value) is not bool:
        raise PracticeError(f"Choose yes or no for {name}.")
    return value


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _ident(value, fallback):
    ident = value or fallback
    if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", ident):
        raise PracticeError("Use letters, numbers, hyphens, or underscores for item IDs.")
    return ident


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def init_practice(store):
    data = store.state.setdefault("practice", {
        "settings": {"sessionQuestions": 2, "sessionMinutes": 15},
        "quizzes": [], "sessions": [], "attempts": [], "hints": [], "tutorChats": [], "revisits": [], "history": [],
    })
    data.setdefault("tutorChats", [])
    data.setdefault("revisits", [])
    data.setdefault("settings", {}).setdefault(
        "tutoringSupport",
        "Use short sentences and plain words. Define unfamiliar school vocabulary. Explain one small step at a time, "
        "using a concrete example that is different from the practice question. Ask one brief checking question when useful."
    )
    return data


def _history(store, obj, actor, action, details):
    obj.setdefault("history", []).append({"at": store.stamp(), "actor": actor,
                                           "action": action, "details": copy.deepcopy(details)})


def _find(items, ident, name):
    for item in items:
        if item["id"] == ident:
            return item
    raise PracticeError(f"That {name} was not found.", 404)


def _quiz(store, ident):
    return _find(init_practice(store)["quizzes"], ident, "quiz")


def _question(quiz, ident):
    return _find(quiz["questions"], ident, "question")


def _clock(store):
    if not hasattr(store, "school_clock_view"):
        return {"available": False, "elapsedSeconds": 0, "running": False, "limitReached": False}
    day = store.day(store.today())
    if "schoolClock" not in day:
        return {"available": False, "elapsedSeconds": 0, "running": False, "limitReached": False}
    return dict(store.school_clock_view(day["schoolClock"]), available=True)


def _require_time(store, start=False):
    clock = _clock(store)
    if not clock["available"] or not hasattr(store, "start_school_clock"):
        raise PracticeError("The school timer needs to be available before starting practice.", 503)
    if clock["limitReached"]:
        raise PracticeError("Today's 90-minute school block is finished. Your quiz is saved for another day.", 409)
    if start:
        store.start_school_clock()


def _fields(body, today):
    if not isinstance(body, dict):
        raise PracticeError("Use a quiz object with its source material, concepts, and questions.")
    review_mode = body.get("reviewMode", "practice")
    if review_mode not in ("practice", "mastery"):
        raise PracticeError("Choose practice or assistant-reviewed mastery mode.")
    sequential = _bool(body.get("sequential", False), "one step at a time")
    if sequential and review_mode != "mastery":
        raise PracticeError("Sequential steps require assistant-reviewed mastery mode.")
    plan_date = body.get("planDate", today)
    try:
        if not isinstance(plan_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan_date):
            raise ValueError()
        date.fromisoformat(plan_date)
    except ValueError:
        raise PracticeError("Use a plan date in YYYY-MM-DD format.")
    source_url = _text(body.get("sourceUrl", ""), "source link", limit=2000)
    parsed = urlparse(source_url)
    if source_url and (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password):
        raise PracticeError("Use an http or https source link.")
    raw_concepts = body.get("concepts", [])
    raw_questions = body.get("questions", [])
    if not isinstance(raw_concepts, list) or not 1 <= len(raw_concepts) <= 20:
        raise PracticeError("Include between one and twenty concepts inside this quiz.")
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 100:
        raise PracticeError("Include between one and one hundred questions inside this quiz.")
    concepts, seen = [], set()
    for c in raw_concepts:
        if not isinstance(c, dict):
            raise PracticeError("Each concept needs a label and goal.")
        label = _text(c.get("label", ""), "concept label", True, 200)
        ident = _ident(c.get("id"), "c_" + _digest(_norm(label)))
        if ident in seen:
            raise PracticeError("Concept IDs must be unique inside this quiz.")
        seen.add(ident)
        target = _int(c.get("targetCorrect", 3), "distinct correct answers", 1, 20)
        sessions = _int(c.get("targetSessions", 2), "sessions", 1, 10)
        if sessions > target:
            raise PracticeError("The session target cannot exceed the distinct-answer target.")
        concepts.append({"id": ident, "label": label, "goal": _text(c.get("goal", label), "learning goal", True, 2000),
                         "targetCorrect": target, "targetSessions": sessions})
    questions, seen_ids, seen_prompts = [], set(), set()
    concepts_by_id = {c["id"]: c for c in concepts}
    for q in raw_questions:
        if not isinstance(q, dict) or not isinstance(q.get("conceptId"), str) or q.get("conceptId") not in concepts_by_id:
            raise PracticeError("Every question must refer to one of this quiz's concepts.")
        prompt = _text(q.get("prompt", ""), "question", True, 8000)
        normalized_prompt = (q["conceptId"], _norm(prompt))
        if normalized_prompt in seen_prompts:
            raise PracticeError("Use genuinely different question text for each varied example.")
        seen_prompts.add(normalized_prompt)
        ident = _ident(q.get("id"), "q_" + _digest(normalized_prompt))
        if ident in seen_ids:
            raise PracticeError("Question IDs must be unique inside this quiz.")
        seen_ids.add(ident)
        kind = q.get("type", "exact")
        if kind not in ("exact", "choice", "rubric"):
            raise PracticeError("Choose exact, choice, or rubric for a question type.")
        origin = q.get("origin", "variation")
        if origin not in ("missed", "variation"):
            raise PracticeError("Identify a question as missed or variation.")
        options, answers = q.get("options", []), q.get("acceptedAnswers", [])
        if not isinstance(options, list) or len(options) > 12 or not isinstance(answers, list) or len(answers) > 30:
            raise PracticeError("Use lists for choices and accepted answers.")
        options = [_text(x, "choice", True, 2000) for x in options]
        answers = [_text(x, "accepted answer", True, 4000) for x in answers]
        if len({_norm(x) for x in options}) != len(options):
            raise PracticeError("Question choices must be different.")
        item = {"id": ident, "conceptId": q["conceptId"], "prompt": prompt, "type": kind,
                "options": options, "acceptedAnswers": answers, "rubric": _text(q.get("rubric", ""), "rubric"),
                "explanation": _text(q.get("explanation", ""), "explanation"),
                "hint": _text(q.get("hint", ""), "hint", limit=4000),
                "required": _bool(q.get("required", True), "required question"), "origin": origin}
        raw_hints = q.get("hintSteps", [])
        if not isinstance(raw_hints,list) or len(raw_hints)>5:
            raise PracticeError("Use up to five graduated reading hints.")
        item["hintSteps"] = [_text(h,"hint",True,4000) for h in raw_hints]
        if review_mode == "mastery" and kind != "rubric":
            raise PracticeError("Mastery review uses short answers reviewed by the assistant.")
        if q.get('effortPolicy') is not None:
            if q['effortPolicy'] != 'three-genuine-efforts' or review_mode != 'mastery' or kind != 'rubric':
                raise PracticeError('Evening effort coaching requires a mastery rubric question.')
            item['effortPolicy'] = q['effortPolicy']
        version_fields={k:item[k] for k in ("conceptId","prompt","type","options","acceptedAnswers","rubric")}
        if q.get('writingRequirements') is not None:
            if review_mode!='mastery' or kind!='rubric':raise PracticeError('Writing sentence review requires assistant-reviewed rubric mode.')
            from writing_review import requirements
            item['writingRequirements']=requirements(q['writingRequirements'])
            version_fields['writingRequirements']=item['writingRequirements']
        if item.get('effortPolicy'): version_fields['effortPolicy'] = item['effortPolicy']
        if sequential:
            version_fields["precedingSteps"] = [x["questionVersion"] for x in questions]
        item["questionVersion"] = _digest(version_fields)
        questions.append(item)
    return {"reviewMode":review_mode,"sequential":sequential,"title": _text(body.get("title", ""), "quiz title", True, 250),
            "subject": _text(body.get("subject", ""), "subject", True, 100),
            "sourceMaterial": _text(body.get("sourceMaterial", ""), "actual missed questions and feedback", True, 30000),
            "sourceNote": _text(body.get("sourceNote", ""), "source note", limit=8000),
            "sourceUrl": source_url, "planDate": plan_date, "concepts": concepts, "questions": questions}


def _publishable(quiz):
    for c in quiz["concepts"]:
        if not any(q["conceptId"] == c["id"] for q in quiz["questions"]):
            raise PracticeError(f"Add at least one question for {c['label']}.")
    for q in quiz["questions"]:
        if q["type"] == "rubric" and not q["rubric"]:
            raise PracticeError("Each free response needs a reviewed rubric before publishing.")
        if q["type"] in ("exact", "choice") and not q["acceptedAnswers"]:
            raise PracticeError("Each scored question needs a reviewed accepted answer before publishing.")
        if q["type"] == "choice" and (len(q["options"]) < 2 or any(_norm(a) not in {_norm(x) for x in q["options"]} for a in q["acceptedAnswers"])):
            raise PracticeError("A choice question needs at least two choices and a key matching a listed choice.")


def _valid_attempts(store, quiz):
    versions = {q["id"]: q["questionVersion"] for q in quiz["questions"]}
    return [a for a in init_practice(store)["attempts"] if a["quizId"] == quiz["id"]
            and versions.get(a["questionId"]) == a["questionVersion"]]


def _progress(store, quiz):
    from mastery import is_mastery, progress
    if is_mastery(quiz):return progress(store,quiz)
    attempts = _valid_attempts(store, quiz)
    required_ids = {q["id"] for q in quiz["questions"] if q["required"]}
    correct_ids = {a["questionId"] for a in attempts if a["grade"] == "correct" and a["reviewedAt"]}
    pending = [a for a in attempts if a["grade"] == "pending"]
    required_pending = [a for a in pending if a["questionId"] in required_ids]
    reasons = []
    if not quiz["published"]:
        reasons.append("The parent needs to review and publish this quiz.")
    missing = len(required_ids - correct_ids)
    if missing:
        reasons.append(f"{missing} required question(s) still need a correct reviewed response.")
    if required_pending:
        reasons.append(f"{len(required_pending)} required response(s) await review with the assistant.")
    concepts = []
    for c in quiz["concepts"]:
        distinct = {}
        for a in attempts:
            if a["conceptId"] == c["id"] and a["grade"] == "correct" and a["independent"]:
                distinct.setdefault(a["questionId"], a)
        count, sessions = len(distinct), len({a["sessionId"] for a in distinct.values()})
        concept_attempts = [(index, a) for index, a in enumerate(attempts) if a["conceptId"] == c["id"]]
        wrong_events = [(a["reviewedAt"] or a["at"], index) for index, a in concept_attempts if a["grade"] == "incorrect"]
        fresh_events = [(a["reviewedAt"] or a["at"], index) for index, a in concept_attempts if a["grade"] == "correct" and a["independent"]]
        followup = bool(wrong_events and (not fresh_events or max(wrong_events) > max(fresh_events)))
        met = count >= c["targetCorrect"] and sessions >= c["targetSessions"] and not followup
        concepts.append(dict(c, correctCount=count, sessionCount=sessions, targetMet=met, needsFollowup=followup))
        if not met:
            reasons.append(f"{c['label']}: {count}/{c['targetCorrect']} varied correct answers without recorded help, across {sessions}/{c['targetSessions']} sessions.")
        if followup:
            reasons.append(f"Review {c['label']} and try a fresh example after the last incorrect response.")
    return {"requiredDone": len(required_ids & correct_ids), "requiredTotal": len(required_ids),
            "pendingReviews": len(pending), "criteriaMet": not reasons, "reasons": reasons}, concepts


def practice_task_review(store, task):
    quiz_id = task.get("practiceQuizId")
    if not quiz_id:
        return []
    try:
        quiz = _quiz(store, quiz_id)
    except PracticeError:
        return ["The linked quiz needs parent review before this task can be finished."]
    return _progress(store, quiz)[0]["reasons"]


def _sync_task(store, quiz):
    if quiz.get("taskId"):
        task = store.task(quiz["taskId"])
        reasons = practice_task_review(store, task)
        if task.get("completedAt") and reasons:
            task["completedAt"] = None
            _history(store, task, "system", "quiz_completion_reopened", {"reasons": reasons})


def _public_question(q):
    return {k: copy.deepcopy(q[k]) for k in ("id", "conceptId", "prompt", "type", "options", "required", "origin")}


def _attempt_view(attempt, actor):
    value = copy.deepcopy(attempt)
    if actor == "river":
        value.pop("reviewContext", None)
        if value.get("effortReview"): value["effortReview"].pop("reason",None)
        if value.get("mastery"):
            value.pop("reviewEvidence",None)
            value.pop("history",None)
    return value


def _elapsed(store, session):
    elapsed = session["elapsedSeconds"]
    if session.get("resumedAt") and not session["endedAt"]:
        end = datetime.combine(date.fromisoformat(session["date"]) + timedelta(days=1), time.min, LOCAL_TZ)
        elapsed += max(0, (min(store.now(), end) - datetime.fromisoformat(session["resumedAt"])).total_seconds())
    return max(0, int(elapsed))


def _session_view(store, session, actor="river"):
    quiz = _quiz(store, session["quizId"])
    settings = init_practice(store)["settings"]
    elapsed = _elapsed(store, session)
    reason = ""
    if session["date"] != store.today():
        reason = "This session is saved from another day. End it and begin a fresh session."
    elif _clock(store)["limitReached"]:
        reason = "Today's 90-minute school block is finished. This quiz stays saved."
    elif elapsed >= settings["sessionMinutes"] * 60:
        reason = "This practice session has reached its time target. Save a break and continue in another session."
    elif session["questionCount"] >= settings["sessionQuestions"]:
        reason = "You have reached this session's question target. The whole quiz stays saved for further practice."
    current = _question(quiz, session["currentQuestionId"]) if session.get("currentQuestionId") else None
    answered = bool(session.get("currentAttemptId"))
    if not current and not reason and session.get("distributionBreak"):
        reason = "Save this session and return for a fresh session; the remaining examples are being spread across sessions."
    if not current and not reason:
        progress, _ = _progress(store, quiz)
        reason = "This quiz's practice checks are met. You can review the whole task in Today." if progress["criteriaMet"] else "End this session to revisit unfinished questions. If there are no fresh examples left, ask the parent or assistant to prepare more variations."
    attempt = _find(init_practice(store)["attempts"], session["currentAttemptId"], "response") if answered else None
    result = {k: copy.deepcopy(session.get(k)) for k in ("id", "quizId", "date", "startedAt", "pausedAt", "endedAt", "questionCount")}
    result.update(elapsedSeconds=elapsed, currentQuestion=_public_question(current) if current else None,
                  currentAttempt=_attempt_view(attempt, actor) if attempt else None,
                  canAnswer=bool(current and not answered and not reason and not session["pausedAt"] and not session["endedAt"] and quiz["published"]),
                  needsBreak=bool(reason), reason=reason, readyForNext=answered)
    return result


def practice_state(store, actor="river"):
    if actor not in ("river", "parent", "assistant", "planner", "manager"):
        raise PracticeError("That practice access is not available.", 403)
    data = init_practice(store)
    quizzes = []
    for quiz in data["quizzes"]:
        if actor == "river" and not quiz["published"] and (quiz["planDate"] > store.today() or not quiz.get("taskId")):
            continue
        progress, concepts = _progress(store, quiz)
        if actor == "river":
            view = {k: copy.deepcopy(quiz[k]) for k in ("id", "taskId", "title", "subject", "published", "reviewedAt", "planDate")}
            view["questions"] = [_public_question(q) for q in quiz["questions"]] if quiz["published"] else []
        else:
            view = copy.deepcopy(quiz)
        from mastery import is_mastery, status
        if is_mastery(quiz):
            view["reviewMode"]="mastery"
            view["sequential"]=quiz.get("sequential",False)
            view["mastery"]=status(store,quiz)
            from writing_coach import policy
            view["writingPolicy"]=policy(store,quiz)
        view.update(progress=progress, concepts=concepts)
        quizzes.append(view)
    allowed_ids = {q["id"] for q in quizzes}
    active = next((s for s in reversed(data["sessions"]) if not s["endedAt"]), None)
    from writing_coach import view as writing_view
    from tutoring import public_chats
    return {"today": store.today(), "settings": copy.deepcopy(data["settings"]), "quizzes": quizzes,
            **({"writingCoach": writing_view(store)} if actor != 'river' else {}),
            "activeSession": _session_view(store, active, actor) if active else None,
            "attempts": [_attempt_view(a, actor) for a in data["attempts"] if actor != "river" or a["quizId"] in allowed_ids],
            "tutorChats": public_chats(store, allowed_ids),
            "schoolClock": _clock(store)}


def _stop_session(store, session, end=False, reason=""):
    session["elapsedSeconds"] = _elapsed(store, session)
    session["resumedAt"] = None
    session["pausedAt"] = store.stamp()
    if end:
        session["endedAt"] = store.stamp()
    _history(store, session, "river" if not reason else "system", "session_ended" if end else "session_paused", {"reason": reason})


def _select_question(store, quiz, session):
    attempts = _valid_attempts(store, quiz)
    attempted_here = {a["questionId"] for a in attempts if a["sessionId"] == session["id"]}
    attempted = {a["questionId"] for a in attempts}
    correct = {a["questionId"] for a in attempts if a["grade"] == "correct"}
    pending = {a["questionId"] for a in attempts if a["grade"] == "pending"}
    independent = {a["questionId"] for a in attempts if a["grade"] == "correct" and a["independent"]}
    _, concepts = _progress(store, quiz)
    unfinished_concepts = {c["id"] for c in concepts if not c["targetMet"]}
    candidates = [q for q in quiz["questions"] if q["id"] not in attempted_here and q["id"] not in pending
                  and (q["required"] and q["id"] not in correct or q["id"] not in attempted and q["conceptId"] in unfinished_concepts)
                  and q["id"] not in independent]
    candidates.sort(key=lambda q: (q["id"] in attempted, not q["required"]))
    # Reserve fresh examples for the configured number of separate sessions.
    # Merely ending and resuming the same session never creates new session credit.
    deferred = set()
    for concept in concepts:
        samples = {}
        for attempt in attempts:
            if attempt["conceptId"] == concept["id"] and attempt["grade"] == "correct" and attempt["independent"]:
                samples.setdefault(attempt["questionId"], attempt)
        others = [a for a in samples.values() if a["sessionId"] != session["id"]]
        other_sessions = len({a["sessionId"] for a in others})
        current_count = len(samples) - len(others)
        reserve = max(0, concept["targetSessions"] - other_sessions - 1)
        quota = max(1, concept["targetCorrect"] - len(others) - reserve)
        if reserve and current_count >= quota:
            deferred.add(concept["id"])
    available = [q for q in candidates if q["conceptId"] not in deferred or q["id"] in attempted]
    session["distributionBreak"] = bool(candidates and not available)
    candidates = available
    session["currentQuestionId"] = candidates[0]["id"] if candidates else None
    session["currentAttemptId"] = None


def _ensure_session(store, ident, work=False):
    session = _find(init_practice(store)["sessions"], ident, "session")
    if session["endedAt"]:
        raise PracticeError("This session is already saved. Start a new session when ready.", 409)
    quiz = _quiz(store, session["quizId"])
    if work:
        _require_time(store)
        if not quiz["published"]:
            raise PracticeError("The parent is reviewing this quiz. Your earlier responses are saved.", 409)
        view = _session_view(store, session)
        if session["pausedAt"]:
            raise PracticeError("Resume this session before answering.", 409)
        if view["needsBreak"] and view["currentQuestion"]:
            raise PracticeError(view["reason"], 409)
        if session["date"] != store.today():
            raise PracticeError("End the saved session and begin a session for today.", 409)
    return session, quiz


def _add_drafts(store, body, actor):
    bodies = body.get("quizzes", [body])
    if not isinstance(bodies, list) or not 1 <= len(bodies) <= 20:
        raise PracticeError("Import between one and twenty quiz drafts.")
    validated = [_fields(item, store.today()) for item in bodies]
    data = init_practice(store)
    added = []
    for fields in validated:
        quiz = dict(fields, id=secrets.token_hex(8), taskId=None, published=False, reviewedAt=None,
                    revision=1, createdAt=store.stamp(), createdBy=actor, history=[])
        _history(store, quiz, actor, "quiz_drafted", {"sourceNote": quiz["sourceNote"]})
        data["quizzes"].append(quiz)
        added.append(quiz["id"])
    return {"ok": True, "quizIds": added, "practice": practice_state(store, actor)}


def practice_mutate(store, path, body, actor):
    from mastery import mutate as mastery_mutate
    mastery_result=mastery_mutate(store,path,body,actor)
    if mastery_result is not None:return mastery_result
    data = init_practice(store)
    if not isinstance(body, dict):
        raise PracticeError("Please send a valid practice update.")
    if path.startswith("/api/parent/practice/"):
        expected = "parent"
    elif path.startswith("/api/assistant/practice/"):
        expected = "assistant"
    elif path.startswith("/api/practice/"):
        expected = "river"
    else:
        raise PracticeError("That practice action was not found.", 404)
    if actor != expected:
        raise PracticeError("This practice action needs the appropriate parent, assistant, or Student access.", 403)

    if path in ("/api/parent/practice/quizzes", "/api/parent/practice/import", "/api/assistant/practice/drafts"):
        return _add_drafts(store, body, actor)
    draft_edit=re.fullmatch(r'/api/assistant/practice/drafts/([^/]+)',path)
    if draft_edit:
        quiz=_quiz(store,draft_edit[1])
        if (quiz['createdBy']!='assistant' or quiz['published'] or quiz.get('taskId')
                or any(a['quizId']==quiz['id'] for a in data['attempts'])):
            raise PracticeError('Only an assistant-created draft that has never been published or attempted can be updated here.',403)
        if type(body.get('expectedRevision')) is not int or body['expectedRevision']!=quiz['revision']:
            raise PracticeError('This draft changed. Read its current revision before updating it.',409)
        fields=_fields(body,store.today())
        before={k:copy.deepcopy(quiz.get(k)) for k in fields}
        quiz.update(fields,revision=quiz['revision']+1,reviewedAt=None)
        _history(store,quiz,'assistant','quiz_draft_updated',{'before':before,'after':fields})
        return {'ok':True,'quizId':quiz['id'],'revision':quiz['revision'],'published':False}
    if path == "/api/parent/practice/writing":
        from writing_coach import update
        return update(store, body)
    if path == "/api/parent/practice/settings":
        settings = {"sessionQuestions": _int(body.get("sessionQuestions", data["settings"]["sessionQuestions"]), "questions per session", 1, 10),
                    "sessionMinutes": _int(body.get("sessionMinutes", data["settings"]["sessionMinutes"]), "minutes per session", 1, 30),
                    "tutoringSupport": _text(body.get("tutoringSupport", data["settings"]["tutoringSupport"]), "tutoring support", True, 2000)}
        data["settings"] = settings
        _history(store, data, actor, "practice_settings_updated", settings)
        return {"ok": True, "settings": copy.deepcopy(settings)}
    match = re.fullmatch(r"/api/parent/practice/quizzes/([^/]+)(?:/(publish))?", path)
    if match:
        quiz = _quiz(store, match[1])
        if match[2] == "publish":
            # "reviewed" remains accepted for older clients; publication is parent approval,
            # not an attestation that the parent checked every generated answer.
            if body.get("approved") is not True and body.get("reviewed") is not True:
                raise PracticeError("Choose Publish quiz to approve adding it to Student’s plan.")
            if "expectedRevision" in body and (type(body["expectedRevision"]) is not int or body["expectedRevision"] != quiz["revision"]):
                raise PracticeError("This quiz changed. Refresh Parent review and publish the current draft.", 409)
            _publishable(quiz)
            from mastery import is_mastery
            existing_id = body.get("existingTaskId", "")
            if existing_id and existing_id != quiz.get("taskId"):
                if quiz.get("taskId"):
                    raise PracticeError("This quiz already has a task.", 409)
                task = next((t for t in store.state["tasks"] if t["id"] == existing_id), None)
                if (not task or task["kind"] != "school" or task.get("practiceQuizId")
                        or task.get("planDate") != quiz["planDate"] or task.get("subject") != quiz["subject"]
                        or any(task.get(k) for k in ("reportedAt", "completedAt", "parentConfirmed", "understanding", "submission", "reviewHold"))):
                    raise PracticeError("Choose an unstarted schoolwork task for the same subject and day.", 409)
                before = copy.deepcopy(task)
                quiz["taskId"] = task["id"]
                task.update(practiceQuizId=quiz["id"], scope="practice", assignmentId="", dueDate="",
                    instructions="Extra practice in this app. Open the practice, read the included passage, and type your answer. Save time for feedback and one focused revision; stop at the planned time limit.",
                    criteria="Save your own response for assistant review. After the required response is approved, report ready for parent review. This practice does not submit or change a school assignment.",
                    requirements={"understanding": False, "submission": False, "parent": is_mastery(quiz)})
                _history(store, task, actor, "practice_attached", {"quizId": quiz["id"], "before": before})
            quiz["published"], quiz["reviewedAt"] = True, store.stamp()
            if not quiz["taskId"]:
                created = store.create_task({"subject": quiz["subject"], "title": quiz["title"],
                    "instructions": "Work through this whole quiz one question at a time. Save a break when needed and return to the same quiz.",
                    "planDate": quiz["planDate"], "dueDate": "", "sourceUrl": quiz["sourceUrl"], "sourceNote": quiz["sourceNote"],
                    "scope": "practice", "plannedMinutes": data["settings"]["sessionMinutes"],
                    "criteria": "Every required response reviewed and all concept practice targets met. A finished session does not finish this quiz.",
                    "requirements": {"understanding": False, "submission": False, "parent": is_mastery(quiz)}})
                quiz["taskId"] = created["id"]
                store.task(created["id"])["practiceQuizId"] = quiz["id"]
            else:
                task = store.task(quiz["taskId"])
                before = {k: task.get(k) for k in ("title", "subject", "planDate", "sourceUrl", "sourceNote")}
                task.update({k: quiz[k] for k in before})
                if before["planDate"] != task["planDate"]:
                    # This project's existing rewards hook updates the day plan.
                    from rewards import task_planned
                    task_planned(store, task, before["planDate"])
                if any(before[k] != task[k] for k in before):
                    _history(store, task, actor, "quiz_plan_updated", {"before": before, "after": {k: task[k] for k in before}})
            _history(store, quiz, actor, "quiz_published", {"revision": quiz["revision"], "taskId": quiz["taskId"]})
        else:
            fields = _fields(body, store.today())
            before = {k: copy.deepcopy(quiz.get(k, False if k == "sequential" else None)) for k in fields}
            quiz.update(fields)
            quiz.update(published=False, reviewedAt=None, revision=quiz["revision"] + 1)
            from mastery import is_mastery, invalidate
            if is_mastery(quiz):invalidate(store,quiz)
            for session in data["sessions"]:
                if session["quizId"] == quiz["id"] and not session["endedAt"]:
                    _stop_session(store, session, end=True, reason="Parent updated the quiz; earlier responses remain saved.")
            _history(store, quiz, actor, "quiz_edited", {"before": before, "after": fields})
        _sync_task(store, quiz)
        return {"ok": True, "quizId": quiz["id"], "taskId": quiz["taskId"], "practice": practice_state(store, actor)}

    match = re.fullmatch(r"/api/(parent|assistant)/practice/attempts/([^/]+)/(review|revoke)", path)
    if match:
        attempt = _find(data["attempts"], match[2], "response")
        quiz = _quiz(store, attempt["quizId"])
        from mastery import is_mastery, review
        if is_mastery(quiz):return review(store,quiz,attempt,body,actor,match[3])
        previous = {k: copy.deepcopy(attempt[k]) for k in ("grade", "independent", "feedback", "reviewedAt", "reviewedBy")}
        if match[3] == "revoke":
            note = _text(body.get("note", ""), "correction note", True)
            attempt.update(grade="withdrawn", independent=False, reviewedAt=store.stamp(), reviewedBy=actor,
                           feedback="This response's credit was withdrawn: " + note)
            _history(store, attempt, actor, "review_withdrawn", {"note": note, "previous": previous})
        else:
            if attempt["reviewContext"]["type"] != "rubric":
                raise PracticeError("Exact and choice responses use the reviewed key. Correct the key in the parent quiz editor if needed.")
            correct = _bool(body.get("correct"), "correct response")
            independent = _bool(body.get("independent"), "independent response")
            evidence = _text(body.get("rubricEvidence", ""), "response-to-rubric evidence", True)
            help_note = _text(body.get("help", ""), "support note", limit=4000)
            attempt.update(grade="correct" if correct else "incorrect", independent=bool(correct and independent and not attempt["supported"] and not help_note),
                           reviewedAt=store.stamp(), reviewedBy=actor, feedback=evidence, helpNote=help_note)
            _history(store, attempt, actor, "rubric_reviewed", {"previous": previous, "rubricEvidence": evidence, "help": help_note, "independent": attempt["independent"]})
        _sync_task(store, quiz)
        return {"ok": True, "attempt": _attempt_view(attempt, actor), "progress": _progress(store, quiz)[0]}

    if path == "/api/practice/sessions/start":
        quiz = _quiz(store, body.get("quizId"))
        if not quiz["published"]:
            raise PracticeError("This quiz needs parent publication before starting.", 409)
        from mastery import is_mastery
        if is_mastery(quiz):raise PracticeError("Open this quiz’s short-answer review instead of a practice session.",409)
        active = next((s for s in data["sessions"] if not s["endedAt"]), None)
        if active:
            if active["quizId"] != quiz["id"]:
                raise PracticeError("End the current saved session before beginning another quiz.", 409)
            return {"ok": True, "session": _session_view(store, active)}
        _require_time(store, start=True)
        session = {"id": secrets.token_hex(8), "quizId": quiz["id"], "date": store.today(), "startedAt": store.stamp(),
                   "resumedAt": store.stamp(), "pausedAt": None, "endedAt": None, "elapsedSeconds": 0,
                   "questionCount": 0, "currentQuestionId": None, "currentAttemptId": None, "history": []}
        data["sessions"].append(session)
        _select_question(store, quiz, session)
        _history(store, session, actor, "session_started", {"quizId": quiz["id"]})
        return {"ok": True, "session": _session_view(store, session)}

    match = re.fullmatch(r"/api/practice/sessions/([^/]+)/(pause|resume|end|hint|answer|next)", path)
    if match:
        action = match[2]
        session, quiz = _ensure_session(store, match[1], work=action in ("hint", "answer", "next"))
        if action in ("pause", "end"):
            _stop_session(store, session, end=action == "end")
        elif action == "resume":
            _require_time(store, start=True)
            if not quiz["published"] or session["date"] != store.today():
                raise PracticeError("End this saved session and begin a session for today once the parent has reviewed the quiz.", 409)
            if _elapsed(store, session) >= data["settings"]["sessionMinutes"] * 60 or session["questionCount"] >= data["settings"]["sessionQuestions"]:
                raise PracticeError("This session reached its target. End it and take a break before another session.", 409)
            if session["pausedAt"]:
                session.update(resumedAt=store.stamp(), pausedAt=None)
                _history(store, session, actor, "session_resumed", {})
        elif action == "next":
            if session["currentQuestionId"] and not session["currentAttemptId"]:
                raise PracticeError("Save a response, ask for help, or pause before continuing.", 409)
            _select_question(store, quiz, session)
        else:
            if not session["currentQuestionId"] or body.get("questionId") != session["currentQuestionId"] or session["currentAttemptId"]:
                raise PracticeError("Use the current question, or continue after its saved feedback.", 409)
            question = _question(quiz, session["currentQuestionId"])
            if action == "hint":
                data["hints"].append({"quizId": quiz["id"], "questionId": question["id"], "questionVersion": question["questionVersion"],
                                      "conceptId": question["conceptId"], "prompt": question["prompt"],
                                      "sessionId": session["id"], "at": store.stamp()})
                hint = question["hint"] or "Start with what the question is asking. You can ask your assistant for a hint or save a break."
                _history(store, session, actor, "hint_requested", {"questionId": question["id"]})
                return {"ok": True, "hint": hint, "session": _session_view(store, session)}
            answer = _text(body.get("answer", ""), "answer", True)
            used_help = _bool(body.get("usedHelp", False), "whether help was used")
            # A key/rubric edit must not turn a previously exposed prompt into a
            # fresh independent example. A genuinely changed prompt may be fresh.
            prior = [a for a in data["attempts"] if a["quizId"] == quiz["id"] and a["conceptId"] == question["conceptId"]
                     and _norm(a["reviewContext"]["prompt"]) == _norm(question["prompt"])]
            hinted = any(h["quizId"] == quiz["id"] and (
                h["questionId"] == question["id"] and h["questionVersion"] == question["questionVersion"] or
                h.get("conceptId") == question["conceptId"] and _norm(h.get("prompt", "")) == _norm(question["prompt"]))
                for h in data["hints"])
            supported = bool(used_help or hinted or prior)
            correct = question["type"] != "rubric" and _norm(answer) in {_norm(x) for x in question["acceptedAnswers"]}
            pending = question["type"] == "rubric"
            feedback = "Your response is saved for a brief review with your assistant." if pending else (
                ("This matches the reviewed answer key. " if correct else "Let's revisit this idea. ") +
                "Reviewed answer: " + " / ".join(question["acceptedAnswers"]) + ". " + question["explanation"])
            concept = _find(quiz["concepts"], question["conceptId"], "concept")
            attempt = {"id": secrets.token_hex(8), "quizId": quiz["id"], "sessionId": session["id"], "questionId": question["id"],
                       "questionVersion": question["questionVersion"], "conceptId": question["conceptId"], "at": store.stamp(), "answer": answer,
                       "supported": supported, "usedHelpReported": used_help, "independent": bool(correct and not supported),
                       "grade": "pending" if pending else "correct" if correct else "incorrect", "feedback": feedback,
                       "reviewedAt": None if pending else store.stamp(), "reviewedBy": None if pending else "reviewed_key", "helpNote": "",
                       "reviewContext": {"prompt": question["prompt"], "type": question["type"], "goal": concept["goal"],
                                         "rubric": question["rubric"], "acceptedAnswers": question["acceptedAnswers"]}, "history": []}
            _history(store, attempt, actor, "response_saved", {"grade": attempt["grade"], "supported": supported})
            data["attempts"].append(attempt)
            session["currentAttemptId"] = attempt["id"]
            session["questionCount"] += 1
            _sync_task(store, quiz)
            return {"ok": True, "attempt": _attempt_view(attempt, actor), "session": _session_view(store, session), "progress": _progress(store, quiz)[0]}
        return {"ok": True, "session": _session_view(store, session), "progress": _progress(store, quiz)[0]}
    raise PracticeError("That practice action was not found.", 404)
