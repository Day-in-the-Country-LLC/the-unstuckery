"""Assistant-reviewed short answers, targeted retries, and final parent checkoff."""
import copy
import re
import secrets


def is_mastery(quiz):
    return quiz.get('reviewMode') == 'mastery'


def latest_attempts(store, quiz):
    from practice import _valid_attempts
    latest = {}
    for attempt in _valid_attempts(store, quiz):
        latest[attempt['questionId']] = attempt
    if quiz.get('sequential'):
        approved_ids=[]
        for q in quiz['questions']:
            a=latest.get(q['id'])
            if a and a.get('sequenceContext',[]) != approved_ids:
                latest.pop(q['id'],None)
                a=None
            if not a or a['grade']!='correct' or a.get('reviewedBy')!='assistant':
                break
            approved_ids.append(a['id'])
    return latest


def preceding_steps(store,quiz,question):
    latest=latest_attempts(store,quiz)
    result=[]
    for q in quiz['questions']:
        if q['id']==question['id']:break
        a=latest.get(q['id'])
        if not a or a['grade']!='correct' or a.get('reviewedBy')!='assistant':return None
        result.append({'questionId':q['id'],'prompt':q['prompt'],'answer':a['answer'],'attemptId':a['id'],'feedback':a.get('feedback','')})
    return result


def pending_revisits(store, quiz, question):
    from practice import init_practice
    return sorted((item for item in init_practice(store).get('revisits', [])
                   if item['quizId'] == quiz['id'] and item['questionId'] == question['id']
                   and item['questionVersion'] == question['questionVersion'] and not item.get('completedAt')),
                  key=lambda item: (item['dueDate'], item['scheduledAt']))


def revisit_view(store, quiz, question):
    items = pending_revisits(store, quiz, question)
    if not items:
        return None
    item = items[0]
    return {'id': item['id'], 'dueDate': item['dueDate'], 'scheduledAt': item['scheduledAt'],
            'status': 'due' if item['dueDate'] <= store.today() else 'scheduled'}


def status(store, quiz):
    from practice import init_practice, _valid_attempts
    data=init_practice(store)
    attempts=_valid_attempts(store,quiz)
    latest=latest_attempts(store,quiz)
    rows=[]
    for q in quiz['questions']:
        a=latest.get(q['id'])
        history=[x for x in attempts if x['questionId']==q['id']]
        hints=[h for h in data['hints'] if h['quizId']==quiz['id'] and h['questionId']==q['id']]
        approved=bool(a and a['grade']=='correct' and a.get('reviewedBy')=='assistant')
        revisit=revisit_view(store,quiz,q)
        if a and a['grade']=='pending':row_status='pending'
        elif a and a['grade']!='correct':row_status='retry'
        elif not approved:row_status='unanswered'
        elif revisit:row_status='revisit' if revisit['status']=='due' else 'scheduled_review'
        else:row_status='approved'
        previous=preceding_steps(store,quiz,q) if quiz.get('sequential') else []
        if previous is None:row_status='locked'
        elif quiz.get('sequential') and a and a.get('sequenceContext',[]) != [x['attemptId'] for x in previous]:row_status='unanswered'
        rows.append({'previousAnswer':previous[-1]['answer'] if previous else '', 'questionId':q['id'], 'prompt':q['prompt'], 'required':q['required'],
                     'writingRequirements':copy.deepcopy(q.get('writingRequirements')),
                     'status':row_status, 'revisit':copy.deepcopy(revisit),
                     'effort':__import__('effort_review').view(store,quiz,q) if q.get('effortPolicy') else None,
                     'attempts':len(history), 'hints':sum(h.get('source')!='tip' for h in hints),
                     'tips':sum(h.get('source')=='tip' for h in hints),
                     'helpedAttempts':sum(bool(x.get('supported') or x.get('helpNote')) for x in history),
                     'responses':[{'id':x['id'],'answer':x['answer'],'at':x['at'],'grade':x['grade'],
                                   'feedback':x.get('feedback',''),'hint':x.get('retryHint',''),'writingReview':copy.deepcopy(x.get('writingReview'))} for x in history],
                     'hintHistory':[{'at':h['at'],'text':h.get('text',''),'source':h.get('source','hint')} for h in hints]})
    all_approved=quiz['published'] and all(r['status']=='approved' for r in rows if r['required'])
    task=store.task(quiz['taskId']) if quiz.get('taskId') else {}
    return {'questions':rows,'allApproved':all_approved,'reportedAt':task.get('reportedAt') if task.get('reportedBy')=='river' else None,'parentReturned':copy.deepcopy(task.get('parentReturned')),
            'parentConfirmed':copy.deepcopy(quiz.get('parentConfirmed')),
            'totalAttempts':sum(r['attempts'] for r in rows),
            'totalHints':sum(r['hints'] for r in rows),'totalTips':sum(r['tips'] for r in rows),
            'totalHelpedAttempts':sum(r['helpedAttempts'] for r in rows)}


def progress(store,quiz):
    stats=status(store,quiz)
    required=[r for r in stats['questions'] if r['required']]
    approved=sum(r['status']=='approved' for r in required)
    reasons=[]
    if not quiz['published']:reasons.append('The parent needs to review and publish this quiz.')
    scheduled=sum(r['status']=='scheduled_review' for r in required)
    due=sum(r['status']=='revisit' for r in required)
    ordinary=len(required)-approved-scheduled-due
    if ordinary>0:reasons.append(f'{ordinary} question(s) still need assistant approval.')
    if scheduled:reasons.append(f'{scheduled} question(s) will return on a later day because tutor help was used.')
    if due:reasons.append(f'{due} later review question(s) are ready now.')
    if not quiz.get('parentConfirmed'):reasons.append('Final parent confirmation is still needed.')
    return {'requiredDone':approved,'requiredTotal':len(required),
            'pendingReviews':sum(r['status']=='pending' for r in required),
            'criteriaMet':not reasons,'allApproved':stats['allApproved'],'reasons':reasons}, copy.deepcopy(quiz['concepts'])


def invalidate(store,quiz):
    quiz['parentConfirmed']=None
    if quiz.get('taskId'):
        task=store.task(quiz['taskId'])
        task.update(parentConfirmed=None,completedAt=None,understanding=None,reportedAt=None,reportedBy=None)


def hint(store,quiz,question,source='hint',provided=''):
    from practice import init_practice
    data=init_practice(store)
    previous=sum(h['quizId']==quiz['id'] and h['questionId']==question['id'] and h.get('source')!='tip' for h in data['hints'])
    steps=question.get('hintSteps') or [question.get('hint') or 'Return to the scene in your reading and find the detail the question asks about.']
    value=provided or steps[min(previous,len(steps)-1)]
    data['hints'].append({'quizId':quiz['id'],'questionId':question['id'],'questionVersion':question['questionVersion'],
                          'conceptId':question['conceptId'],'prompt':question['prompt'],'at':store.stamp(),
                          'source':source,'text':value,'level':min(previous+1,len(steps))})
    return value


def review(store,quiz,attempt,body,actor,action):
    from practice import PracticeError, _text, _bool, _history, _question, init_practice
    if actor!='assistant':raise PracticeError('These answers must be reviewed by the assistant. The parent confirms after all answers pass.',403)
    if latest_attempts(store,quiz).get(attempt['questionId'],{}).get('id')!=attempt['id']:
        raise PracticeError('Review the latest response for this question; this earlier response stays in history.',409)
    before={k:copy.deepcopy(attempt.get(k)) for k in ('grade','independent','feedback','retryHint','reviewEvidence','reviewedAt','reviewedBy','helpNote','writingReview','writingMechanics')}
    question=_question(quiz,attempt['questionId'])
    if quiz.get('sequential') and action!='revoke':
        previous=preceding_steps(store,quiz,question)
        if previous is None or attempt.get('sequenceContext',[]) != [x['attemptId'] for x in previous]:
            raise PracticeError('An earlier step changed. Submit this step again after it passes.',409)
    effort=None
    if question.get('effortPolicy') and action != 'revoke':
        from effort_review import assess as assess_effort
        effort=assess_effort(store,quiz,question,attempt,body)
    writing=None
    mechanics=None
    if action=='revoke':
        feedback=_text(body.get('note',''),'correction feedback',True)
        correct=False
        evidence=feedback
    else:
        correct=_bool(body.get('correct'),'approved answer')
        evidence=_text(body.get('rubricEvidence',''),'private review evidence',True)
        if question.get('writingRequirements'):
            from writing_review import assess, feedback as writing_feedback
            writing=assess(question,attempt,body,correct)
            feedback=writing_feedback(writing)
            from writing_coach import assess as assess_mechanics
            mechanics=assess_mechanics(quiz,body,correct)
            if mechanics and mechanics['coachingTip']:
                feedback+='\nWriting tip: '+mechanics['coachingTip']
        else:
            feedback=_text(body.get('feedback',''),'feedback for Student',not correct,4000)
    if not correct:
        retry_hint=hint(store,quiz,_question(quiz,attempt['questionId']),'review',_text(body.get('hint',''),'reading hint',limit=4000))
    else:retry_hint=''
    help_note=_text(body.get('help',''),'help/tip',limit=4000)
    if help_note:hint(store,quiz,_question(quiz,attempt['questionId']),'tip',help_note)
    independent=body.get('independent',False)
    if type(independent) is not bool:raise PracticeError('Choose whether this answer was independently explained.')
    attempt.update(grade='correct' if correct else 'incorrect', reviewedAt=store.stamp(),reviewedBy='assistant',
                   feedback=feedback or 'Approved by your assistant.',retryHint=retry_hint,reviewEvidence=evidence,
                   independent=bool(correct and independent and not attempt['supported'] and not help_note),helpNote=help_note)
    if correct and attempt.get('revisitId'):
        revisit=next((item for item in init_practice(store).get('revisits', []) if item['id']==attempt['revisitId']),None)
        if revisit and not revisit.get('completedAt'):
            revisit.update(completedAt=store.stamp(),completedByAttemptId=attempt['id'])
    if effort is not None:
        attempt['effortReview']=effort
        from effort_review import view as effort_view
        if effort_view(store,quiz,question,effort['date'])['stopForTonight']:
            from effort_review import STOP
            attempt['feedback'] += '\n\n' + STOP
            attempt['retryHint']=''
    if writing is not None:attempt['writingReview']=writing
    if mechanics is not None:attempt['writingMechanics']=mechanics
    _history(store,attempt,actor,'mastery_reviewed',{'previous':before,'evidence':evidence,'feedback':feedback,'hint':retry_hint,'help':help_note,'writingReview':writing,'writingMechanics':mechanics})
    invalidate(store,quiz)
    from writing_coach import advance
    advance(store)
    return {'ok':True,'attempt':copy.deepcopy(attempt),'mastery':status(store,quiz)}


def same_answer(first, second):
    # Spacing alone is not a revision; capitals and punctuation may be the skill being practiced.
    return ' '.join(first.split()) == ' '.join(second.split())


def duplicate_answer(store, quiz):
    return {'ok': True, 'duplicate': True,
            'message': 'This is the same answer as your previous submission. Read the feedback or ask the tutor, then revise your answer before submitting again.',
            'mastery': status(store, quiz)}


def mutate(store,path,body,actor):
    match=re.fullmatch(r'/api/(parent/)?practice/mastery/([^/]+)/(answer|hint|tutor|confirm)',path)
    if not match:return None
    from practice import PracticeError, _quiz, _question, _text, _bool, _history, init_practice, _require_time
    parent,ident,action=match.groups()
    if (action=='confirm' and (not parent or actor!='parent')) or (action!='confirm' and (parent or actor!='river')):
        raise PracticeError('Use the appropriate Student or parent control.',403)
    quiz=_quiz(store,ident)
    if not is_mastery(quiz):raise PracticeError('Use this quiz’s normal practice controls.')
    if not quiz['published']:raise PracticeError('This quiz needs parent publication before starting.',409)
    if action=='confirm':return confirm_parent(store,quiz)
    question=_question(quiz,body.get('questionId'))
    previous=preceding_steps(store,quiz,question) if quiz.get('sequential') else []
    if previous is None:raise PracticeError('Pass the current step before opening the next one.',409)
    if action=='tutor':
        from tutoring import ask
        return ask(store,quiz,question,body.get('message',''))
    latest=latest_attempts(store,quiz).get(question['id'])
    revisit=next((item for item in pending_revisits(store,quiz,question) if item['dueDate']<=store.today()),None)
    if action == 'answer' and question.get('effortPolicy'):
        from effort_review import attempts as effort_attempts, day as effort_day
        answer=_text(body.get('answer',''),'answer',True)
        duplicate=next((a for a in reversed(effort_attempts(store,quiz,question)) if effort_day(a)==store.today() and same_answer(a['answer'],answer)),None)
        if duplicate:
            return duplicate_answer(store,quiz)
    if latest and latest['grade']=='pending':
        raise PracticeError('This question is approved or awaiting review. Only a question needing another try can reopen.',409)
    if latest and latest['grade']=='correct' and not revisit and not question.get('effortPolicy'):
        future=pending_revisits(store,quiz,question)
        if future:
            raise PracticeError('This question will return on a later day because tutor help was used.',409)
        raise PracticeError('This question is approved. Only a question needing another try can reopen.',409)
    _require_time(store,start=True)
    if action=='hint':
        return {'ok':True,'hint':hint(store,quiz,question),'mastery':status(store,quiz)}
    answer=_text(body.get('answer',''),'answer',True)
    # A scheduled later learning check is a new attempt context, even if its answer matches.
    if latest and (not revisit or latest.get('revisitId') == revisit['id']) and same_answer(latest['answer'],answer):
        return duplicate_answer(store,quiz)
    used=_bool(body.get('usedHelp',False),'whether help was used')
    from writing_coach import policy
    policy(store,quiz,freeze=True)
    hints=[h for h in init_practice(store)['hints'] if h['quizId']==ident and h['questionId']==question['id']
           and (not revisit or h.get('at','')[:10]==store.today())]
    attempt={'id':secrets.token_hex(8),'quizId':ident,'questionId':question['id'],'conceptId':question['conceptId'],
             'questionVersion':question['questionVersion'],'sessionId':'mastery-'+store.today(),'at':store.stamp(),'answer':answer,
             'mastery':True,'supported':bool(used or hints),'usedHelpReported':used,'independent':False,
             'grade':'pending','feedback':'Saved. Ask your Canvas-planning Codex task to review your answers.',
             'reviewedAt':None,'reviewedBy':None,'helpNote':'','history':[],
             'reviewContext':{'prompt':question['prompt'],'type':'rubric','rubric':question['rubric'],'acceptedAnswers':[]}}
    if quiz.get('sequential'):attempt['sequenceContext']=[x['attemptId'] for x in previous]
    if revisit:attempt['revisitId']=revisit['id']
    init_practice(store)['attempts'].append(attempt)
    invalidate(store,quiz)
    _history(store,attempt,'river','response_saved',{'supported':attempt['supported']})
    from ai_review import enqueue
    enqueue(store,quiz,attempt)
    return {'ok':True,'mastery':status(store,quiz)}


def confirm_parent(store,quiz):
    from practice import PracticeError,_history
    stats=status(store,quiz)
    if not stats['allApproved']:raise PracticeError('Every required answer must be assistant-approved before parent confirmation.',409)
    task=store.task(quiz['taskId'])
    if not task.get('reportedAt') or task.get('reportedBy')!='river':
        raise PracticeError('Student must press Ready for parent review after the answers are approved.',409)
    if quiz.get('parentConfirmed') and task.get('parentConfirmed'):return {'ok':True,'mastery':stats}
    confirmation={'at':store.stamp(),'actor':'parent','note':''}
    quiz['parentConfirmed']=copy.deepcopy(confirmation)
    task['parentConfirmed']=copy.deepcopy(confirmation)
    from parent_review import complete
    task['reviewHold']=''
    complete(store,task)
    if store.view_task(task)['needsReview']:raise PracticeError('The quiz still has required checks to finish.',409)
    _history(store,quiz,'parent','mastery_confirmed',{'totalAttempts':stats['totalAttempts'],'totalHints':stats['totalHints'],'totalTips':stats['totalTips']})
    _history(store,task,'parent','parent_confirmed',confirmation)
    return {'ok':True,'mastery':status(store,quiz)}
