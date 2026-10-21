'use strict';
(() => {
  const getApp = () => window.RiverApp;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = (...args) => getApp().api(...args);
  const openParentQuizzes=new Set();
  document.addEventListener('toggle',event=>{if(event.target.matches?.('.practice-review[data-quiz]')){const id=event.target.dataset.quiz;if(event.target.open)openParentQuizzes.add(id);else openParentQuizzes.delete(id);}},true);
  let aiReview = {enabled:false,jobs:{}}, reviewPollBusy=false;
  let practice = null, modalQuiz = null, pageQuiz = null, parentMode = false, editor = null, loading = false, mountId = 0;
  const modal = document.createElement('dialog');
  modal.id='practice-dialog'; modal.className='practice-dialog'; modal.setAttribute('aria-label','Quiz and practice');
  document.body.append(modal);
  const page = document.createElement('main');
  page.id='practice-page'; page.className='practice-page'; page.hidden=true; page.tabIndex=-1;
  document.body.append(page);
  const act=(name,label,id='',kind='secondary')=>`<button type="button" class="button ${kind}" data-practice="${esc(name)}" data-id="${esc(id)}">${esc(label)}</button>`;
  const stamp=value=>value?new Date(value).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';
  const dayLabel=value=>value?new Date(`${value}T12:00:00`).toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric'}):'';
  const conceptCards=q=>q.reviewMode==='mastery'?masterySummary(q,true):`<div class="practice-concepts">${q.concepts.map(c=>`<div class="soft-panel"><strong>${esc(c.label)}</strong><p>${c.correctCount||0} / ${c.targetCorrect} varied independent answers · ${c.sessionCount||0} / ${c.targetSessions} sessions</p><span class="badge ${c.targetMet?'':'waiting'}">${c.targetMet?'Practice target met':'More practice or review'}</span></div>`).join('')}</div>`;
  function draftKey(form){return 'river-answer-'+form.elements.sessionId.value+'-'+form.elements.questionId.value;}
  function restoreDrafts(root){for(const form of root.querySelectorAll('#practice-answer,.mastery-answer')){try{form.elements.answer.value=(window.RiverMemory || localStorage).getItem(draftKey(form))??form.elements.answer.value;}catch{}}}
  function open(html){modal.innerHTML=html;if(!modal.open)modal.showModal();restoreDrafts(modal);}
  function openPracticePage(html,id,navigate=true,preserveScroll=false){
    const scroll=preserveScroll?window.scrollY:0;
    pageQuiz=id;modalQuiz=id;page.innerHTML=html;page.hidden=false;document.body.classList.add('practice-page-open');restoreDrafts(page);
    if(navigate&&location.hash!==`#practice/${id}`)history.pushState({practice:id},'',`#practice/${id}`);
    requestAnimationFrame(()=>{window.scrollTo(0,scroll);if(!preserveScroll)page.focus();});
  }
  function closePracticePage(navigate=false){
    pageQuiz=null;page.hidden=true;page.innerHTML='';document.body.classList.remove('practice-page-open');
    if(navigate&&location.hash.startsWith('#practice/'))history.pushState({},'','#today');
  }
  const head=(title,sub='')=>`<div class="dialog-heading"><div><h2>${esc(title)}</h2>${sub?`<p class="muted">${esc(sub)}</p>`:''}</div>${act('close','Close')}</div>`;
  async function load(parent=false){
    const [data,review]=await Promise.all([api(parent?'/api/parent/practice':'/api/practice'),api('/api/ai-review').catch(()=>({enabled:false,jobs:{}}))]);
    practice=data;aiReview=review;return practice;
  }
  async function mount(detail){
    if(!getApp())return;
    const host=document.getElementById(detail.view==='parent'?'practice-parent':'practice-today');
    if(!host)return;
    // The open practice page owns its refreshes. Do not consume a review update
    // here without rendering it, which would leave the review poll seeing no change.
    if(detail.view!=='parent'&&pageQuiz&&!page.hidden)return;
    const version=++mountId;
    loading=true;
    try{
      parentMode=detail.view==='parent'&&detail.parent;
      const [snapshot,review]=await Promise.all([
        api(parentMode?'/api/parent/practice':'/api/practice'),
        api('/api/ai-review').catch(()=>({enabled:false,jobs:{}}))
      ]);
      if(version!==mountId||!host.isConnected||(!parentMode&&pageQuiz&&!page.hidden))return;
      practice=snapshot;aiReview=review;
      if(!parentMode && modal.open && modalQuiz && !editor && !modal.querySelector('#practice-answer,.mastery-answer'))renderQuiz();
      if(!host.isConnected)return;
      if(parentMode)host.innerHTML=parentSummary();
      else {
        host.innerHTML='';
        for(const group of ['earlier','upcoming']) {
          const target=document.getElementById(`practice-${group}`);
          if(target)target.innerHTML='';
        }
        const routed=location.hash.match(/^#practice\/([a-zA-Z0-9_-]+)$/)?.[1];
        if(routed&&!pageQuiz){modalQuiz=routed;const q=practice.quizzes.find(item=>item.id===routed);if(q?.reviewMode==='mastery')renderMastery(q,false);}
      }
    }catch(e){host.innerHTML=`<p class="muted">${esc(e.message)}</p>`;}finally{loading=false;}
  }
  function quizDayCards(group){
    const today=practice.today;
    const groups=[['today','Extra practice · complete in this app',q=>q.planDate===today],['earlier','Earlier extra practice',q=>q.planDate<today&&!q.progress?.criteriaMet],['upcoming','Extra practice to work ahead on',q=>q.planDate>today&&q.published&&!q.progress?.criteriaMet]];
    return groups.filter(([key])=>!group||key===group).map(([,title,include])=>{
      const quizzes=practice.quizzes.filter(include).sort((a,b)=>a.planDate.localeCompare(b.planDate));
      if(!quizzes.length)return '';
      return `<section class="card quiz-day-card"><h3>${title}</h3>${quizzes.map(q=>`<div class="review-row"><div><strong>${esc(q.title)}</strong><p>Planned ${esc(q.planDate)} · ${q.progress?.requiredDone||0} of ${q.progress?.requiredTotal||0} required questions checked</p></div>${act('open',q.published?'Open practice & answer':'View practice',q.id)}</div>`).join('')}</section>`;
    }).join('');
  }
  function gradingSummary(q){
    const questions=q.questions||[], groups=[];
    const countLabel=n=>`${n} ${n===1?'question':'questions'}`;
    const short=questions.filter(item=>item.type==='rubric'&&!item.writingRequirements);
    if(short.length)groups.push(`<li><strong>Short explanations · ${countLabel(short.length)}</strong><p>Accurate, complete answers in Student’s own words, with relevant details or evidence when requested. Equivalent wording is welcome. Minor language slips are okay when meaning is clear, unless the assignment specifically tests them. ${q.reviewMode==='mastery'?'Codex checks each response and gives guidance on what needs another try.':'Explanations are checked against the prepared rubric.'}</p></li>`);
    const writing=new Map();
    for(const item of questions.filter(item=>item.writingRequirements)){
      const rules=item.writingRequirements,key=JSON.stringify([rules.minQualifiedSentences,rules.expectedSentenceRange]);
      if(!writing.has(key))writing.set(key,{rules,count:0});
      writing.get(key).count++;
    }
    for(const {rules,count} of writing.values())groups.push(`<li><strong>Extended writing · ${countLabel(count)}</strong><p>Teacher expectation: ${esc(rules.expectedSentenceRange)} sentences; at least ${esc(rules.minQualifiedSentences)} must be complete, relevant, accurate, and add useful information. All requested parts need coverage and explained evidence. Fragments, filler, repetition, off-topic ideas, and incorrect claims do not count. Codex identifies excluded sentences and gives one or two revision goals.</p></li>`);
    for(const [type,label] of [['choice','Multiple choice'],['exact','Exact answers']]){
      const count=questions.filter(item=>item.type===type).length;
      if(count)groups.push(`<li><strong>${label} · ${countLabel(count)}</strong><p>${type==='choice'?'The selected choice must match the prepared answer key.':'The response must match one of the accepted answers in the prepared key; capitalization and extra spacing are ignored.'}</p></li>`);
    }
    return `<section class="practice-grading" aria-label="Grading summary"><h4>How this quiz is graded</h4><ul>${groups.join('')}</ul>${q.writingPolicy?`<p><strong>Writing: ${esc(q.writingPolicy.label)}</strong> · ${esc(q.writingPolicy.description)} Expectations stay the same once Student starts this quiz.</p>`:''}<p class="small muted">${q.reviewMode==='mastery'?'Only answers needing another try reopen. Hints and retries are allowed and saved. Once every required answer is approved, Student reports ready. You then confirm or send it back from Schoolwork plan.':'Every required question and the saved independent-practice targets must be met. Attempts and help stay recorded.'}</p></section>`;
  }
  function writingCoachSummary(){
    const c=practice.writingCoach;if(!c)return '';
    const p=c.progress,current=c.levels[c.level];
    return `<section class="soft-panel" aria-label="Writing expectations"><div class="section-heading"><div><h3>Writing expectations</h3><p><strong>${esc(current.label)}</strong> · ${c.automatic?'Automatic increases on':'Automatic increases paused'}</p></div>${act('writing-settings','Adjust writing expectations')}</div><p>${esc(current.description)}</p><p class="small muted">${p.nextLevel?`${p.responses} of ${p.targetResponses} recent independent first-attempt writing responses meet the current level, across ${p.days} of ${p.targetDays} days. ${c.automatic?'Once both targets are met, the next quiz adds one new skill with a practice hint.':'You can turn automatic increases back on when you’re ready.'}`:'Student is at the highest writing level. Occasional harmless slips are still okay.'} Started quizzes keep their original expectations. Required content, evidence, and sentence counts still apply.</p></section>`;
  }
  function publishDestination(q){
    if(q.published||q.taskId)return '';
    const candidates=getApp().getState().tasks.filter(t=>t.kind==='school'&&!t.practiceQuizId&&t.planDate===q.planDate&&t.subject===q.subject&&!['reportedAt','completedAt','parentConfirmed','understanding','submission','reviewHold'].some(k=>t[k]));
    if(!candidates.length)return '';
    return `<label>Place this practice<select id="practice-destination-${esc(q.id)}"><option value="">Add a new task</option>${candidates.map(t=>`<option value="${esc(t.id)}">Replace planned task: ${esc(t.title)}</option>`).join('')}</select></label><p class="small muted">Replacing keeps the existing task’s planned minutes, checklist position, and history.</p>`;
  }
  function parentSummary(){
    const pending=(practice.attempts||[]).filter(a=>a.grade==='pending');
    return `<section class="section-block card"><div class="section-heading"><div><span class="eyebrow">QUIZZES</span><h2>Ready for Student</h2></div><div class="button-row">${act('new','Create a quiz')}${act('import','Import prepared quiz')}${act('settings','Practice settings')}</div></div><p>Your planner prepares the questions and grading criteria. Read the short grading summary, then publish the quiz to Student’s plan. Detailed question review is optional.</p>
    ${writingCoachSummary()}
    ${practice.quizzes.length?practice.quizzes.map(q=>`<article class="practice-review"><div class="section-heading"><div><h3>${esc(q.title)}</h3><p class="small muted">${esc(q.subject)} · Plan date: ${esc(q.planDate)} · ${q.progress?.requiredTotal||q.questions.filter(item=>item.required).length} required items</p></div><div class="button-row"><span class="badge ${q.published?'':'waiting'}">${q.published?'On Student’s plan':'Ready to publish'}</span>${!q.published?act('publish','Publish quiz',q.id,'primary'):''}</div></div>${gradingSummary(q)}${publishDestination(q)}<div class="button-row">${act('edit','View questions & answers',q.id)}</div>${!q.published?'<p class="small muted">Publishing saves this whole practice as one schoolwork task.</p>':`<p>${q.progress?.requiredDone||0} of ${q.progress?.requiredTotal||0} required items approved.</p>`}<details><summary>Source information</summary><p>${esc(q.sourceNote||'No source note supplied.')}</p></details>${q.published?`<details class="practice-review" data-quiz="${esc(q.id)}" ${openParentQuizzes.has(q.id)?'open':''}><summary>Progress & support</summary>${conceptCards(q)}<p>${esc((q.progress?.reasons||[]).join(' '))}</p>${act('attempts','Answers & support',q.id)}</details>`:''}</article>`).join(''):'<p class="empty-note">No quizzes yet. Your planner can prepare a draft from school material.</p>'}
    ${pending.length?`<h3>Answers awaiting review (${pending.length})</h3>${pending.map(a=>`<div class="review-row"><span>${esc(a.answer)} <span class="muted">${esc(stamp(a.at||a.answeredAt))}</span></span><span class="muted">Awaiting Codex review</span></div>`).join('')}`:''}</section>`;
  }
  async function showQuiz(id,navigate=true){
    parentMode=false; await load(false);modalQuiz=id;editor=null;
    const q=practice.quizzes.find(item=>item.id===id);
    if(q?.reviewMode==='mastery'){renderMastery(q,navigate);return;}
    renderQuiz();
  }
  function renderQuiz(){
    const q=practice.quizzes.find(q=>q.id===modalQuiz);
    if(!q){open(head('Quiz unavailable')+'<p>Your parent may be reviewing this quiz.</p>');return;}
    if(q.reviewMode==='mastery'){renderMastery(q);return;}
    const s=practice.activeSession;
    let body=head(q.title,'One quiz task, with your place saved across sessions.')+`<p>${q.progress.requiredDone} of ${q.progress.requiredTotal} required questions checked. ${q.progress.pendingReviews||0} awaiting review.</p>`;
    if(!q.published){open(body+'<p>Your parent is reviewing this quiz. You can choose another task in Today.</p>');return;}
    if(!s||s.quizId!==q.id||s.endedAt){
      body+=conceptCards(q)+`<p>${esc(q.progress.reasons.join(' '))}</p><p>Try up to ${practice.settings.sessionQuestions} questions or ${practice.settings.sessionMinutes} minutes, then pause. Only schoolwork counts toward today’s 90-minute budget. Breaks are separate.</p>`;
      body+=s&&!s.endedAt&&s.quizId!==q.id?`<p>A different quiz session is saved. Finish or pause it before switching.</p>${act('open','Open saved session',s.quizId)}`:q.progress.criteriaMet?'':act('start','Start a short session',q.id,'primary');
      if(q.progress.criteriaMet)body+='<p>Your practice requirements are met. Return to Today, report the whole quiz ready, and check it off when its remaining checks are satisfied. For a later review, your parent and assistant can add fresh questions to this same quiz.</p>';
      open(body);return;
    }
    const current=s.currentQuestion;
    body+=`<div class="badge-list"><span class="badge neutral">${s.questionCount||0} responses this session</span><span class="badge neutral">${Math.floor((s.elapsedSeconds||0)/60)} minutes this session</span></div>`;
    if(s.pausedAt){open(body+'<h3>Your place is saved.</h3><p>Resume this session when you are ready.</p>'+act('resume','Resume session',s.id,'primary')+act('end','End this session',s.id));return;}
    const attempts=(practice.attempts||[]).filter(a=>a.sessionId===s.id&&a.questionId===current?.id);
    const latest=attempts[attempts.length-1];
    if(current){
      const concept=q.concepts.find(c=>c.id===current.conceptId);
      body+=`<div class="practice-question"><span class="eyebrow">${esc(concept?.label||'Your next question')}</span><h3>${esc(current.prompt)}</h3></div>`;
      if(latest){
        const result=latest.grade==='pending'?'Waiting for a review':latest.grade==='correct'?'This response is correct':latest.grade==='withdrawn'?'This response needs another review':'Let’s look at this idea again';
        body+=`<div class="soft-panel"><h3>${result}</h3><p><strong>Your response:</strong> ${esc(latest.answer)}</p><p>${esc(latest.feedback||latest.explanation||'')}</p>${latest.rubricEvidence?`<p>${esc(latest.rubricEvidence)}</p>`:''}<p>${latest.supported?'Help was used. This response stays recorded as supported.':latest.independent?'Recorded as an independent response for this sampled question.':'This response is not yet independent correct evidence.'}</p></div>${s.needsBreak?`<p>${esc(s.reason)}</p>`:act('next','Next step',s.id,'primary')}`;
      }else if(s.canAnswer){
        body+=`<form id="practice-answer"><input type="hidden" name="sessionId" value="${esc(s.id)}"><input type="hidden" name="questionId" value="${esc(current.id)}">${current.type==='choice'?`<label>Your answer<select name="answer" required><option value="">Choose an answer</option>${current.options.map(v=>`<option>${esc(v)}</option>`).join('')}</select></label>`:`<label>Your answer<textarea name="answer" rows="4" required placeholder="Explain or solve it here."></textarea></label>`}<div class="button-row"><button class="button primary" type="submit">Save my answer</button>${act('hint','I need a hint',s.id)}</div><div id="practice-hint" aria-live="polite"></div></form>`;
      }else body+=`<p>${esc(s.reason||'This session is ready for a pause.')}</p>`;
    }else body+=`<h3>A good place to pause.</h3><p>${esc(s.reason||'Your responses are saved. Your assistant can help with anything needing review, or prepare another varied example with your parent.')}</p>${conceptCards(q)}`;
    body+=`<div class="dialog-footer button-row">${act('pause','Pause and save',s.id)}${act('end','End this session',s.id)}</div><p class="small muted">Ending a session keeps the whole quiz on your list until every required question and practice target is satisfied.</p>`;
    open(body);
  }
  function masterySummary(q,parent=false){
    const m=q.mastery;
    if(!m)return '';
    return `<section class="soft-panel" aria-label="Quiz completion summary"><h3>${m.parentConfirmed ? 'Quiz confirmed' : m.allApproved ? m.reportedAt ? 'Student reported ready for parent review' : 'All answers approved · Student can report ready'  : 'Assistant-reviewed answers'}</h3><p>${m.totalAttempts} total attempts · ${m.totalHints} hints · ${m.totalTips} tips${parent?` · ${m.totalHelpedAttempts} responses with recorded help`:''}</p><p class="small muted">Every required answer needs assistant approval. Help and hints are welcome. You can retry until your answers meet the requirements. This review does not submit a Canvas retake or change a grade.</p><ul>${m.questions.map(r=>`<li>${esc(r.questionId.toUpperCase())}: ${r.attempts} attempts · ${r.hints} hints · ${r.tips} tips${parent?` · ${r.helpedAttempts} responses with help`:''} · ${esc(r.status.replace('_',' '))}</li>`).join('')}</ul>${m.parentConfirmed ? `<p>Parent confirmed ${esc(stamp(m.parentConfirmed.at))}.</p>` : ''}</section>`;
  }
  function stripQuestionPreamble(value){
    const parts=String(value||'').split(/\n\s*\n/);
    if(parts.length>1&&/^Extra practice — complete in this app\./i.test(parts[0].trim()))parts.shift();
    return parts.join('\n\n').trim();
  }
  function tutorPanel(q,row){
    const chat=(practice.tutorChats||[]).find(item=>item.quizId===q.id&&item.questionId===row.questionId);
    const messages=chat?.messages||[];
    const lastRiver=[...messages].reverse().find(item=>item.role==='river');
    const waiting=lastRiver&&['queued','creating','running'].includes(lastRiver.status);
    const failed=lastRiver?.status==='failed';
    const transcript=messages.length?`<div class="tutor-transcript" aria-live="polite">${messages.map(message=>`<div class="tutor-message ${message.role==='river'?'student':'assistant'}"><span>${message.role==='river'?'Student':'Tutor'}</span><p>${esc(message.text)}</p>${message.role==='river'&&message.statusMessage&&!['completed'].includes(message.status)?`<small>${esc(message.statusMessage)}</small>`:''}</div>`).join('')}</div>`:'<p class="small muted">Ask about a word, the directions, the information, or any step that does not make sense. The tutor sees this question and remembers the conversation.</p>';
    const retry=failed&&aiReview.jobs?.[lastRiver.id]?.tries<3?act('retry-tutor','Try tutor again',lastRiver.id,'outline small'):'';
    const form=aiReview.enabled?`<form class="tutor-chat-form" data-quiz="${esc(q.id)}"><input type="hidden" name="questionId" value="${esc(row.questionId)}"><label>What do you want help understanding?<textarea name="message" rows="2" required maxlength="2000" placeholder="${q.sequential?'For example: What does this step mean?':'For example: I don’t understand what a rate calculation is.'}"></textarea></label><div class="button-row"><button class="button secondary" type="submit" ${waiting?'disabled':''}>${waiting?'Tutor is thinking…':'Ask the tutor'}</button>${retry}</div></form>`:'<p class="notice">Tutoring chat is waiting for parent setup. You can still use the saved hint.</p>';
    return `<section class="tutor-panel" aria-label="Tutoring chat for question ${esc(row.questionId)}"><div class="tutor-heading"><div><span class="eyebrow">Question help</span><h4>Ask the tutor</h4></div><span class="badge neutral">Saved conversation</span></div>${transcript}${form}<p class="small muted">The tutor can work through this question with you until it makes sense. You still write and submit your own final answer. ${q.sequential?'Tutor help is saved. Submit your writing for review to pass this step.':'If you use the tutor, this question returns on the next weekday—never again the same day.'}</p></section>`;
  }
  function currentSequentialStep(q){
    return q?.sequential?q.mastery.questions.find(row=>!['approved','scheduled_review'].includes(row.status))?.questionId:null;
  }
  function usePreviousResponse(form,answer){
    if(!form||!answer)return;
    if(form.elements.answer.value.trim()){
      getApp().toast('Your draft is kept. Clear the answer box first if you want to use your previous response.');
      return;
    }
    form.elements.answer.value=answer;
    try{(window.RiverMemory || localStorage).setItem(draftKey(form),answer);}catch{}
    form.elements.answer.focus();
  }
  function renderMastery(q,navigate=false,preserveScroll=false){
    if(!q.published){open(head(q.title)+'<p>Your parent is reviewing this quiz.</p>');return;}
    const required=q.mastery.questions.length,approved=q.mastery.questions.filter(row=>row.status==='approved').length;
    const minutes=practice.settings?.sessionMinutes||20;
    const displayTitle=q.title.replace(/\s*·\s*\d+\s+minutes?\s*$/i,'');
    const ready=q.mastery.allApproved&&!q.mastery.reportedAt&&!q.mastery.parentConfirmed?act('mastery-ready','Ready for parent review',q.taskId,'primary'):'';
    const activeIndex=q.sequential?q.mastery.questions.findIndex(row=>!['approved','scheduled_review'].includes(row.status)):-1;
    const questions=q.mastery.questions.map((row,index)=>{
      if(q.sequential && index!==activeIndex)return '';
      const prior=row.responses.at(-1),editable=['unanswered','retry','revisit'].includes(row.status)||(row.effort&&row.status==='approved'),job=prior&&aiReview.jobs?.[prior.id];
      const pendingText=aiReview.enabled?(aiReview.ready?(job?.message||'Waiting for review.'):aiReview.message):'Waiting for assistant review';
      const status=row.effort?.stopForTonight?'Saved · stop for tonight':row.status==='approved'?'Pass':row.status==='pending'?pendingText:row.status==='retry'?(q.sequential?'Not yet — try this step again':'Another try needed'):row.status==='scheduled_review'?`Returns ${dayLabel(row.revisit?.dueDate)}`:row.status==='revisit'?'Later review ready':'Ready for your answer';
      const revisitNote=row.status==='scheduled_review'?`<p class="revisit-note"><strong>Later learning check scheduled:</strong> This question will return ${esc(dayLabel(row.revisit?.dueDate))} because tutor help was used. It will not reopen again today.</p>`:row.status==='revisit'?'<p class="revisit-note"><strong>Later learning check:</strong> Try this question again from memory before opening the saved tutor conversation.</p>':'';
      const responses=row.responses.length?`<details class="response-history" ${!q.sequential&&row.status==='retry'?'open':''}><summary>Earlier responses (${row.responses.length})</summary>${row.responses.map(a=>`<div class="soft-panel"><p><strong>Your response:</strong> ${esc(a.answer)}</p><p style="white-space:pre-wrap">${esc(a.feedback)}</p>${a.hint?`<p><strong>Feedback hint:</strong> ${esc(a.hint)}</p>`:''}<p class="small muted">${esc(stamp(a.at))}</p></div>`).join('')}</details>`:'';
      const answer=editable?`<form class="mastery-answer" data-quiz="${esc(q.id)}"><input type="hidden" name="sessionId" value="${esc(q.id)}"><input type="hidden" name="questionId" value="${esc(row.questionId)}"><label>Your answer<textarea name="answer" rows="4" required placeholder="Write your answer in your own words. Show your work when the question asks.">${q.sequential?esc(prior?.answer||''):row.effort&&prior?esc(prior.answer):''}</textarea></label><p class="notice" data-answer-feedback role="status" hidden></p><div class="button-row"><button class="button primary" type="submit">${row.effort?'Review':prior?'Submit another try':aiReview.enabled?'Submit for AI review':'Submit answer'}</button>${act('mastery-hint','Give me a hint',row.questionId,'outline')}</div><p class="saved-hint" id="mastery-hint-${esc(row.questionId)}" aria-live="polite">${esc(row.effort?.stopForTonight?'':row.hintHistory.at(-1)?.text||'')}</p></form>`:'';
      const previous=q.sequential&&!prior&&row.previousAnswer?`<details class="response-history"><summary>Your passed response from step ${index}</summary><p style="white-space:pre-wrap">${esc(row.previousAnswer)}</p>${editable?act('use-previous-response','Use this as a starting point',row.questionId,'outline'):''}</details>`:'';
      return `<article class="practice-question-card" aria-labelledby="question-${esc(row.questionId)}"><div class="question-heading"><span>${q.sequential?'Step':'Question'} ${index+1}${q.sequential?` of ${required}`:''}</span><strong class="question-status ${row.status}">${esc(status)}</strong></div><h3 id="question-${esc(row.questionId)}" class="question-prompt">${esc(stripQuestionPreamble(row.prompt))}</h3>${row.effort?`<div class="notice" aria-live="polite"><strong>${row.effort.count} of 3 genuine efforts tonight</strong><p>${esc(row.effort.message)}</p><p>90 minutes is the total homework limit, not a target for this paragraph.</p></div>`:''}${revisitNote}${row.writingRequirements?`<p class="small muted">Writing target: ${esc(row.writingRequirements.expectedSentenceRange)} sentences, with at least ${row.writingRequirements.minQualifiedSentences} complete and useful sentences.</p>`:''}${row.status==='pending'&&job?.status==='failed'&&job.tries<3?act('retry-ai-review','Retry AI review',prior.id,'outline small'):''}${q.sequential&&prior?.feedback?`<div class="notice" role="status">${esc(prior.feedback)}</div>`:''}${q.sequential?'':responses}${answer}${previous}${q.sequential?responses:''}${tutorPanel(q,row)}</article>`;
    }).join('');
    const writing=!q.sequential&&q.writingPolicy?`<div class="instruction-note"><strong>Writing support: ${esc(q.writingPolicy.label)}</strong><p>${esc(q.writingPolicy.description)} ${q.writingPolicy.practiceHint?esc(q.writingPolicy.practiceHint):''}</p></div>`:'';
    const passed=q.sequential?q.mastery.questions.filter(row=>row.status==='approved').map(row=>`<details class="response-history"><summary>Pass · ${esc(row.prompt)}</summary><p>${esc(row.responses.at(-1)?.answer||'')}</p><p>${esc(row.responses.at(-1)?.feedback||'')}</p></details>`).join(''):'';
    const body=q.sequential?`<div class="practice-page-shell" aria-label="Quiz completion summary"><header class="practice-page-header"><button class="button quiet" data-practice="back">← Back to Today</button><h1>${esc(displayTitle)}</h1></header><p>Extra practice — complete in this app</p><section class="practice-progress" aria-label="Practice progress" aria-live="polite">${activeIndex>=0?`<h2>Current step: ${activeIndex+1} of ${required}</h2>`:''}<strong>${approved} of ${required} steps passed · ${required-approved} remaining</strong><progress aria-label="Steps passed" max="${required}" value="${approved}" style="width:100%;height:24px;accent-color:var(--accent,#27786b)"></progress></section><p>Submit this step for feedback. The next step opens when this one passes. You can pause and return to your saved work.</p>${questions}${q.mastery.allApproved?'<p role="status">Pass — your practice is ready for parent review. This does not submit your schoolwork.</p>':''}${ready}<details><summary>Passed steps and feedback</summary>${passed}</details><p class="small muted">Pause after 20 minutes. All homework stays within the 90-minute daily limit.</p></div>`:`<div class="practice-page-shell"><header class="practice-page-header"><button type="button" class="button quiet" data-practice="back">← Back to Today</button><div><span class="eyebrow">${esc(q.subject)} · Extra practice</span><h1>${esc(displayTitle)}</h1></div><span class="time-pill">${esc(minutes)} minutes</span></header><section class="practice-instructions" aria-labelledby="practice-instructions-heading"><div><span class="eyebrow">Start here</span><h2 id="practice-instructions-heading">How this practice works</h2><ul><li>Work for no more than ${esc(minutes)} minutes. Your place and answers are saved.</li><li>Answer each question here and submit it for AI review.</li><li>Have a real conversation with the tutor when the question, information, or steps do not make sense.</li><li>The tutor can teach and work through the problem with you. You still write your own final answer.</li><li>If an answer needs work, revise only that question. This does not submit to Canvas or change a school grade.</li></ul></div><div class="practice-progress"><strong>${approved} of ${required}</strong><span>questions approved</span><div class="progress-track"><i style="width:${required?Math.round(approved/required*100):0}%"></i></div>${ready}</div>${writing}</section><section class="practice-questions" aria-label="Practice questions"><div class="questions-heading"><span class="eyebrow">Your work</span><h2>Questions</h2></div>${questions}</section></div>`;
    openPracticePage(body,q.id,navigate,preserveScroll);
  }
  const emptyQuestion=(conceptId)=>({id:'',conceptId,prompt:'',type:'rubric',options:[],acceptedAnswers:[],rubric:'',explanation:'',hint:'',required:true,origin:'variation'});
  const editingParts=new Set();
  let editorOriginal='';
  function makeEditor(q){
    editor=q?JSON.parse(JSON.stringify(q)):{title:'',subject:'',sourceMaterial:'',sourceNote:'',sourceUrl:'',planDate:getApp().getState().date,concepts:[{id:'concept-1',label:'',goal:'',targetCorrect:3,targetSessions:2}],questions:[]};
    if(!editor.questions.length)editor.questions.push(emptyQuestion(editor.concepts[0].id));
    editorOriginal=JSON.stringify(editor);editingParts.clear();
    if(!q){editingParts.add('settings');editingParts.add('question:0');editingParts.add('answer:0');}
    modal.scrollTop=0;renderEditor();
  }
  function field(label,name,value='',type='text'){return `<label>${esc(label)}<input type="${type}" name="${name}" value="${esc(value)}" ${['title','subject'].includes(name)?'required':''}></label>`;}
  function area(label,name,value='',rows=3){return `<label>${esc(label)}<textarea name="${name}" rows="${rows}">${esc(value)}</textarea></label>`;}
  const readText=value=>`<div class="practice-readable">${esc(value||'Not supplied.')}</div>`;
  function editToggle(key,label){return act(editingParts.has(key)?'finish-edit-part':'edit-part',editingParts.has(key)?'Done editing':label,key,'outline small');}
  function questionPair(q,i){
    const questionKey=`question:${i}`,answerKey=`answer:${i}`,settingsKey=`settings:${i}`;
    return `<section class="practice-editor-item" aria-label="Question ${i+1} and benchmark answer"><div class="section-heading"><h3>Question ${i+1}</h3>${editToggle(questionKey,'Edit question')}</div>${editingParts.has(questionKey)?area(`Question ${i+1}`,`q${i}-prompt`,q.prompt)+(q.type==='choice'?area('Choices (one per line)',`q${i}-options`,q.options.join('\n')):''):readText(q.prompt)+(q.type==='choice'?`<ul>${q.options.map(option=>`<li>${esc(option)}</li>`).join('')}</ul>`:'')}<div class="section-heading benchmark-heading"><h4>${q.type==='rubric'?'Benchmark answer / criteria':'Benchmark answer'}</h4>${editToggle(answerKey,'Edit answer')}</div>${editingParts.has(answerKey)?q.type==='rubric'?area(`Benchmark answer / criteria ${i+1}`,`q${i}-rubric`,q.rubric):area(`Accepted answers ${i+1} (one per line)`,`q${i}-answers`,q.acceptedAnswers.join('\n')):readText(q.type==='rubric'?q.rubric:q.acceptedAnswers.join('\n'))}<details ${editingParts.has(settingsKey)?'open':''}><summary>Question settings</summary>${editToggle(settingsKey,'Edit question settings')}${editingParts.has(settingsKey)?`<div class="form-grid"><label>Concept<select name="q${i}-conceptId">${editor.concepts.map(c=>`<option value="${esc(c.id)}" ${c.id===q.conceptId?'selected':''}>${esc(c.label||c.id)}</option>`).join('')}</select></label><label>Answer check<select name="q${i}-type"><option value="exact" ${q.type==='exact'?'selected':''}>Exact answer</option><option value="choice" ${q.type==='choice'?'selected':''}>Multiple choice</option><option value="rubric" ${q.type==='rubric'?'selected':''}>Explanation</option></select></label><label>Question source<select name="q${i}-origin"><option value="missed" ${q.origin==='missed'?'selected':''}>Original missed material</option><option value="variation" ${q.origin==='variation'?'selected':''}>Different practice example</option></select></label></div>${area('Explanation shown after answering',`q${i}-explanation`,q.explanation)}${area('Hint (optional)',`q${i}-hint`,q.hint)}${editor.reviewMode==='mastery'?`<label>Evening effort coaching<select name="q${i}-effortPolicy"><option value="">Standard step feedback</option><option value="three-genuine-efforts" ${q.effortPolicy?'selected':''}>Pause after three genuine efforts</option></select></label>`:''}<label class="check-label"><input name="q${i}-required" type="checkbox" ${q.required?'checked':''}> Required question</label>${editor.questions.length>1?act('remove-question','Remove question',String(i),'outline small'):''}`:`<p>${q.required?'Required':'Optional'} · ${esc(editor.concepts.find(c=>c.id===q.conceptId)?.label||'')}</p>${q.explanation?'<h4>Explanation after answering</h4>'+readText(q.explanation):''}${q.hint?'<h4>Hint</h4>'+readText(q.hint):''}`}</details></section>`;
  }
  function quizSettings(){
    const editing=editingParts.has('settings');
    return `<details ${editing?'open':''}><summary>Quiz settings and source</summary>${editToggle('settings','Edit quiz settings')}${editing?`<div class="form-grid">${field('Quiz title','title',editor.title)}${field('Subject','subject',editor.subject)}${field('Plan date','planDate',editor.planDate,'date')}${field('Source or test link (optional)','sourceUrl',editor.sourceUrl,'url')}</div>${editor.reviewMode==='mastery'?`<label class="check-label"><input type="checkbox" name="sequential" ${editor.sequential?'checked':''}> One step at a time — pass before advancing</label>`:''}${area('Original missed questions / teacher feedback','sourceMaterial',editor.sourceMaterial)}${area('Source and date · what is known or uncertain','sourceNote',editor.sourceNote)}<h3>Concepts</h3>${editor.concepts.map((c,i)=>`<div class="practice-editor-item">${field('Concept name',`c${i}-label`,c.label)}${area('Learning goal',`c${i}-goal`,c.goal)}${editor.reviewMode==='mastery'?'':field('Varied independent answers',`c${i}-targetCorrect`,c.targetCorrect,'number')+field('Across at least this many sessions',`c${i}-targetSessions`,c.targetSessions,'number')}</div>`).join('')}${act('add-concept','Add concept')}`:`<p>${esc(editor.subject)} · Plan date: ${esc(editor.planDate)}</p><h4>Source note</h4>${readText(editor.sourceNote)}<h4>Source material</h4>${readText(editor.sourceMaterial)}`}</details>`;
  }
  function resizeEditorFields(){
    for(const input of modal.querySelectorAll('#practice-editor textarea')){input.style.height='auto';input.style.height=`${input.scrollHeight+2}px`;}
  }
  function renderEditor(){
    const scroll=modal.scrollTop,hasEdits=editingParts.size||JSON.stringify(editor)!==editorOriginal||!editor.id;
    open(head(editor.id?editor.title:'Create one complete quiz','Questions and benchmark answers are shown in full. Use Edit question or Edit answer to make a change.')+`<form id="practice-editor">${editor.questions.map(questionPair).join('')}<div class="button-row">${act('add-question','Add a question')}</div>${quizSettings()}${hasEdits?'<p class="small muted">Save changes to keep your edits. The quiz will be saved as a draft for publishing.</p>':''}<div class="dialog-footer">${hasEdits?'<button type="submit" class="button primary">Save changes</button>':''}${act('close',hasEdits?'Cancel':'Close')}</div></form>`);
    resizeEditorFields();modal.scrollTop=scroll;
  }
  function collectEditor(){
    const f=new FormData(modal.querySelector('#practice-editor'));
    for(const key of ['title','subject','sourceMaterial','sourceNote','sourceUrl','planDate'])if(f.has(key))editor[key]=String(f.get(key)||'');
    if(editingParts.has('settings')&&editor.reviewMode==='mastery')editor.sequential=f.has('sequential');
    editor.concepts=editor.concepts.map((c,i)=>{
      const next={...c};for(const key of ['label','goal','targetCorrect','targetSessions'])if(f.has(`c${i}-${key}`))next[key]=key.startsWith('target')?Number(f.get(`c${i}-${key}`)):String(f.get(`c${i}-${key}`)||'');return next;
    });
    editor.questions=editor.questions.map((q,i)=>{
      const next={...q};
      for(const key of ['prompt','conceptId','type','origin','rubric','explanation','hint'])if(f.has(`q${i}-${key}`))next[key]=String(f.get(`q${i}-${key}`)||'');
      for(const [key,name] of [['options','options'],['acceptedAnswers','answers']])if(f.has(`q${i}-${name}`))next[key]=String(f.get(`q${i}-${name}`)||'').split('\n').map(x=>x.trim()).filter(Boolean);
      if(editingParts.has(`settings:${i}`))next.required=f.has(`q${i}-required`);
      if(f.has(`q${i}-effortPolicy`)){if(f.get(`q${i}-effortPolicy`))next.effortPolicy=String(f.get(`q${i}-effortPolicy`));else delete next.effortPolicy;}
      return next;
    });
  }
  async function changed(){await getApp().refresh(true);}
  async function action(name,id){
    if(name==='publish'){const q=practice.quizzes.find(q=>q.id===id),existingTaskId=document.getElementById(`practice-destination-${id}`)?.value||'',body={approved:true,expectedRevision:q.revision};if(existingTaskId)body.existingTaskId=existingTaskId;await api(`/api/parent/practice/quizzes/${id}/publish`,'POST',body);await changed();getApp().toast('Quiz published. It’s now on Student’s plan.');return;}
    if(name==='retry-ai-review'){await api(`/api/practice/mastery/${modalQuiz}/review-retry`,'POST',{attemptId:id});await showQuiz(modalQuiz,false);return;}
    if(name==='retry-tutor'){await api(`/api/practice/mastery/${modalQuiz}/tutor-retry`,'POST',{attemptId:id});await showQuiz(modalQuiz,false);return;}
    if(name==='mastery-ready'){await api(`/api/tasks/${id}/report`,'POST',{});await showQuiz(modalQuiz,false);await changed();getApp().toast('Ready report saved for your parent.');return;}
    if(name==='refresh-mastery'){await showQuiz(id,false);return;}
    if(name==='mastery-hint'){const result=await api(`/api/practice/mastery/${modalQuiz}/hint`,'POST',{questionId:id});const target=(pageQuiz?page:modal).querySelector(`#mastery-hint-${id}`);if(target)target.textContent=result.hint;return;}
    if(name==='use-previous-response'){
      const row=practice.quizzes.find(q=>q.id===(pageQuiz||modalQuiz))?.mastery?.questions.find(row=>row.questionId===id);
      const form=[...(pageQuiz?page:modal).querySelectorAll('.mastery-answer')].find(form=>form.elements.questionId.value===id);
      usePreviousResponse(form,row?.previousAnswer);return;
    }
    if(name==='back'){closePracticePage(true);return;}
    if(name==='close'){modal.close();editor=null;return;}
    if(name==='open'){await showQuiz(id);return;}
    if(name==='new'){await load(true);makeEditor();return;}
    if(name==='edit'){await load(true);makeEditor(practice.quizzes.find(q=>q.id===id));return;}
    if(name==='edit-part'||name==='finish-edit-part'){collectEditor();if(name==='edit-part')editingParts.add(id);else editingParts.delete(id);renderEditor();return;}
    if(name==='add-question'){collectEditor();const i=editor.questions.length;editor.questions.push(emptyQuestion(editor.concepts[0].id));editingParts.add(`question:${i}`);editingParts.add(`answer:${i}`);renderEditor();return;}
    if(name==='add-concept'){collectEditor();editor.concepts.push({id:'concept-'+(editor.concepts.length+1),label:'',goal:'',targetCorrect:3,targetSessions:2});renderEditor();return;}
    if(name==='remove-question'){collectEditor();editor.questions.splice(Number(id),1);for(const key of [...editingParts])if(key.includes(':'))editingParts.delete(key);renderEditor();return;}
    if(name==='import'){open(head('Import a prepared quiz','Paste the structured quiz prepared with your assistant. It will be a draft until you publish it.')+`<form id="practice-import">${area('Prepared quiz JSON','json','',14)}<button type="submit" class="button primary">Import draft</button></form>`);return;}
    if(name==='writing-settings'){await load(true);const c=practice.writingCoach;open(head('Writing expectations','Start gently and build habits as Student’s writing improves.')+`<form id="writing-settings" data-revision="${c.revision}"><label>Writing level<select name="level">${Object.entries(c.levels).map(([id,level])=>`<option value="${esc(id)}" ${c.level===id?'selected':''}>${esc(level.label)}</option>`).join('')}</select></label>${Object.values(c.levels).map(level=>`<p><strong>${esc(level.label)}:</strong> ${esc(level.description)}</p>`).join('')}<label class="check-label"><input name="automatic" type="checkbox" ${c.automatic?'checked':''}> Increase automatically as his writing improves</label><p>Five consecutive independent first-attempt writing responses across at least three days must meet the current level. Then the next quiz introduces one new skill with a hint to practice it. Changes apply to quizzes he has not started. Changing this setting starts a fresh observation period.</p><button class="button primary">Save writing expectations</button></form>`);return;}
    if(name==='settings'){await load(true);open(head('Practice settings')+`<form id="practice-settings"><div class="form-grid">${field('Questions per session (1–10)','sessionQuestions',practice.settings.sessionQuestions,'number')}${field('Minutes per session (1–30)','sessionMinutes',practice.settings.sessionMinutes,'number')}</div>${area('Tutoring support level','tutoringSupport',practice.settings.tutoringSupport,4)}<p class="small muted">This is sent to the question tutor so explanations match Student’s current support needs. Keep it factual, specific, and revisable.</p><button class="button primary">Save practice settings</button></form>`);return;}
    if(name==='attempts'){await load(true);const q=practice.quizzes.find(q=>q.id===id);const rows=(practice.attempts||[]).filter(a=>a.quizId===id);open(head(q.title+' · responses')+conceptCards(q)+(rows.length?rows.slice().reverse().map(a=>`<div class="practice-editor-item"><strong>${esc(q.questions.find(x=>x.id===a.questionId)?.prompt||'Question')}</strong><p>${esc(a.answer)}</p><p>${esc(a.grade)} · ${a.supported?'supported':a.independent?'independent response':'awaiting independent evidence'} · ${esc(stamp(a.at||a.answeredAt))}</p><p>${esc(a.feedback||a.rubricEvidence||'')}</p><p>Reviewed in the Canvas-planning Codex task.</p></div>`).join(''):'<p>No responses recorded yet.</p>'));return;}
    if(name==='review'){await load(true);const a=practice.attempts.find(a=>a.id===id),q=practice.quizzes.find(q=>q.id===a.quizId),question=q.questions.find(q=>q.id===a.questionId);open(head('Review this explanation')+`<p><strong>${esc(question.prompt)}</strong></p><div class="soft-panel"><p><strong>Rubric:</strong> ${esc(question.rubric)}</p><p><strong>Student’s response:</strong> ${esc(a.answer)}</p></div><form id="practice-review" data-id="${esc(id)}"><label>Does the response satisfy the rubric?<select name="correct"><option value="false">Needs more work</option><option value="true">Yes</option></select></label><label class="check-label"><input type="checkbox" name="independent"> Independently explained, without a supplied answer</label>${area('Evidence against the rubric','rubricEvidence','',3)}${area('Help or support used','help','',2)}<button class="button primary">Save review</button></form>`);return;}
    if(name==='revoke'){open(head('Correct a review')+`<form id="practice-revoke" data-id="${esc(id)}">${area('What needs correcting?','note','',3)}<p>The earlier attempt and review stay in history.</p><button class="button primary">Save correction</button></form>`);return;}
    if(name==='start')await api('/api/practice/sessions/start','POST',{quizId:id});
    else if(name==='hint'){
      const s=practice.activeSession;const result=await api(`/api/practice/sessions/${id}/hint`,'POST',{questionId:s.currentQuestion.id});
      const target=modal.querySelector('#practice-hint');if(target)target.textContent=result.hint||'Ask your assistant for a small hint. This response will be recorded as supported.';return;
    }else if(['pause','resume','end','next'].includes(name))await api(`/api/practice/sessions/${id}/${name}`,'POST',{});
    else return;
    await load(false);renderQuiz();await changed();
  }
  document.addEventListener('click',async event=>{
    const target=event.target.closest('[data-practice],[data-practice-open]');if(!target)return;event.preventDefault();
    target.disabled=true;
    try{await action(target.dataset.practice||'open',target.dataset.id||target.dataset.practiceOpen);}catch(e){getApp().toast(e.message,true);}finally{target.disabled=false;}
  });
  function answerFeedback(form,message=''){
    const notice=form.querySelector('[data-answer-feedback]');
    if(notice){notice.textContent=message;notice.hidden=!message;}
  }
  async function submitMasteryAnswer(form,fields){
    const result=await api(`/api/practice/mastery/${form.dataset.quiz}/answer`,'POST',{questionId:fields.get('questionId'),answer:fields.get('answer')});
    if(result.duplicate){
      answerFeedback(form,result.message||'This answer is unchanged. Use the feedback to revise it before submitting again.');
      form.elements.answer.focus({preventScroll:true});
      return false;
    }
    try{(window.RiverMemory || localStorage).removeItem(draftKey(form));}catch{}
    return true;
  }
  modal.addEventListener('input',event=>{if(event.target.closest('#practice-editor'))resizeEditorFields();const form=event.target.closest('#practice-answer,.mastery-answer');if(form){answerFeedback(form);try{(window.RiverMemory || localStorage).setItem(draftKey(form),form.elements.answer.value);}catch{}}});
  page.addEventListener('input',event=>{const form=event.target.closest('.mastery-answer');if(form){answerFeedback(form);try{(window.RiverMemory || localStorage).setItem(draftKey(form),form.elements.answer.value);}catch{}}});
  page.addEventListener('submit',async event=>{
    const form=event.target;if(!form.matches('.mastery-answer,.tutor-chat-form'))return;
    event.preventDefault();if(form.dataset.submitting)return;form.dataset.submitting='true';const f=new FormData(form),submit=form.querySelector('[type=submit]');if(submit)submit.disabled=true;
    const scroll=window.scrollY;
    try{
      if(form.classList.contains('mastery-answer')){
        if(!await submitMasteryAnswer(form,f))return;
      }else{
        await api(`/api/practice/mastery/${form.dataset.quiz}/tutor`,'POST',{questionId:f.get('questionId'),message:f.get('message')});
      }
      await load(false);const q=practice.quizzes.find(item=>item.id===form.dataset.quiz);if(q)renderMastery(q,false,true);window.scrollTo(0,scroll);await changed();
    }catch(e){getApp().toast(e.message,true);}finally{delete form.dataset.submitting;if(submit&&submit.isConnected)submit.disabled=false;}
  });
  modal.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;if(form.dataset.submitting)return;form.dataset.submitting='true';const f=new FormData(form);const submit=form.querySelector('[type=submit]')||form.querySelector('button');if(submit)submit.disabled=true;
    try{
      if(form.classList.contains('mastery-answer')){
        if(!await submitMasteryAnswer(form,f))return;
        await load(false);renderQuiz();await changed();
      }else if(form.id==='practice-editor'){
        collectEditor();if(editor.id&&JSON.stringify(editor)===editorOriginal){modal.close();editor=null;return;}await api('/api/parent/practice/quizzes'+(editor.id?'/'+editor.id:''),editor.id?'PUT':'POST',editor);
        modal.close();editor=null;await changed();getApp().toast('Draft saved. Use Publish quiz to add it to Student’s plan.');
      }else if(form.id==='practice-import'){
        const parsed=JSON.parse(String(f.get('json')));await api('/api/parent/practice/import','POST',parsed.quizzes?parsed:{quizzes:[parsed]});modal.close();await changed();getApp().toast('Drafts imported. Use Publish quiz when you’re ready.');
      }else if(form.id==='writing-settings'){
        await api('/api/parent/practice/writing','POST',{level:f.get('level'),automatic:f.has('automatic'),expectedRevision:Number(form.dataset.revision)});modal.close();await changed();getApp().toast('Writing expectations saved. Started quizzes keep their original level.');
      }else if(form.id==='practice-settings'){
        await api('/api/parent/practice/settings','POST',{sessionQuestions:Number(f.get('sessionQuestions')),sessionMinutes:Number(f.get('sessionMinutes')),tutoringSupport:f.get('tutoringSupport')});modal.close();await changed();
      }else if(form.id==='practice-answer'){
        await api(`/api/practice/sessions/${f.get('sessionId')}/answer`,'POST',{questionId:f.get('questionId'),answer:f.get('answer')});try{(window.RiverMemory || localStorage).removeItem(draftKey(form));}catch{}await load(false);renderQuiz();await changed();
      }else if(form.id==='practice-review'){
        await api(`/api/parent/practice/attempts/${form.dataset.id}/review`,'POST',{correct:f.get('correct')==='true',independent:f.has('independent'),rubricEvidence:f.get('rubricEvidence'),help:f.get('help')});modal.close();await changed();
      }else if(form.id==='practice-revoke'){
        await api(`/api/parent/practice/attempts/${form.dataset.id}/revoke`,'POST',{note:f.get('note')});modal.close();await changed();
      }
    }catch(e){getApp().toast(e instanceof SyntaxError?'That prepared quiz is not valid JSON. Ask your assistant to check it.':e.message,true);}finally{delete form.dataset.submitting;if(submit)submit.disabled=false;}
  });
  function capturePracticeInputs(root){
    return [...root.querySelectorAll('.mastery-answer,.tutor-chat-form')].map(form=>{
      const field=form.elements.answer||form.elements.message;
      return {quizId:form.dataset.quiz,questionId:form.elements.questionId.value,name:field.name,value:field.value,
        focused:field===document.activeElement,start:field.selectionStart,end:field.selectionEnd,
        direction:field.selectionDirection,scroll:field.scrollTop,feedback:form.querySelector('[data-answer-feedback]')?.textContent||''};
    });
  }
  function restorePracticeInputs(root,saved){
    for(const form of root.querySelectorAll('.mastery-answer,.tutor-chat-form')){
      const field=form.elements.answer||form.elements.message;
      const previous=saved.find(item=>item.quizId===form.dataset.quiz&&item.questionId===form.elements.questionId.value&&item.name===field.name);
      if(!previous)continue;
      field.value=previous.value;
      answerFeedback(form,previous.feedback);
      if(previous.focused){field.focus({preventScroll:true});field.setSelectionRange(previous.start,previous.end,previous.direction);}
      field.scrollTop=previous.scroll;
    }
  }
  async function pollReviews(){
    const onPage=Boolean(pageQuiz&&!page.hidden),root=onPage?page:modal,quizId=onPage?pageQuiz:modalQuiz;
    if(reviewPollBusy||!aiReview.enabled||(!onPage&&!modal.open)||parentMode||!quizId||!root.querySelector('.mastery-answer, .tutor-panel, [aria-label="Quiz completion summary"]'))return;
    if(root.querySelector('form[data-submitting]'))return;
    reviewPollBusy=true;
    try{
      const [data,review]=await Promise.all([api('/api/practice'),api('/api/ai-review')]);
      if((!onPage&&!modal.open)||parentMode||(onPage?pageQuiz:modalQuiz)!==quizId||root.querySelector('form[data-submitting]'))return;
      const before=practice.quizzes.find(q=>q.id===quizId),after=data.quizzes.find(q=>q.id===quizId);
      if(!after?.mastery)return;
      const beforeChats=(practice.tutorChats||[]).filter(chat=>chat.quizId===quizId),afterChats=(data.tutorChats||[]).filter(chat=>chat.quizId===quizId);
      const hasChanged=JSON.stringify([before?.mastery,beforeChats,aiReview])!==JSON.stringify([after.mastery,afterChats,review]);
      practice=data;aiReview=review;
      if(hasChanged){
        const inputs=capturePracticeInputs(root);
        if(onPage)renderMastery(after,false,currentSequentialStep(before)===currentSequentialStep(after));
        else {const scroll=modal.scrollTop;renderMastery(after,false);modal.scrollTop=scroll;}
        restorePracticeInputs(root,inputs);
      }
    }catch{}finally{reviewPollBusy=false;}
  }
  setInterval(pollReviews,2500);
  window.addEventListener('resize',resizeEditorFields);
  window.addEventListener('hashchange',async()=>{
    const id=location.hash.match(/^#practice\/([a-zA-Z0-9_-]+)$/)?.[1];
    if(id){if(id!==pageQuiz)try{await showQuiz(id,false);}catch(e){getApp().toast(e.message,true);}}
    else if(pageQuiz)closePracticePage(false);
  });
  document.addEventListener('river:render',event=>mount(event.detail));
})();
