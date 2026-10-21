"""Recorded assistant assessment of writing; never automatic semantic grading."""


def requirements(value):
    from practice import PracticeError, _int, _text
    if not isinstance(value,dict):raise PracticeError('Use an object for writing requirements.')
    minimum=_int(value.get('minQualifiedSentences'),'minimum qualifying sentences',1,50)
    coverage=value.get('requiredCoverage',[])
    if not isinstance(coverage,list) or not 1<=len(coverage)<=10:raise PracticeError('List the required writing topics.')
    coverage=[_text(x,'required topic',True,100) for x in coverage]
    if len(set(coverage))!=len(coverage):raise PracticeError('Writing topics must be unique.')
    return {'minQualifiedSentences':minimum,'expectedSentenceRange':_text(value.get('expectedSentenceRange',''),'teacher sentence expectation',True,100),'requiredCoverage':coverage}


def assess(question,attempt,body,approved):
    from practice import PracticeError, _text, _bool
    rules=question['writingRequirements']
    assessment=body.get('writingReview')
    if not isinstance(assessment,dict):raise PracticeError('Assess each sentence and the required writing coverage before reviewing this answer.')
    raw=assessment.get('sentences')
    if not isinstance(raw,list) or not 1<=len(raw)<=200:raise PracticeError('Review the response sentence by sentence, including fragments.')
    sentences=[]
    for index,row in enumerate(raw,1):
        if not isinstance(row,dict) or type(row.get('number')) is not int or row['number']!=index:
            raise PracticeError('Number the reviewed sentences consecutively from 1.')
        text=_text(row.get('text',''),'exact sentence text',True)
        flags={key:_bool(row.get(key),key) for key in ('complete','relevant','accurate','addsNewIdea')}
        counts=all(flags.values())
        reason=_text(row.get('reason',''),'reason a sentence does not count',not counts,2000)
        sentences.append(dict(number=index,text=text,**flags,counts=counts,reason=reason))
    # Reviewer supplies linguistic boundaries; require a faithful, complete accounting of the answer.
    normalize=lambda value:' '.join(value.split())
    if normalize(' '.join(row['text'] for row in sentences))!=normalize(attempt['answer']):
        raise PracticeError('Sentence text must account for the complete saved answer in order, without adding, omitting, or duplicating text.')
    coverage=assessment.get('coverage')
    if not isinstance(coverage,dict):raise PracticeError('Assess coverage of each required character or topic.')
    coverage={name:_bool(coverage.get(name),name+' coverage') for name in rules['requiredCoverage']}
    evidence=_bool(assessment.get('evidenceExplained'),'relevant textual evidence and explanation')
    count=sum(row['counts'] for row in sentences)
    if approved and (count<rules['minQualifiedSentences'] or not all(coverage.values()) or not evidence):
        raise PracticeError(f"This answer needs at least {rules['minQualifiedSentences']} qualifying sentences, coverage of every required topic, and explained textual evidence before approval.",409)
    goals=body.get('revisionGoals',[])
    if not isinstance(goals,list) or len(goals)>2 or (not approved and not goals):
        raise PracticeError('Give one or two concrete revision goals for this writing retry.')
    goals=[_text(goal,'revision goal',True,2000) for goal in goals]
    return {'sentences':sentences,'qualifyingSentences':count,'minimum':rules['minQualifiedSentences'],
            'coverage':coverage,'evidenceExplained':evidence,'revisionGoals':goals}


def feedback(review):
    lines=[f"{review['qualifyingSentences']} of {review['minimum']} required qualifying sentences."]
    lines += [f"Sentence {row['number']} does not count: {row['reason']}" for row in review['sentences'] if not row['counts']]
    lines += [f"Revision goal {i}: {goal}" for i,goal in enumerate(review['revisionGoals'],1)]
    return '\n'.join(lines)
