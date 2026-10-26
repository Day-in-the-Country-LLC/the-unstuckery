'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const main = $('#main');
const dialog = $('#action-dialog');
let state = null;
let auth = { configured: false, parent: false };
let view = location.pathname === '/parent' ? 'parent' : 'today';
let reviewDate = '';
let selectedId = null;
let stateReceivedAt = Date.now();
let alertState = null;
let alertStateReceivedAt = Date.now();
let lastRenderKey = '';
let toastTimeout;
let busy = false;
let authPending = false;
let draftItems = [];
let parentHistory = [];
const timerAudio = new window.RiverTimerAudio();
document.addEventListener('pointerdown', () => timerAudio.arm());
document.addEventListener('keydown', () => timerAudio.arm());

const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const safeUrl = value => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
const link = (value, label = 'Open assignment ↗') => safeUrl(value) ? `<a href="${esc(safeUrl(value))}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : '';
const dateText = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString('en-US', {weekday:'long', month:'long', day:'numeric'}) : '';
const timeText = value => value ? new Date(value).toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '';
const compactTime = seconds => `${Math.floor(Math.max(0, seconds) / 60).toString().padStart(2, '0')}:${Math.floor(Math.max(0, seconds) % 60).toString().padStart(2, '0')}`;
const button = (label, action, id = '', classes = 'secondary', extras = '') => `<button class="button ${classes}" data-action="${esc(action)}" data-id="${esc(id)}" ${extras}>${label}</button>`;
const currentTask = id => [...(state?.tasks || []), ...(state?.schoolwork?.upcoming || [])].find(task => task.id === id);

async function api(path, method = 'GET', body) {
  const options = { method, headers: {'Content-Type':'application/json', 'X-Student-Request':'1'}, credentials:'same-origin', cache:'no-store' };
  if (body !== undefined) options.body = JSON.stringify(body);
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('The app is not connected right now. Keep this page open and try again.'); }
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && (auth.cloud || ['/parent','/student'].includes(location.pathname))) { location.assign('/login'); throw new Error('Please sign in again.'); }
    if (response.status === 401 && path.startsWith('/api/parent')) { auth.parent = false; lastRenderKey = ''; render(); }
    throw new Error(result.error || 'That change could not be saved. Please try again.');
  }
  return result;
}

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { element.hidden = true; }, error ? 6500 : 4000);
}

async function refresh(force = false) {
  try {
    await window.RiverMemory.sync();
    const memoryStatus = window.RiverMemory.status();
    if (memoryStatus.error) throw new Error(memoryStatus.error);
    const requestedDate = view === 'parent' && auth.parent ? reviewDate : '';
    const result = await api('/api/state' + (requestedDate ? `?date=${encodeURIComponent(requestedDate)}` : ''));
    state = result;
    stateReceivedAt = Date.now();
    alertState = result.date === result.today ? result : await api('/api/state');
    alertStateReceivedAt = Date.now();
    $('#connection').hidden = true;
    const storage = await api('/api/storage');
    $('#saved-status').textContent = memoryStatus.pending ? 'Saving your draft…' : storage.mode === 'google-drive' ? (storage.error ? 'Save paused — keep this Mac open' : 'Saved to Google Drive folder · check Drive for sync status') : auth.cloud ? 'Saved in The Unstuckery' : 'Saved on this device';
    if (storage.error) throw new Error(storage.error);
    if (force || (!dialog.open && !(main.contains(document.activeElement) && document.activeElement.matches('input,textarea,select,[contenteditable]')))) render(force);
    updateTimer();
  } catch (error) {
    $('#connection').textContent = error.message;
    $('#connection').hidden = false;
    $('#saved-status').textContent = 'Waiting to reconnect';
    if (!state) main.innerHTML = `<div class="card empty-state"><h2>Let’s reconnect.</h2><p>${esc(error.message)}</p>${button('Try again', 'refresh', '', 'primary')}</div>`;
  }
}

function render(force = false) {
  if (!state) return;
  const stableBraces = {...state.braces, elapsedSeconds: Math.floor((state.braces?.elapsedSeconds || 0) / 60)};
  const key = JSON.stringify({view,auth,selectedId,state:{...state,braces:stableBraces,schoolClock:{...state.schoolClock,elapsedSeconds:Math.floor((state.schoolClock?.elapsedSeconds||0)/60)}}});
  if (!force && key === lastRenderKey) return;
  lastRenderKey = key;
  const openDetails = [...main.querySelectorAll('details[open][id]')].map(item => item.id);
  $('#today-tab').classList.toggle('active', view === 'today');
  $('#parent-tab').classList.toggle('active', view === 'parent');
  $('#today-tab').toggleAttribute('aria-current', view === 'today');
  $('#parent-tab').toggleAttribute('aria-current', view === 'parent');
  if (view === 'parent') main.innerHTML = auth.parent ? parentPage() : authPage();
  else main.innerHTML = todayPage();
  for (const id of openDetails) { const element = document.getElementById(id); if (element) element.open = true; }
  updateTimer();
  document.dispatchEvent(new CustomEvent('river:render',{detail:{view,parent:auth.parent}}));
}

function evidenceBadges(task) {
  const badges = [];
  if (task.kind === 'chore' && task.routineScheduled === false) badges.push('<span class="badge neutral">Not scheduled today · saved activity</span>');
  if (task.parentReturned) badges.push(`<span class="badge waiting">Sent back by parent · ${esc(timeText(task.parentReturned.at))}</span>`);
  if (task.reportedAt) badges.push(`<span class="badge neutral">Ready for review · ${esc(timeText(task.reportedAt))}</span>`);
  if (task.understanding) badges.push('<span class="badge">✓ Understanding checked</span>');
  if (task.submission) badges.push('<span class="badge">✓ Submission confirmed</span>');
  if (task.parentConfirmed) badges.push('<span class="badge">✓ Parent confirmed</span>');
  if (task.completedAt && task.completionBasis?.type==='parent_outside_app_review') badges.push('<span class="badge neutral">Parent reviewed outside the app</span>');
  if (task.completedAt) badges.push(`<span class="badge">✓ Finished · ${esc(timeText(task.completedAt))}</span>`);
  if ((task.needsReview || []).length && !task.completedAt) badges.push('<span class="badge waiting">Needs review</span>');
  return badges.length ? `<div class="badge-list">${badges.join('')}</div>` : '';
}

function requirementLabels(task) {
  const req = task.requirements || {};
  return [req.understanding ? 'Explain your thinking with the assistant' : '', req.submission ? 'Check the actual submission confirmation' : '', req.parent ? 'Ask your parent to review this work' : ''].filter(Boolean);
}

function chooseCurrent() {
  const selected = currentTask(selectedId);
  if (selected && selected.routineScheduled !== false && !selected.completedAt && selected.owner !== 'mom') return selected;
  return state.tasks.find(task => task.kind === 'chore' && task.routineScheduled !== false && task.owner !== 'mom' && !task.reportedAt && !task.completedAt) || state.tasks.find(task => task.kind === 'school' && (task.planDate === state.date || task.practiceReviewDate === state.date) && !task.completedAt);
}

function nextAction(task) {
  if (task.completedAt) return '<strong>Finished.</strong>Your parent has checked this off.';
  if (task.reviewHold) return `<strong>A correction needs a fresh look.</strong>${esc(task.reviewHold)} Talk it through with the assistant or your parent.`;
  if (['chore','todo'].includes(task.kind)) return task.reportedAt ? '<strong>Your parent will check this.</strong>You can move on to your schoolwork while you wait.' : '<strong>Your next step</strong>When you’re ready, let your parent know. They’ll check this off.';
  if (!task.reportedAt) return `<strong>Your next step</strong>${task.criteria ? esc(task.criteria) : 'Work through the instructions, then let the assistant know you’re ready for a check.'}`;
  if (task.canComplete) return '<strong>Your required checks are ready.</strong>You can mark this assignment finished.';
  return `<strong>Ready for the next check.</strong>${(task.needsReview || ['Ask the assistant to review this work.']).map(esc).join(' · ')}<br>Talk with the assistant alongside this app. Your review will appear here when it is saved.`;
}

function todayPage() {
  const task = chooseCurrent();
  const todayTasks = state.tasks.filter(item => item.routineScheduled !== false && item.kind !== 'todo' && (item.kind !== 'school' || item.planDate === state.date || item.practiceReviewDate === state.date));
  const unfinished = state.tasks.filter(item => !item.completedAt);
  const schoolCount = unfinished.filter(item => item.kind === 'school' && (item.planDate === state.date || item.practiceReviewDate === state.date)).length;
  const otherTodoOpen = unfinished.some(item=>item.kind==='todo');
  const waiting = state.tasks.filter(item => ['chore','todo'].includes(item.kind) && item.routineScheduled !== false && item.owner !== 'mom' && item.reportedAt && !item.completedAt).length;
  return `<section class="intro"><div><div class="date-label">${esc(dateText(state.date))}</div><h1>One thing at a time.</h1><p class="muted">${task || otherTodoOpen ? 'Start here. We’ll keep your place.' : 'You’ve reached the end of your current plan.'}</p></div></section>
    <div class="today-grid"><div>${schoolClockCard()}<section class="card current-card" aria-label="Current task">${task ? currentTaskCard(task) : `<div class="task-heading"><span class="task-number" aria-hidden="true">✓</span><p class="eyebrow">A good place to pause</p></div><h2>${waiting ? 'Your work is ready for review.' : otherTodoOpen ? 'Your other to-dos are below.' : schoolCount === 0 && !todayTasks.some(item => item.kind === 'school') ? 'Your schoolwork plan is on its way.' : 'Your current plan is finished.'}</h2><p class="muted">${waiting ? 'Your parent can check the routine when they’re ready. You can take a break.' : otherTodoOpen ? 'Your remaining items are in the Other to-dos section below.' : !todayTasks.some(item => item.kind === 'school') ? 'You can choose earlier unfinished work or a planned future assignment below. Your daily braces timer is ready whenever it fits.' : 'Take a break. Anything unfinished will stay in your plan.'}</p>${!state.afterSchool ? '<p class="small muted">There is no after-school routine planned for today.</p>' : ''}`}</section>
    ${todayTaskSections(todayTasks, task?.id, schoolCount)}${waiting ? `<p class="inline-note" style="margin-top:12px">${waiting} ${waiting === 1 ? 'item is' : 'items are'} waiting for your parent. You can keep going.</p>` : ''}${otherTodosSection(false)}</div>
    <aside><section class="card timer-card" aria-labelledby="braces-heading"><p class="eyebrow">Fits around your work</p><h2 id="braces-heading">Your hour in braces</h2><p class="small muted">Put them on, then start your timer.</p><div class="timer-readout"><b id="timer-value" style="font-weight:400">60:00</b><span id="timer-caption">remaining</span></div><div class="timer-track" aria-hidden="true"><div id="timer-progress"></div></div><p class="small muted" id="timer-state"></p><div class="actions" id="timer-actions"></div><p class="timer-note">The timer keeps your place when you close or refresh the app. Elapsed time is separate from your parent’s confirmation.</p><div id="timer-confirmation"></div></section>${riverRewards()}<div class="time-budget"><strong>Room for breaks.</strong>Three 30-minute sessions give you ${esc(state.schoolMinutes || 90)} minutes of schoolwork. Automatic 10-minute breaks do not count toward that time. Start your separate braces timer after putting them on. Unfinished work stays here for next time.</div></aside></div>${otherSchoolwork(task?.id)}`;
}

function todayTaskSections(tasks, activeId, schoolCount) {
  const routine = tasks.filter(task => task.kind !== 'school');
  const school = tasks.filter(task => task.kind === 'school');
  const rows = items => items.map(task => taskRow(task, tasks.indexOf(task), activeId)).join('');
  return `<section class="plan-section today-plan" aria-labelledby="today-tasks-heading">
    <div class="section-heading"><h2 id="today-tasks-heading">Today’s tasks</h2></div>
    <div class="card"><ul class="task-list">${routine.length ? rows(routine) : '<li class="empty-state small">No routine tasks planned for today.</li>'}</ul></div>
  </section>
  <section class="plan-section today-plan" aria-labelledby="today-schoolwork-heading">
    <div class="section-heading"><h2 id="today-schoolwork-heading">Today’s schoolwork</h2><span class="muted">${school.length ? `${schoolCount} ${schoolCount === 1 ? 'task' : 'tasks'} left` : 'Plan not added yet'}</span></div>
    <div class="card"><ul class="task-list">${school.length ? rows(school) : '<li class="empty-state small">Your parent’s schoolwork plan will appear here.</li>'}</ul></div>
    <div id="practice-today"></div>
  </section>`;
}

function otherSchoolwork(activeId) {
  const groups = state.schoolwork || {};
  return [['earlier', 'Earlier unfinished work', 'Keep going with work from another day.'], ['upcoming', 'Work ahead', 'Choose work your parent has already planned. Time spent on it counts toward today’s schoolwork.']].map(([key, title, note]) => {
    const tasks = groups[key] || [];
    const dates = [...new Set(tasks.map(task => task.planDate))].sort();
    const count = tasks.filter(task => !task.completedAt).length;
    return `<details class="card other-work" id="schoolwork-${key}">
      <summary><span class="other-work-title">${title}</span><span class="small muted">${count} ${count === 1 ? 'task' : 'tasks'}</span></summary>
      <div class="other-work-body"><p class="small muted">${note}</p>
      ${dates.length ? dates.map(date => {
        const rows = tasks.filter(task => task.planDate === date);
        return `<section class="schoolwork-date"><h3>${esc(dateText(date))}</h3><ul class="task-list">${rows.map((task, index) => taskRow(task, index, activeId)).join('')}</ul></section>`;
      }).join('') : `<p class="small muted">${key === 'earlier' ? 'No earlier schoolwork is unfinished.' : 'No future schoolwork is planned yet.'}</p>`}
      <div id="practice-${key}"></div></div>
    </details>`;
  }).join('');
}

function schoolworkKind(task) {
  if(task.kind!=='school')return null;
  if(task.practiceQuizId || task.scope==='practice')return 'practice';
  try { if(new URL(task.sourceUrl).hostname.endsWith('.instructure.com'))return 'assigned'; } catch {}
  return 'unconfirmed';
}
function schoolworkLabel(task) {
  const kind=schoolworkKind(task);
  return kind==='practice'?'EXTRA PRACTICE · Answer in this app':kind==='assigned'?'SCHOOL-ASSIGNED · Follow teacher submission instructions':kind==='unconfirmed'?'WORK TYPE NEEDS REVIEW':'';
}
function schoolworkNotice(task) {
  const kind=schoolworkKind(task);
  if(!kind)return '';
  if(kind==='practice')return '<div class="work-origin practice-origin"><strong>Extra practice — complete in this app</strong><p>This is learning practice prepared for you, not an unfinished school assignment. Type and submit your response here for feedback. It does not submit anything to Canvas or change a school grade.</p></div>';
  if(kind==='unconfirmed')return '<div class="work-origin"><strong>Work type needs review</strong><p>This older item has not been clearly classified. Check with your parent before treating it as missing schoolwork. Extra practice should have an answer space in this app.</p></div>';
  const evidence=task.submission?'Submission confirmation is recorded; check the instructions for any remaining teacher-requested corrections.':'School submission has not been verified in this app. Check Canvas or the teacher’s submission record before redoing work.';
  return `<div class="work-origin assigned-origin"><strong>School-assigned work</strong><p>Open Canvas on your school Chromebook, complete the teacher’s assignment, and submit it there. If the teacher requires paper or another tool, follow that method instead. Marking this app ready does not turn work in at school.</p><p class="small">${evidence}</p></div>`;
}
function schoolworkSource(task) {
  if(!task.sourceUrl)return '';
  const kind=schoolworkKind(task);
  const label=kind==='practice'?'Reference material ↗':kind==='assigned'?(/\/files\//.test(task.sourceUrl)?'Open school materials in Canvas ↗':'Open school assignment in Canvas ↗'):'Open materials ↗';
  return `<p class="small">${link(task.sourceUrl,label)}</p>`;
}
function currentTaskCard(task) {
  const plannedLabel=task.kind==='school'?`<p class="small muted">${task.practiceReviewDate===state.date&&task.planDate<state.date?`Tutor follow-up scheduled for ${esc(dateText(task.practiceReviewDate))}`:`Planned for ${esc(dateText(task.planDate))}${task.planDate>state.today?' · Working ahead · schoolwork time counts today':task.planDate<state.today?' · Earlier unfinished work':''}`}</p>`:'';
  if(task.masteryReview)return `${plannedLabel}${schoolworkNotice(task)}<p class="eyebrow">Extra practice · In-app response</p><h2>${esc(task.title)}</h2><p>Open this task to see the instructions, questions, saved answers, feedback, and tutoring chat on one focused page.</p>${evidenceBadges(task)}${task.parentReturned?'<p class="notice">Your parent sent this back to you. Check with them, then report ready again. Your approved answers are saved.</p>':''}<div class="actions"><button class="button primary" data-practice-open="${esc(task.practiceQuizId)}">Open practice</button>${!task.reportedAt?button('Ready for parent review','report',task.id,'primary',task.canReportReady?'':'disabled'):'<p>Waiting for your parent.</p>'}</div>`;
  const taskIndex = state.tasks.findIndex(item => item.id === task.id) + 1;
  const isChore = ['chore','todo'].includes(task.kind);
  const dishwasher = task.id === `${task.planDate}-dishwasher`;
  const labels = requirementLabels(task);
  return `${plannedLabel}${schoolworkNotice(task)}<div class="task-heading"><span class="task-number" aria-hidden="true">${taskIndex || '↗'}</span><p class="eyebrow">${esc(task.kind === 'todo' ? 'One-time chore' : isChore ? 'Routine chore' : task.subject || 'Schoolwork')}${task.practiceQuizId ? ' · Quiz' : task.scope === 'work_block' ? ' · Work session' : ''}</p></div><h2>${esc(task.title)}</h2>${task.scope === 'work_block' ? '<p class="small muted">This is one work session. Finishing it does not check off the whole assignment.</p>' : ''}${task.owner === 'mom' ? '<p class="owner-note">Parent owns this chore during your redeemed swap. You do not need to do it.</p>' : ''}<p class="instructions">${esc(task.instructions || (isChore ? 'Take your time, then let your parent know it’s ready.' : 'Follow your assignment’s instructions. Ask for help whenever you need it.'))}</p>
    ${schoolworkSource(task)}${task.practiceQuizId ? `<div class="actions"><button class="button primary" data-practice-open="${esc(task.practiceQuizId)}">Open practice & answer</button></div>` : ''}${task.kind==='school'&&!task.practiceQuizId?`<div class="actions"><button class="button outline" data-photos-task="${esc(task.id)}">Photo help & review</button></div>`:''}${evidenceBadges(task)}<div class="next-step">${nextAction(task)}</div>
    ${task.helpNote ? `<p class="small muted"><strong>Your help note:</strong> ${esc(task.helpNote)}</p>` : ''}
    <div class="actions">${!task.reportedAt ? button(isChore || task.requirements?.parent ? 'Ready for parent review' : 'My work is ready', 'report', task.id, 'primary') : !isChore && (!task.requirements?.parent || task.canComplete) ? button('Mark finished', 'complete', task.id, 'primary', task.canComplete ? '' : 'disabled') : ''}
    ${dishwasher && !task.reportedAt ? button('No clean dishes to put away', 'dishwasher-empty', task.id, 'outline') : ''}${task.reportedAt && isChore && !task.completedAt ? button('Continue to schoolwork', 'next-school', '', 'primary') : ''}${!isChore ? button('I need a little help', 'help', task.id, 'outline') : ''}</div>
    <div class="secondary-actions">${!isChore ? button('Take a pause', 'pause-task', task.id, 'quiet') : ''}${task.reportedAt ? button('I need to correct this', 'correct', task.id, 'quiet') : ''}${!isChore && state.tasks.filter(item => item.kind === 'school' && !item.completedAt).length > 1 ? button('Choose another task', 'choose-another', task.id, 'quiet') : ''}</div>
    ${!isChore ? `<details class="task-detail" id="requirements-${esc(task.id)}"><summary>What counts as finished?</summary><div>${task.criteria ? `<p>${esc(task.criteria)}</p>` : ''}<p>First, report that your work is ready.${labels.length ? ' Then complete these checks:' : ' No additional checks are required by your plan.'}</p>${labels.length ? `<ul class="requirements">${labels.map(label => `<li>${esc(label)}</li>`).join('')}</ul>` : ''}${task.dueDate ? `<p style="margin-top:12px">Due date from the plan: ${esc(task.dueDate)}</p>` : ''}${task.sourceNote ? `<p class="muted">Plan note: ${esc(task.sourceNote)}</p>` : ''}</div></details>` : ''}`;
}

function taskRow(task, index, activeId) {
  const done = Boolean(task.completedAt);
  const waiting = task.reportedAt && !done;
  const subtitle = task.parentReturned ? 'Sent back by parent · ready when you are' : task.owner === 'mom' ? 'Parent’s chore · your redeemed swap' : done ? `Finished ${timeText(task.completedAt)}` : task.kind === 'chore' && waiting ? 'Ready · awaiting parent confirmation' : task.reviewHold ? 'Correction needs review' : waiting ? task.canComplete ? 'Checks ready · you can mark finished' : 'Ready · a check is still needed' : task.kind === 'chore' ? 'Routine' : `${task.subject || 'Schoolwork'}${task.scope === 'work_block' ? ' · Work session' : ''}${task.planDate < state.date ? ' · carried forward' : ''}`;
  const rowAction = task.owner === 'mom' ? '<span class="small muted">Parent</span>'
    : done ? button('Correct', 'correct', task.id, 'quiet small')
    : task.practiceQuizId ? `<button class="button quiet small" data-practice-open="${esc(task.practiceQuizId)}">Open practice</button>`
    : activeId === task.id ? '<span class="small muted">Current</span>' : button('Open', 'select-task', task.id, 'quiet small');
  return `<li class="task-row ${activeId === task.id ? 'selected' : ''}"><span class="row-marker ${done ? 'done' : waiting ? 'waiting' : ''}" aria-hidden="true">${task.owner === 'mom' ? 'M' : done ? '✓' : waiting ? '·' : index + 1}</span><div class="task-row-main"><div class="task-row-title">${esc(task.title)}</div><div class="task-row-sub">${esc(subtitle)}</div>${task.kind==='school'?`<div class="work-type-label">${esc(schoolworkLabel(task))}</div>`:''}</div>${rowAction}</li>`;
}

function updateTimer() {
  updateSchoolClock();
  checkTimerAlerts();
  if (!state?.braces || !$('#timer-value')) return;
  const braces = state.braces;
  const seconds = (braces.elapsedSeconds || 0) + (braces.running ? Math.floor((Date.now() - stateReceivedAt) / 1000) : 0);
  const finished = seconds >= 3600;
  $('#timer-value').textContent = compactTime(Math.max(0, 3600 - seconds));
  $('#timer-caption').textContent = finished ? 'hour elapsed' : 'remaining';
  $('#timer-progress').style.width = `${Math.min(100, seconds / 36)}%`;
  $('#timer-state').textContent = finished ? 'Your hour has elapsed on the timer.' : braces.running ? 'Timer is running. Pause if you take them off.' : seconds > 0 ? 'Paused. Start again when your braces are back on.' : 'Start whenever it fits into your afternoon.';
  const timerKey = JSON.stringify({running:braces.running,finished,started:Boolean(seconds || braces.startedAt)});
  if ($('#timer-actions').dataset.state !== timerKey) {
    $('#timer-actions').dataset.state = timerKey;
    $('#timer-actions').innerHTML = `${!finished ? button(braces.running ? 'Pause timer' : seconds > 0 ? 'Resume timer' : 'Start timer', braces.running ? 'braces-pause' : 'braces-start', '', 'primary') : ''}${seconds > 0 || braces.startedAt ? button('Correct timer', 'braces-reset', '', 'quiet small') : ''}`;
  }
  $('#timer-confirmation').innerHTML = braces.parentConfirmed ? `<div class="badge-list"><span class="badge">✓ Parent confirmed · ${esc(timeText(braces.parentConfirmed.at))}</span></div>` : finished ? '<div class="badge-list"><span class="badge waiting">Timer elapsed · parent review available</span></div>' : '';
}

function schoolClockCard(id = 'school-clock') {
  if (!state.schoolClock) return '';
  return `<section class="school-clock pomodoro" id="${id}" data-school-clock aria-label="Schoolwork time"><div><p class="eyebrow">Work, then a breather</p><strong data-clock-title></strong><p data-clock-caption></p><p data-clock-budget></p><p class="session-plan">30 minutes of work → 10-minute break → Resume work when you’re ready</p></div><div class="actions" data-clock-actions></div><div class="timer-sound"><span>${button(timerAudio.enabled ? 'Sound on' : 'Sound off','timer-sound','','quiet small')}${button('Test sound','timer-sound-test','','quiet small')}</span><p class="small muted">Keep this tab open for timer sounds. The countdown keeps your place if you close it.</p></div></section>`;
}

function currentTimerValues(source = state, receivedAt = stateReceivedAt) {
  const c = source?.schoolClock;
  const extra = Math.max(0, Math.floor((Date.now()-receivedAt)/1000));
  let p = c?.phase;
  let phaseSeconds = p ? Math.min(p.durationSeconds, p.elapsedSeconds + (p.running ? extra : 0)) : 0;
  const budgetExtra = c?.running && (!p || p.kind === 'work') ? Math.min(extra, p ? Math.max(0,p.durationSeconds-p.elapsedSeconds) : Infinity) : 0;
  const budget = Math.min(5400,(c?.elapsedSeconds || 0) + budgetExtra);
  // Match the server's deadline-based transition between polls; never start work.
  if (p?.kind === 'work' && p.running && phaseSeconds >= p.durationSeconds && budget < 5400) {
    const elapsed = Math.min(600, p.elapsedSeconds + extra - p.durationSeconds);
    p = {id:p.id+'-rest',kind:'break',durationSeconds:600,elapsedSeconds:elapsed,
      running:elapsed<600,automatic:true,fromWorkId:p.id};
    phaseSeconds = elapsed;
  }
  const phaseFinished = p && phaseSeconds >= p.durationSeconds;
  const braces = source?.braces;
  const bracesSeconds = Math.min(3600,(braces?.elapsedSeconds || 0) + (braces?.running ? extra : 0));
  return {p,phaseSeconds,phaseFinished,budget,bracesSeconds};
}

function checkTimerAlerts() {
  if (!alertState || alertState.date !== alertState.today) return;
  const source = alertState;
  const {p,phaseFinished,budget,bracesSeconds} = currentTimerValues(source, alertStateReceivedAt);
  if (p?.automatic && !phaseFinished) timerAudio.alert(`phase:${source.date}:${p.fromWorkId}`, 'Time for a break. Your work clock is paused for ten minutes.', toast);
  if (p && p.kind !== 'braces' && phaseFinished) timerAudio.alert(`phase:${source.date}:${p.id}`, budget >= 5400 ? 'Schoolwork time is up for today. Your place is saved.' : p.kind === 'work' ? 'Time for a break. Your work clock is paused.' : 'Break finished. Choose Resume work when you’re ready.', toast);
  if (!p && budget >= 5400) timerAudio.alert(`school:${source.date}`, 'Schoolwork time is up for today.', toast);
  if (bracesSeconds >= 3600) {
    const resets = (source.braces.history || []).filter(event => event.action === 'timer_corrected').length;
    timerAudio.alert(`braces:${source.date}:${resets}`, 'Your braces hour has elapsed. Parent confirmation is still separate.', toast);
  }
}

function updateSchoolClock() {
  if (!state?.schoolClock) return;
  const {p,phaseSeconds,phaseFinished,budget} = currentTimerValues();
  const atLimit = budget >= 5400;
  const running = p ? p.running && !phaseFinished : state.schoolClock.running;
  const workNumber = Math.min(3, Math.floor(budget/1800)+1);
  let title, caption, actions;
  if (atLimit) {
    title = 'Schoolwork is finished for today';
    caption = 'You’ve reached the 90-minute limit. Your work is saved for another day.';
    actions = '';
  } else if (p?.kind === 'break') {
    title = phaseFinished ? 'Break finished — ready when you are' : `${running ? 'Time for a break' : 'Break paused'} · ${compactTime(p.durationSeconds-phaseSeconds)}`;
    caption = 'Your work clock is paused. Choose Resume work when you’re ready to begin again.';
    actions = button('Resume work','school-work','','primary small') + (!phaseFinished ? button(running ? 'Pause break' : 'Resume break',running ? 'school-pause' : 'school-start','','outline small') : '');
  } else if (p?.kind === 'braces') {
    title = phaseFinished ? 'Braces timer finished' : `Braces break · ${compactTime(p.durationSeconds-phaseSeconds)}`;
    caption = 'Your work clock is paused. The braces timer is separate from schoolwork.';
    actions = button('Resume work','school-work','','primary small') + (!phaseFinished ? button(running ? 'Pause braces break' : 'Resume braces break',running ? 'school-pause' : 'school-start','','outline small') : '');
  } else if (p && !phaseFinished) {
    title = `${running ? `Work session ${p.sessionNumber || workNumber} of 3` : 'Work paused'} · ${compactTime(p.durationSeconds-phaseSeconds)}`;
    caption = running ? 'A 10-minute break starts when this interval ends. Work only resumes when you choose.' : 'Choose Resume work when you’re ready to begin again.';
    actions = button(running ? 'Pause work' : 'Resume work',running ? 'school-pause' : 'school-work','','primary small');
  } else {
    title = p ? 'Time for a break' : budget ? (running ? 'Your schoolwork window is running' : 'Work paused') : '30 minutes of work, then a break';
    caption = p ? 'Your work clock is paused. Take a breather, then choose Resume work.' : '10-minute breaks are separate from your 90 minutes of schoolwork.';
    actions = button(budget ? 'Resume work' : 'Start work',running ? 'school-pause' : 'school-work','','primary small');
    if (running) actions = button('Pause work','school-pause','','primary small');
  }
  for (const card of document.querySelectorAll('[data-school-clock]')) {
    card.classList.toggle('limit',atLimit);
    card.querySelector('[data-clock-title]').textContent = title;
    card.querySelector('[data-clock-caption]').textContent = caption;
    card.querySelector('[data-clock-budget]').textContent = atLimit ? '90 minutes of schoolwork recorded. Unfinished work carries forward.' : `${compactTime(5400-budget)} of schoolwork left · ${Math.floor(budget/1800)} of 3 work sessions complete. Breaks do not count toward the 90 minutes.`;
    const controls = card.querySelector('[data-clock-actions]');
    if (controls.innerHTML !== actions) controls.innerHTML = actions;
  }
}

function plannedTimeNote(school) {
  const total = school.filter(task => task.planDate === state.date).reduce((sum,task) => sum + (Number(task.plannedMinutes) || 0),0);
  return `<p class="${total > (state.schoolMinutes || 90) ? 'notice' : 'small muted'}">${total} minutes planned for this date${total > (state.schoolMinutes || 90) ? ` — above the ${state.schoolMinutes || 90}-minute window. Keep the plan within 90 minutes of actual schoolwork. Breaks are separate.` : `. Plan up to ${state.schoolMinutes || 90} minutes of schoolwork. Breaks are separate.`}</p>`;
}

function weekStrip(rewards) {
  const dayNames=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const schedule=(rewards.settings?.qualifyingDays||[]).map(d=>dayNames[d]).join(', ');
  return `<p class="small muted">Qualifying days: ${esc(schedule)}. Week starts ${esc(dayNames[rewards.settings?.weekStart||0])}. Braces stay daily.</p><div class="week-strip" aria-label="Weekly progress">${(rewards.week?.days || []).map(day => `<div class="week-day ${day.complete ? 'done' : ['awaiting_review','needs_plan'].includes(day.status) ? 'waiting' : ''}" title="${esc(`${day.date}: ${String(day.status).replace(/_/g,' ')}`)}"><span>${esc(new Date(`${day.date}T12:00:00`).toLocaleDateString('en-US',{weekday:'short'}))}</span><span class="week-dot">${day.complete ? '✓' : day.qualifying ? new Date(`${day.date}T12:00:00`).getDate() : '–'}</span>${day.holiday ? '<span>Freebie</span>' : ''}</div>`).join('')}</div>`;
}

function pendingAward(rewards) { return (rewards.history || []).find(award => !award.redeemedAt) || (rewards.week?.award && !rewards.week.award.redeemedAt ? rewards.week.award : null); }

function riverRewards() {
  const rewards = state.rewards;
  if (!rewards?.week) return '';
  const award = pendingAward(rewards);
  const week = rewards.week;
  return `<section class="card week-card"><p class="eyebrow">Something to look forward to</p><h2>Your week</h2><p class="small muted">${esc(week.completedDays ?? rewards.completedDays ?? 0)} of ${esc(week.requiredDays ?? rewards.requiredDays ?? 0)} planned days complete</p>${weekStrip(rewards)}${catchupCard(false)}${(rewards.activeSwaps || []).map(swap => `<p class="owner-note">Parent has ${esc(swap.title || swap.chore || 'your chosen chore')} from ${esc(swap.start)} through ${esc(swap.end)}.</p>`).join('')}
    ${award?.requestedPrize ? `<p class="reward-status">Your choice: <strong>${esc(award.requestedPrize.prize?.title || award.requestedPrize.title)}</strong>${award.requestedPrize.chore ? ` · ${esc(award.requestedPrize.chore)}` : ''}. Waiting for your parent to redeem it.</p>` : award ? '<p class="reward-status">Your parent has confirmed a prize for your week. Choose one below.</p>' : `<p class="small muted">${week.eligible || rewards.eligible ? 'Your week is ready for parent review.' : 'Your parent reviews the daily plan and confirms your finished week.'}</p>`}
    <div class="prize-list">${(rewards.catalog || []).filter(prize => prize.active).map(prize => `<article class="prize-choice"><h3>${esc(prize.title)}</h3><p>${esc(prize.description)}</p>${award ? button(award.requestedPrize ? 'Change my choice' : 'Choose this prize','request-prize',prize.id,'outline small',`data-award="${esc(award.id)}"`) : ''}</article>`).join('')}</div></section>`;
}

function parentRewards() {
  const rewards = state.rewards;
  if (!rewards?.week) return '';
  const week = rewards.week;
  const awards = (rewards.history || []).filter(award => !award.redeemedAt);
  return `<section class="parent-section"><div class="section-heading"><h2>Weekly prizes</h2><div class="actions" style="margin-top:0">${button('Holiday freebies','reward-holidays','','outline small')}${button('Week settings','reward-settings','','outline small')}${button('Review daily checklist','reward-day','','primary small')}</div></div><div class="card review-card"><div class="weekly-parent-row"><div><h3>${esc(week.start)} – ${esc(week.end)}</h3><p class="small muted">${esc(week.completedDays ?? rewards.completedDays ?? 0)} of ${esc(week.requiredDays ?? rewards.requiredDays ?? 0)} qualifying days complete. Review the expected schoolwork for each day explicitly.</p></div>${week.eligible || rewards.eligible ? button('Confirm week earned','reward-award','','primary small') : week.award ? '<span class="badge">✓ Prize earned</span>' : ''}</div>${weekStrip(rewards)}${catchupCard(true)}${(week.reasons || rewards.reasons || []).length ? `<p class="small muted">${(week.reasons || rewards.reasons).map(esc).join(' · ')}</p>` : ''}<details class="week-details" id="weekly-checklist-details"><summary>Daily checklist and ownership</summary>${(week.days || []).map(day => `<div class="checklist-day"><strong>${esc(dateText(day.date))}</strong> · ${day.holiday ? 'Holiday · freebie day' : esc(String(day.status).replace(/_/g,' '))}${day.holiday ? '<p class="small muted">Holiday credit counts toward the weekly prize. The records below show actual task progress.</p>' : ''}<ul>${(day.items || []).map(item => `<li>${esc(item.title)}${item.owner === 'mom' ? ' · Parent owns this chore' : ''}: ${esc(String(item.status).replace(/_/g,' '))}${item.reason ? ` — ${esc(item.reason)}` : ''}</li>`).join('')}</ul>${button('Review this date','reward-day','','quiet small',`data-date="${esc(day.date)}"`)}</div>`).join('')}</details>${awards.map(award => `<div class="checklist-day"><h3>Earned ${esc(award.start)} – ${esc(award.end)}</h3>${award.requestedPrize ? `<p class="small">Requested: ${esc(award.requestedPrize.prize?.title || award.requestedPrize.title)}${award.requestedPrize.chore ? ` · ${esc(award.requestedPrize.chore)}` : ''}</p>${award.requestedPrize.swap ? `<p class="small muted">Parent owns ${esc(award.requestedPrize.swap.title)} from ${esc(award.requestedPrize.swap.start)} through ${esc(award.requestedPrize.swap.end)}.</p>` : ''}${button('Redeem selected prize','reward-redeem',award.id,'primary small',`data-revision="${award.requestRevision}"`)}` : '<p class="small muted">Student has not selected a prize yet.</p>'}</div>`).join('')}</div><div class="section-heading" style="margin-top:20px"><h3>Prize choices</h3>${button('Add a prize','reward-catalog-add','','outline small')}</div><div class="catalog-grid">${(rewards.catalog || []).map(prize => `<article class="card prize-choice"><h3>${esc(prize.title)}</h3><p>${esc(prize.description)}</p><p>${prize.active ? 'Available to choose' : 'Hidden from choices'}</p>${button('Edit prize','reward-catalog-edit',prize.id,'outline small')}</article>`).join('')}</div><details class="week-details" id="reward-history"><summary>Earned and redeemed prize history</summary>${(rewards.history || []).length ? rewards.history.map(award => `<div class="checklist-day"><strong>${esc(award.start)} – ${esc(award.end)}</strong><p>Earned ${esc(timeText(award.earnedAt))}. ${award.redeemedAt ? `Redeemed ${esc(timeText(award.redeemedAt))}: ${esc(award.selectedPrize?.title || award.requestedPrize?.prize?.title || 'chosen prize')}.` : 'Not redeemed yet.'}</p>${award.redemptionNote ? `<p>${esc(award.redemptionNote)}</p>` : ''}</div>`).join('') : '<p>No weeks have been awarded yet.</p>'}</details></section>`;
}

function catchupCard(parent) {
  const c = state.rewards?.week?.catchup;
  if (!c?.supported) return '';
  if (!c.enabled) return parent ? `<section class="checklist-day" aria-label="Saturday catch-up"><h3>Saturday catch-up</h3><p class="small muted">Let Student finish this week’s schoolwork and assigned quizzes by Saturday night and still earn the prize.</p>${button('Allow Saturday catch-up','reward-catchup','','outline small','data-enabled="true"')}</section>` : '';
  const open = c.items.filter(item=>item.status !== 'complete');
  return `<section class="checklist-day" aria-label="Saturday catch-up"><h3>Saturday catch-up</h3><p class="small"><strong>Finish by ${esc(dateText(c.deadlineDate))}, 11:59 p.m.</strong></p><p class="small muted">Schoolwork and assigned quizzes only. Report ready before Sunday; parent review can happen afterward.</p><p class="small">${c.missedDeadline ? 'The deadline passed with schoolwork still unfinished. This week’s prize was not earned.' : !open.length ? 'All assigned schoolwork has its required checks. The rest of the weekly checklist still applies.' : c.closed ? 'The deadline has passed. Work reported in time is still awaiting its required checks.' : `${open.length} school ${open.length === 1 ? 'item' : 'items'} still to finish or review.`}</p>${open.length ? `<ul>${open.map(item=>`<li><strong>${esc(item.title)}</strong><br><span class="small muted">${esc(dateText(item.planDate))} · ${esc(item.reason)}</span>${!parent && !c.closed && state.tasks.some(t=>t.id===item.taskId) ? button('Open schoolwork','select-task',item.taskId,'quiet small') : ''}</li>`).join('')}</ul>` : ''}${parent ? button('Turn off Saturday catch-up','reward-catchup','','quiet small','data-enabled="false"') : ''}</section>`;
}

function authPage() {
  const setup = !auth.configured;
  return `<section class="card auth-card"><p class="eyebrow">A space for the parent</p><h1>${setup ? 'Set up parent review.' : 'Welcome back.'}</h1><p>${setup ? 'Choose a four-digit passcode to protect planning and parent checkoffs. Enter it here on this Mac.' : 'Enter your four-digit passcode to plan schoolwork and review Student’s day.'}</p><form id="auth-form" autocomplete="off"><div class="field"><label for="parent-pin">${setup ? 'New four-digit passcode' : 'Parent passcode'}</label><input class="pin-input" id="parent-pin" name="pin" type="password" inputmode="numeric" pattern="[0-9]{4}" minlength="4" maxlength="4" autocomplete="one-time-code" data-lpignore="true" data-1p-ignore required aria-describedby="pin-hint"><span class="hint" id="pin-hint">Enter four numbers.</span></div>${setup ? '<div class="field"><label for="confirm-pin">Confirm passcode</label><input class="pin-input" id="confirm-pin" name="confirm" type="password" inputmode="numeric" pattern="[0-9]{4}" minlength="4" maxlength="4" autocomplete="one-time-code" data-lpignore="true" data-1p-ignore required></div>' : ''}<p class="form-error" role="alert" hidden></p><button type="submit" class="button primary">${setup ? 'Create passcode' : 'Open parent review'}</button></form><p class="small muted" style="margin:20px 0 0">Student can keep moving through his plan while chores wait for your confirmation.</p></section>`;
}

function summaryItem(item) {
  if (typeof item === 'string') return item;
  if (item && typeof item === 'object') return [item.title || item.assignment || item.taskTitle || '', item.helpNote || item.help || item.reason || item.note || ''].filter(Boolean).join(' — ');
  return '';
}

function summaryCard(number, title, subtitle, items = []) {
  return `<section class="card summary-card"><span class="summary-number">${esc(number)}</span><h3>${esc(title)}</h3><p>${esc(subtitle)}</p>${items.length ? `<ul class="summary-list">${items.slice(0, 4).map(item => `<li>${esc(summaryItem(item))}</li>`).join('')}${items.length > 4 ? `<li>And ${items.length - 4} more below</li>` : ''}</ul>` : ''}</section>`;
}

function parentPage() {
  const school = state.tasks.filter(task => task.kind === 'school');
  const chores = state.tasks.filter(task => task.kind === 'chore');
  const completed = state.summary?.completed || state.tasks.filter(task => task.completedAt);
  const unfinished = state.summary?.unfinished || state.tasks.filter(task => !task.completedAt);
  const help = state.summary?.helpNeeded || state.tasks.filter(task => task.helpNote || task.understanding?.help);
  return `<section class="intro"><div><div class="date-label">${esc(dateText(state.date))}</div><h1>The day, with context.</h1><p class="muted">Plan the next steps and see what supports each checkoff.</p></div><div class="parent-controls"><label class="small muted" for="review-date">Review date</label><input id="review-date" type="date" value="${esc(state.date)}" aria-label="Review date">${button(auth.cloud ? 'Preview student view' : 'Lock parent view', 'logout', '', 'outline small')}</div></section>
    ${state.date !== state.today ? `<div class="notice">You’re reviewing ${esc(dateText(state.date))}. Student’s Today screen follows the current day.${button('Return to today', 'review-today', '', 'quiet small')}</div>` : ''}
    <div class="summary-grid">${summaryCard(completed.length, 'Completed', 'Supported by the checks below.', completed)}${summaryCard(unfinished.length, 'Still open', 'Unfinished schoolwork carries forward.', unfinished)}${summaryCard(help.length, 'Learning help', 'Notes to guide the next conversation.', help)}</div>
    <section class="parent-section"><div class="section-heading"><h2>Routine & braces</h2><div class="actions" style="margin-top:0">${button('Chore days','routine-days','','outline small')}${button('+ Add routine chore','add-routine-chore','','outline small')}</div></div><div class="parent-routine-setting"><label class="check-label"><input id="after-school-toggle" type="checkbox" ${state.afterSchool ? 'checked' : ''}><span>Include chores that follow the after-school routine on this date</span></label></div><div class="card review-card">${chores.map(choreReview).join('')}${!chores.length ? '<p class="small muted">No routine chores are scheduled for this date.</p>' : ''}${bracesReview()}</div></section>
    ${parentSchoolClock()}<div id="canvas-parent"></div><section class="parent-section"><div class="section-heading"><h2>Schoolwork plan</h2><div class="actions" style="margin-top:0">${button('Review saved-plan suggestions', 'drafts', '', 'outline small')}${button('+ Add assignment', 'add-task', '', 'primary small')}</div></div><p class="small muted">Order the work as you’d like Student to approach it. Set checks from the assignment instructions and your plan.</p>${plannedTimeNote(school)}${school.length ? school.map((task, index) => parentTask(task, index, school.length)).join('') : '<div class="card empty-state"><h3>A clear first step starts here.</h3><p>Add an assignment, or review the dated suggestions from the saved school plan.</p></div>'}</section>${earlySchoolworkReview()}<div id="practice-parent"></div><div id="backlog-parent"></div>${otherTodosSection(true)}${parentRewards()}
    <section class="parent-section"><div class="section-heading"><h2>Daily activity</h2>${button('Read today’s history', 'daily-history', '', 'outline small')}</div><p class="inline-note">Reports, checkoffs, learning notes, and later corrections are kept with their times. Open an assignment’s details to see its evidence.</p></section>`;
}

function earlySchoolworkReview(){
  const tasks=(state.schoolwork?.upcoming || []).filter(t=>t.reportedAt && !t.completedAt);
  if(!tasks.length)return '';
  return `<section class="parent-section" aria-label="Work finished ahead"><h2>Work finished ahead</h2><p class="small muted">Student has reported these future assignments ready. Confirming them keeps their planned dates.</p>${tasks.map(t=>`<article class="card parent-task-card"><h3>${esc(t.title)}</h3><p>Planned for ${esc(dateText(t.planDate))}</p>${parentSchoolActions(t)}</article>`).join('')}</section>`;
}

function parentSchoolClock() {
  const c = state.schoolClock;
  if (!c) return '';
  return `<section class="parent-section" aria-label="Schoolwork timer review"><div class="section-heading"><h2>Schoolwork timer</h2>${button('Correct schoolwork time','correct-school-time','','outline small',state.date > state.today ? 'disabled' : '')}</div><div class="card review-card"><p><strong>${esc(compactTime(c.elapsedSeconds))}</strong> recorded toward 90 minutes of schoolwork · ${esc(compactTime(5400-c.elapsedSeconds))} remaining.</p><p class="small muted">${c.running ? 'Schoolwork is running.' : c.phase?.running ? 'A break is running. Schoolwork time is paused.' : 'Schoolwork is paused.'} Remove any minutes spent playing or doing something other than schoolwork.</p><details class="task-detail" id="school-time-history"><summary>Timer corrections</summary>${historyHtml(c.history || [])}</details></div></section>`;
}

function correctSchoolTime() {
  const day = state.date;
  openDialog('Correct schoolwork time', `<p class="dialog-description">${esc(dateText(day))} · ${esc(compactTime(state.schoolClock.elapsedSeconds))} recorded. Remove minutes that were not schoolwork. Schoolwork pauses until a work session is started or resumed. Break timers keep their time.</p><form id="school-time-form"><div class="field"><label for="school-remove-minutes">Minutes to remove</label><input id="school-remove-minutes" name="removeMinutes" type="number" min="1" max="90" step="1" required value="${Math.max(1,Math.min(5,Math.floor(state.schoolClock.elapsedSeconds/60)))}"></div><div class="field"><label for="school-correction-note">Note (optional)</label><input id="school-correction-note" name="note" maxlength="4000" placeholder="Time spent off task"></div><p class="small muted">For example, removing 10 minutes adds 10 minutes back to the schoolwork time remaining. Completed assignments stay checked off.</p><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel','close-dialog','','quiet','type="button"')}<button class="button primary" type="submit">Remove time and pause schoolwork</button></div></form>`);
  $('#school-time-form').addEventListener('submit',async event=>{
    event.preventDefault();
    await submitForm(event.currentTarget,async()=>{
      const fields = new FormData(event.target);
      await api('/api/parent/school-clock/correct','POST',{date:day,removeMinutes:Number(fields.get('removeMinutes')),note:fields.get('note').trim()});
      dialog.close(); await refresh(true); toast('Schoolwork time corrected. Schoolwork is paused.');
    });
  });
}

function otherTodosSection(parent) {
  const items = state.tasks.filter(task=>task.kind==='todo');
  return `<section class="${parent ? 'parent-section' : 'plan-section'}" aria-label="Other to-dos"><div class="section-heading"><h2>Other to-dos</h2>${parent ? button('+ Add to-do','add-todo','','outline small') : ''}</div><p class="small muted">One-time items for the day. Unfinished items stay here until they are finished.</p><div class="card review-card">${items.length ? items.map(task=>todoCard(task,parent)).join('') : `<p class="small muted">${parent ? 'Assign errands, reminders, or other things Student needs to do.' : 'No other to-dos assigned yet.'}</p>`}</div></section>`;
}
function todoCard(task,parent) {
  if (parent) return choreReview(task);
  return `<article class="review-row"><div><p class="small muted">Assigned ${esc(dateText(task.planDate))}${task.planDate < state.date && !task.completedAt ? ' · Carried forward' : ''} · Does not repeat</p>${currentTaskCard(task)}<details class="task-detail" id="todo-history-${esc(task.id)}"><summary>Activity and corrections</summary>${historyHtml(task.history)}</details></div></article>`;
}
function todoEditor(task=null) {
  const planDate = task?.planDate || (state.date >= state.today ? state.date : state.today);
  const earliest = task?.planDate < state.today ? task.planDate : state.today;
  openDialog(task ? 'Edit a to-do' : 'Assign a to-do', `<p class="dialog-description">Works like a routine chore, for one assigned date. Student reports it ready and you confirm it with one click. It does not repeat; unfinished items carry forward.</p><form id="todo-form"><div class="field"><label for="todo-title">To-do</label><input id="todo-title" name="title" required maxlength="300" value="${esc(task?.title || '')}" placeholder="e.g. Pack your bag for tomorrow"></div><div class="field"><label for="todo-instructions">Instructions (optional)</label><textarea id="todo-instructions" name="instructions" maxlength="4000">${esc(task?.instructions || '')}</textarea></div><div class="field"><label for="todo-date">Assigned date</label><input id="todo-date" name="planDate" type="date" required min="${esc(earliest)}" value="${esc(planDate)}"></div><label class="check-label"><input name="countsForReward" type="checkbox" ${task?.countsForReward !== false ? 'checked' : ''}><span>Include in the daily prize checklist</span></label><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel','close-dialog','','quiet','type="button"')}<button class="button primary" type="submit">${task ? 'Save to-do' : 'Add to-do'}</button></div></form>`);
  $('#todo-form').addEventListener('submit',async event=>{
    event.preventDefault();
    await submitForm(event.currentTarget,async()=>{
      const fields=new FormData(event.target);
      await api(task ? `/api/parent/todos/${encodeURIComponent(task.id)}` : '/api/parent/todos','POST',{title:fields.get('title').trim(),instructions:fields.get('instructions').trim(),planDate:fields.get('planDate'),countsForReward:fields.has('countsForReward')});
      dialog.close(); await refresh(true); toast('To-do saved.');
    });
  });
}

function choreReview(task) {
  return `<div class="review-row"><div><h3>${esc(task.title)}</h3>${task.kind === 'todo' ? `<p class="small muted">Assigned ${esc(dateText(task.planDate))}${task.planDate < state.date && !task.completedAt ? ' · Carried forward' : ''} · Does not repeat · ${task.countsForReward ? 'Counts toward weekly prize' : 'Outside prize checklist'}</p>${task.instructions ? `<p>${esc(task.instructions)}</p>` : ''}` : ''}${task.owner === 'mom' ? '<p class="owner-note">Parent owns this chore during the redeemed swap. It is not expected from Student.</p>' : ''}${task.reportedAt ? `<p>Student reported ready: ${esc(timeText(task.reportedAt))}</p>` : '<p>Student has not reported this ready yet.</p>'}${task.reportedNote ? `<p><strong>Student’s note:</strong> ${esc(task.reportedNote)}</p>` : ''}${task.parentConfirmed ? `<p>Parent confirmed: ${esc(timeText(task.parentConfirmed.at))}${task.parentConfirmed.note ? ` · ${esc(task.parentConfirmed.note)}` : ''}</p>` : ''}${task.reviewHold ? `<p class="danger">Correction: ${esc(task.reviewHold)}</p>` : ''}${evidenceBadges(task)}<details id="history-${esc(task.id)}" class="task-detail"><summary>Activity and corrections</summary>${historyHtml(task.history)}</details></div><div class="actions">${task.kind === 'todo' ? button('Edit','edit-todo',task.id,'outline small',`aria-label="Edit to-do ${esc(task.title)}"`) : ''}${task.reviewHold ? button('Resolve correction', 'resolve', task.id, 'outline small') : ''}${task.parentConfirmed ? button('Correct checkoff', 'revoke-parent', task.id, 'quiet small') : button(task.kind === 'todo' ? 'Confirm to-do' : 'Confirm chore', 'confirm', task.id, 'primary small')}</div></div>`;
}

function bracesReview() {
  const braces = state.braces;
  return `<div class="review-row"><div><h3>One hour in braces</h3><p>${esc(compactTime(braces.elapsedSeconds || 0))} recorded · ${braces.timerElapsed ? 'one hour elapsed' : braces.running ? 'timer running' : braces.elapsedSeconds ? 'timer paused' : 'not started'}</p><p>Elapsed time records the timer, not proof of wearing braces.</p>${braces.parentConfirmed ? `<p>Parent confirmed ${esc(timeText(braces.parentConfirmed.at))}${braces.parentConfirmed.note ? ` · ${esc(braces.parentConfirmed.note)}` : ''}</p>` : '<p>Parent confirmation has not been recorded.</p>'}</div><div class="actions">${braces.parentConfirmed ? button('Correct confirmation', 'revoke-braces', '', 'quiet small') : button('Confirm braces hour', 'confirm-braces', '', 'outline small')}</div></div>`;
}

function parentSchoolActions(task){
  if(!task.requirements?.parent&&!task.masteryReview)return '';
  const ready=Boolean(task.reportedAt),confirmed=Boolean(task.parentConfirmed);
  const support=task.quizSupport;
  return `<section class="parent-ready-review"><p>${ready?`Student reported ready for parent review: ${esc(timeText(task.reportedAt))}`:'Student has not reported this ready for parent review yet.'}</p>${support?`<p class="small muted">${support.totalAttempts} attempts · ${support.totalHints} hints · ${support.totalTips} tips · ${support.totalHelpedAttempts} responses with help</p>`:''}<div class="actions">${!confirmed?button('Confirm','confirm',task.id,'primary small',ready?'':'disabled'):''}${ready||confirmed?button('Send back to Student','return-school',task.id,'outline small'):''}</div></section>`;
}

function parentTask(task, index, total) {
  const requirements = task.requirements || {};
  return `<article class="card parent-task-card"><div class="parent-task-top"><div><p class="eyebrow" style="font-size:.68rem">${esc(task.subject || 'Schoolwork')} · ${index + 1}</p><h3>${esc(task.title)}</h3><p>${task.practiceQuizId ? 'Quiz · ' : task.scope === 'work_block' ? 'Work session · ' : ''}${Number(task.plannedMinutes) ? `${esc(task.plannedMinutes)} min · ` : ''}Planned ${esc(task.planDate)}${task.planDate < state.date && !task.completedAt ? ' · carried forward' : ''}${task.dueDate ? ` · Due ${esc(task.dueDate)}` : ''}</p></div><div class="actions">${button('↑', 'move-up', task.id, 'quiet', `${index === 0 ? 'disabled' : ''} aria-label="Move ${esc(task.title)} earlier"`)}${button('↓', 'move-down', task.id, 'quiet', `${index === total - 1 ? 'disabled' : ''} aria-label="Move ${esc(task.title)} later"`)}${task.practiceQuizId ? `${button('Edit task', 'edit-task', task.id, 'outline small')}<button class="button outline small" data-practice="edit" data-id="${esc(task.practiceQuizId)}">Edit quiz</button>` : button('Edit', 'edit-task', task.id, 'outline small')}</div></div>${schoolworkNotice(task)}${evidenceBadges(task)}${!task.practiceQuizId?`<button class="button outline small" data-photos-task="${esc(task.id)}">View photos & feedback</button>`:''}${parentSchoolActions(task)}${task.needsReview?.length && !task.completedAt ? `<p class="small muted"><strong>Next:</strong> ${task.needsReview.map(esc).join(' · ')}</p>` : ''}${task.helpNote ? `<p class="small"><strong>Help requested:</strong> ${esc(task.helpNote)}</p>` : ''}${task.understanding?.help ? `<p class="small"><strong>Learning support:</strong> ${esc(task.understanding.help)}</p>` : ''}${task.reviewHold ? `<div class="notice"><p>Correction awaiting review: ${esc(task.reviewHold)}</p>${button('Resolve correction', 'resolve', task.id, 'outline small')}</div>` : ''}
    <details id="evidence-${esc(task.id)}"><summary>Instructions, evidence & history</summary><div class="task-detail"><p class="instructions small">${esc(task.instructions)}</p>${task.criteria ? `<p class="small"><strong>Completion requirements:</strong> ${esc(task.criteria)}</p>` : ''}<p class="small muted">Checks in this plan: ${[requirements.understanding ? 'understanding' : '', requirements.submission ? 'submission' : '', requirements.parent ? 'parent review' : ''].filter(Boolean).join(', ') || 'Student reports ready and marks finished'}.</p>${schoolworkSource(task)}${task.sourceNote ? `<p class="small muted">Plan source: ${esc(task.sourceNote)}</p>` : ''}<div class="evidence-grid">${evidenceBox('Student’s report', task.reportedAt ? {at:task.reportedAt, note:task.reportedNote} : null)}${evidenceBox('Understanding check', task.understanding, task.id, 'understanding')}${evidenceBox('Submission confirmation', task.submission, task.id, 'submission')}${evidenceBox('Parent confirmation', task.parentConfirmed, task.id, 'parentConfirmed')}</div><h3 style="margin-top:22px">Activity and corrections</h3>${historyHtml(task.history)}</div></details></article>`;
}

function evidenceBox(title, evidence, id = '', field = '') {
  return `<div class="evidence-box"><strong>${esc(title)}</strong>${evidence ? `<div>${esc(timeText(evidence.at))}</div>${evidence.goal ? `<p><b>Learning goal:</b> ${esc(evidence.goal)}</p>` : ''}${evidence.evidence ? `<p>${esc(evidence.evidence)}</p>` : ''}${evidence.help ? `<p><b>Help used:</b> ${esc(evidence.help)}</p>` : ''}${evidence.note ? `<p>${esc(evidence.note)}</p>` : ''}${evidence.observedAt ? `<p>Observed: ${esc(timeText(evidence.observedAt))}</p>` : ''}${link(evidence.sourceUrl, 'Evidence source ↗')}${id ? button('Correct this evidence', 'revoke-evidence', id, 'quiet small', `data-field="${esc(field)}"`) : ''}` : '<p>No check recorded yet.</p>'}</div>`;
}

function humanAction(action) {
  const known = {created:'Assignment planned',updated:'Plan updated',reported:'Reported ready',report:'Reported ready',completed:'Marked finished',complete:'Marked finished',help:'Help requested',corrected:'Correction recorded',correct:'Correction recorded',parent_confirmed:'Parent confirmed',understanding:'Understanding checked',submission:'Submission confirmed',resolved:'Correction reviewed',resolve:'Correction reviewed',reordered:'Plan order changed',imported:'Saved-plan suggestion added',braces_started:'Braces timer started',braces_paused:'Braces timer paused',braces_reset:'Braces timer corrected'};
  return known[action] || String(action || 'Activity').replace(/[_-]/g, ' ').replace(/^./, character => character.toUpperCase());
}

function detailText(details) {
  if (typeof details === 'string') return details;
  if (!details || typeof details !== 'object') return '';
  return Object.entries(details).filter(([key, value]) => value && !['pin','token','id','requirements'].includes(key)).map(([key,value]) => `${key.replace(/([A-Z])/g, ' $1').replace(/_/g, ' ')}: ${typeof value === 'object' ? Object.entries(value).map(([k,v]) => `${k}: ${typeof v === 'object' ? 'recorded' : v}`).join(', ') : value}`).join('\n');
}

function historyHtml(history = []) {
  if (!history?.length) return '<p class="small muted" style="margin-top:12px">No activity has been recorded yet.</p>';
  return `<ol class="history">${[...history].reverse().map(event => `<li><strong>${esc(humanAction(event.action))}</strong> <span>· ${esc(event.actor || '')}</span><p>${esc(event.action === 'school_time_corrected' ? `${event.details.removedMinutes} minutes removed from ${dateText(event.details.date)}: ${compactTime(event.details.beforeSeconds)} → ${compactTime(event.details.afterSeconds)} recorded. ${event.details.note}` : detailText(event.details))}</p><time>${esc(timeText(event.at))}</time></li>`).join('')}</ol>`;
}

function openDialog(title, content) {
  $('#dialog-body').innerHTML = `<div class="dialog-heading"><h2 id="dialog-title">${esc(title)}</h2><button class="close-button" data-action="close-dialog" aria-label="Close dialog">×</button></div>${content}`;
  dialog.showModal();
}

function noteDialog({title,description,label='A brief note',initial='',required=false,submit='Save note',path,body={},onSuccess}) {
  openDialog(title, `<p class="dialog-description">${esc(description)}</p><form id="note-form"><div class="field"><label for="action-note">${esc(label)}${required ? '' : ' (optional)'}</label><textarea id="action-note" name="note" maxlength="3000" ${required ? 'required' : ''} placeholder="${required ? 'What should we record?' : 'Anything helpful to know?'}">${esc(initial)}</textarea></div><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel', 'close-dialog', '', 'quiet', 'type="button"')}<button class="button primary" type="submit">${esc(submit)}</button></div></form>`);
  $('#note-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitForm(form, async () => {
      const note = new FormData(form).get('note').trim();
      if (required && !note) throw new Error('Please add a brief note.');
      await api(path, 'POST', {...body,note});
      dialog.close();
      if (onSuccess) onSuccess();
      await refresh(true);
      toast('Saved.');
    });
  });
}

async function submitForm(form, operation) {
  const submit = form.querySelector('[type="submit"]');
  const error = form.querySelector('.form-error');
  if (submit?.disabled) return;
  if (submit) submit.disabled = true;
  if (error) error.hidden = true;
  try { await operation(); } catch (failure) { if (error) { error.textContent = failure.message; error.hidden = false; } else toast(failure.message, true); } finally { if (submit) submit.disabled = false; }
}

const routineDayNames = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
function routineDaysSummary(days) {
  return days === null || days === undefined ? 'After-school routine days' : days.length === 7 ? 'Every day' : days.map(day=>routineDayNames[day]).join(', ');
}
function routineDaysFields(days = [0,1,2,3,4,5,6]) {
  const selected = days || [0,1,2,3,4];
  return `<div class="field"><label for="routine-mode">When to do this chore</label><select id="routine-mode" name="scheduleMode"><option value="weekdays" ${days ? 'selected' : ''}>Choose days of the week</option><option value="after-school" ${days ? '' : 'selected'}>Follow the after-school routine</option></select></div><fieldset id="routine-weekdays" class="field" style="border:0;padding:0" ${days ? '' : 'hidden'}><legend>Days of the week</legend><div class="actions" style="margin:8px 0">${button('Every day','routine-every-day','','outline small','type="button"')}${button('Clear days','routine-clear-days','','quiet small','type="button"')}</div><div class="routine-weekday-grid">${routineDayNames.map((name,index)=>`<label class="check-label"><input type="checkbox" name="days" value="${index}" ${selected.includes(index) ? 'checked' : ''}><span>${name}</span></label>`).join('')}</div><p class="small muted">Selected weekdays apply even when the after-school routine is off.</p></fieldset>`;
}
function wireRoutineDays() {
  $('#routine-mode').addEventListener('change',()=>{ $('#routine-weekdays').hidden = $('#routine-mode').value === 'after-school'; });
}
function routineDaysBody(fields) {
  if (fields.get('scheduleMode') === 'after-school') return null;
  const days = fields.getAll('days').map(Number);
  if (!days.length) throw new Error('Choose at least one day.');
  return days;
}
function routineDaysManager() {
  openDialog('Chore days', `<p class="dialog-description">Choose every day or specific weekdays for each chore. Days follow the calendar, including weekends.</p>${(state.routineCatalog || []).map(chore=>`<div class="checklist-day"><strong>${esc(chore.title)}</strong><p class="small muted">${esc(routineDaysSummary(chore.days))}${chore.effectiveDate > state.today ? ` · Starts ${esc(dateText(chore.effectiveDate))}` : ''}</p>${button('Set days','routine-edit-days',chore.id,'outline small',`aria-label="Set days for ${esc(chore.title)}"`)}</div>`).join('')}<div class="dialog-footer">${button('Done','close-dialog','','secondary')}</div>`);
}
function routineScheduleEditor(id) {
  const chore = state.routineCatalog.find(item=>item.id===id);
  const effectiveDate = [state.today,chore.startDate,chore.effectiveDate].sort().at(-1);
  openDialog(`Days for ${chore.title}`, `<form id="routine-schedule-form">${routineDaysFields(chore.days)}<div class="field"><label for="routine-effective">Apply starting</label><input id="routine-effective" name="effectiveDate" type="date" required min="${esc([state.today,chore.startDate].sort().at(-1))}" value="${esc(effectiveDate)}"></div><p class="small muted">Earlier checklists and confirmation history are preserved.</p><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel','close-dialog','','quiet','type="button"')}<button type="submit" class="button primary">Save chore days</button></div></form>`);
  wireRoutineDays();
  $('#routine-schedule-form').addEventListener('submit',async event=>{
    event.preventDefault();
    await submitForm(event.currentTarget,async()=>{
      const fields=new FormData(event.target);
      await api(`/api/parent/routine-chores/${encodeURIComponent(id)}/schedule`,'POST',{days:routineDaysBody(fields),effectiveDate:fields.get('effectiveDate')});
      dialog.close(); await refresh(true); toast('Chore days saved.');
    });
  });
}
function routineChoreEditor() {
  const startDate = state.date >= state.today ? state.date : state.today;
  openDialog('Add a routine chore', `<p class="dialog-description">Choose when this chore appears in Student’s checklist. Student reports it ready; you confirm it with one click.</p><form id="routine-chore-form"><div class="field"><label for="routine-chore-title">Chore name</label><input id="routine-chore-title" name="title" required maxlength="300" placeholder="e.g. Take a bath/shower"></div><div class="field"><label for="routine-chore-instructions">Instructions (optional)</label><textarea id="routine-chore-instructions" name="instructions" maxlength="4000" placeholder="Anything Student needs to know"></textarea></div>${routineDaysFields()}<div class="field"><label for="routine-chore-start">Start date</label><input id="routine-chore-start" name="startDate" type="date" required min="${esc(state.today)}" value="${esc(startDate)}"></div><p class="small muted">It counts toward routine expectations on its scheduled days. Earlier checklists stay as they were.</p><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel','close-dialog','','quiet','type="button"')}<button type="submit" class="button primary">Add routine chore</button></div></form>`);
  wireRoutineDays();
  $('#routine-chore-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitForm(form, async () => {
      const fields = new FormData(form);
      await api('/api/parent/routine-chores','POST',{title:fields.get('title').trim(),instructions:fields.get('instructions').trim(),startDate:fields.get('startDate'),days:routineDaysBody(fields)});
      dialog.close(); await refresh(true); toast('Routine chore added.');
    });
  });
}

function taskEditor(task = null) {
  const req = task?.requirements || {};
  openDialog(task ? 'Edit the assignment' : 'Plan an assignment', `<p class="dialog-description">Give Student a concrete next step. Use the checks the assignment and your plan actually need.</p><form id="task-form"><div class="form-grid"><div class="field"><label for="task-subject">Subject</label><input id="task-subject" name="subject" required maxlength="160" value="${esc(task?.subject || '')}" placeholder="e.g. Photography"></div><div class="field"><label for="task-plan-date">Plan for</label><input id="task-plan-date" name="planDate" type="date" required value="${esc(task?.planDate || state.date)}"></div><div class="field full"><label for="task-title">Assignment</label><input id="task-title" name="title" required maxlength="300" value="${esc(task?.title || '')}" placeholder="Name the work"></div><div class="field full"><label for="task-instructions">Instructions & next step</label><textarea id="task-instructions" name="instructions" required maxlength="10000" placeholder="Start with one manageable action.">${esc(task?.instructions || '')}</textarea></div><div class="field full"><label for="task-criteria">What counts as finished?</label><textarea id="task-criteria" name="criteria" maxlength="5000" placeholder="Required questions, attachments, or a learning goal to check…">${esc(task?.criteria || '')}</textarea></div><div class="field"><label for="task-due">Due date (optional)</label><input id="task-due" name="dueDate" type="date" value="${esc((task?.dueDate || '').slice(0,10))}"></div><div class="field"><label for="task-source">Source link (optional)</label><input id="task-source" name="sourceUrl" type="url" value="${esc(task?.sourceUrl || '')}" placeholder="https://…"></div><fieldset class="field full" style="border:0;padding:0"><legend style="font-size:.81rem;font-weight:650;margin-bottom:7px">Required checks before Student marks this finished</legend><label class="check-label"><input name="understanding" type="checkbox" ${req.understanding ? 'checked' : ''}><span>Understanding check<small>The assistant records a brief explanation or fresh example and any help used.</small></span></label><label class="check-label"><input name="submission" type="checkbox" ${req.submission ? 'checked' : ''}><span>Submission confirmation<small>Requires observed confirmation in the assignment portal. Leave off for work that is not submitted online.</small></span></label><label class="check-label"><input name="parent" type="checkbox" ${req.parent ? 'checked' : ''}><span>Parent review<small>Use for checking paper work, or another parent review the plan needs.</small></span></label></fieldset><div class="field full"><label for="task-source-note">Planning context (optional)</label><textarea id="task-source-note" name="sourceNote" maxlength="5000" placeholder="Where these instructions came from; anything still uncertain.">${esc(task?.sourceNote || '')}</textarea></div></div><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel', 'close-dialog', '', 'quiet', 'type="button"')}<button class="button primary" type="submit">${task ? 'Save changes' : 'Add to the plan'}</button></div></form>`);
  $('#task-form .form-grid').insertAdjacentHTML('afterbegin', `<div class="field"><label for="task-scope">Plan item</label><select id="task-scope" name="scope"><option value="assignment" ${task?.scope !== 'work_block' ? 'selected' : ''}>Whole assignment</option><option value="work_block" ${task?.scope === 'work_block' ? 'selected' : ''}>Work session on an assignment</option></select><span class="hint">A quiz stays one assignment. Questions and practice belong inside it.</span></div><div class="field"><label for="task-minutes">Planned work minutes</label><input id="task-minutes" name="plannedMinutes" type="number" min="0" max="90" step="1" value="${esc(task?.plannedMinutes ?? 25)}"><span class="hint">Plan up to 90 minutes of actual schoolwork. Breaks do not count.</span></div><div class="field full" id="linked-assignment-field" ${task?.scope === 'work_block' ? '' : 'hidden'}><label for="task-assignment">Assignment this session supports (optional)</label><select id="task-assignment" name="assignmentId"><option value="">No linked assignment</option>${state.tasks.filter(item => item.kind === 'school' && item.id !== task?.id && item.scope !== 'work_block').map(item => `<option value="${esc(item.id)}" ${task?.assignmentId === item.id ? 'selected' : ''}>${esc(item.title)}</option>`).join('')}</select><span class="hint">Finishing a work session does not finish its linked assignment.</span></div>`);
  $('#task-scope').addEventListener('change', event => { $('#linked-assignment-field').hidden = event.target.value !== 'work_block'; if (event.target.value !== 'work_block') $('#task-assignment').value = ''; });
  $('#task-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitForm(form, async () => {
      const fields = new FormData(form);
      const body = Object.fromEntries(['subject','title','instructions','criteria','planDate','dueDate','sourceUrl','sourceNote','scope','assignmentId'].map(key => [key,String(fields.get(key) || '').trim()]));
      body.plannedMinutes = Number(fields.get('plannedMinutes') || 0);
      if (body.sourceUrl && !safeUrl(body.sourceUrl)) throw new Error('Use an http or https source link.');
      body.requirements = {understanding:fields.has('understanding'),submission:fields.has('submission'),parent:fields.has('parent')};
      await api(task ? `/api/parent/tasks/${encodeURIComponent(task.id)}` : '/api/parent/tasks', task ? 'PUT' : 'POST', body);
      dialog.close();
      await refresh(true);
      toast(task ? 'Plan changes saved.' : 'Assignment added to the plan.');
    });
  });
}

async function showDrafts() {
  const data = await api('/api/parent/drafts');
  draftItems = data.items || data.drafts || (Array.isArray(data) ? data : []);
  openDialog('Review saved-plan suggestions', `<p class="dialog-description">These suggestions come from the September 8 catch-up plan. Review the instructions and requirements before adding them. The saved observations are dated context; no completion is assumed.</p>${data.sourceNote ? `<p class="small muted">${esc(data.sourceNote)}</p>` : ''}<form id="draft-form">${draftItems.length ? draftItems.map(task => `<article class="draft-item"><label class="check-label"><input type="checkbox" name="draft" value="${esc(task.id)}" ${task.imported ? 'disabled' : 'checked'}><span>${esc(task.title)}<small>${esc(task.subject)} · Planned ${esc(task.planDate)}${task.imported ? ' · Already added' : ''}</small></span></label><p>${esc(task.instructions)}</p>${task.criteria ? `<p><strong>Finished when:</strong> ${esc(task.criteria)}</p>` : ''}<p>Required checks: ${[task.requirements?.understanding ? 'understanding' : '',task.requirements?.submission ? 'submission' : '',task.requirements?.parent ? 'parent review' : ''].filter(Boolean).join(', ') || 'no extra checks'}.</p>${task.sourceUrl ? `<p>${link(task.sourceUrl)}</p>` : ''}</article>`).join('') : '<p class="small muted">There are no new saved-plan suggestions to add.</p>'}<p class="small muted">You can edit each assignment immediately after adding it. Later work remains in the saved catch-up plan.</p><p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Close', 'close-dialog', '', 'quiet', 'type="button"')}${draftItems.some(item => !item.imported) ? '<button class="button primary" type="submit">Add selected to the plan</button>' : ''}</div></form>`);
  $('#draft-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitForm(form, async () => {
      const ids = new FormData(form).getAll('draft');
      if (!ids.length) throw new Error('Choose at least one suggestion to add.');
      await api('/api/parent/import-drafts', 'POST', {ids});
      dialog.close();
      await refresh(true);
      toast('Suggestions added. You can edit their next steps and checks.');
    });
  });
}

function rewardFormFooter(label) { return `<p class="form-error" role="alert" hidden></p><div class="dialog-footer">${button('Cancel','close-dialog','','quiet','type="button"')}<button class="button primary" type="submit">${esc(label)}</button></div>`; }

function wireRewardForm(operation) {
  $('#reward-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitForm(form, async () => { await operation(new FormData(form)); dialog.close(); await refresh(true); toast('Saved.'); });
  });
}

async function rewardDayEditor(date = state.date) {
  const data = date === state.date ? state : await api(`/api/state?date=${encodeURIComponent(date)}`);
  const rewards = data.rewards;
  const day = rewards.day;
  const expected = day.expectedSchoolIds || [];
  const choices = rewards.schoolChoices || data.tasks.filter(task => task.kind === 'school');
  const dayEntry = rewards.week.days.find(item => item.date === date);
  const ids = [...new Map([...(dayEntry?.items || []), ...choices.map(task => ({id:`school:${task.id}`,title:task.title}))].map(item => [item.id,item])).values()];
  openDialog('Review the day’s checklist', `<p class="dialog-description">${esc(dateText(date))}. Choose the schoolwork expected for this day. An unfinished assignment can stay in the overall plan; a work session counts only for its stated work. Braces and the day’s routine are included separately.</p>${day.planIssues?.length ? `<div class="notice">${day.planIssues.map(esc).join(' · ')}</div>` : ''}<form id="reward-form"><fieldset class="field" style="border:0;padding:0"><legend style="font-size:.85rem;font-weight:650">Expected schoolwork</legend>${choices.length ? choices.map(task => `<label class="check-label"><input name="school" type="checkbox" value="${esc(task.id)}" ${expected.includes(task.id) ? 'checked' : ''}><span>${esc(task.title)}<small>${task.scope === 'practice' ? 'Whole quiz' : task.scope === 'work_block' ? 'Work session' : 'Whole assignment'}${task.planDate ? ` · Planned ${esc(task.planDate)}` : ''}${task.plannedMinutes ? ` · ${esc(task.plannedMinutes)} min` : ''}</small></span></label>`).join('') : '<p class="small muted">No schoolwork has been planned for this date.</p>'}</fieldset><p class="small muted">If no schoolwork is expected, leave the list empty and explain why in the review note. Keep the expected schoolwork within 90 minutes. Both breaks are separate.</p><details class="task-detail"><summary>Record an exception for an item</summary><p class="small muted" style="margin-top:12px">Use a specific reason when an item is excused for this date. This records your planning decision without creating completion evidence.</p>${ids.map(item => `<div class="field"><label>${esc(item.title)}${item.owner === 'mom' ? ' · Parent’s chore' : ''}<input name="excuse:${esc(item.id)}" maxlength="1000" value="${esc(day.excusedItems?.[item.id] || '')}" placeholder="No exception"></label></div>`).join('')}</details><div class="field" style="margin-top:18px"><label for="review-day-note">Parent review note</label><textarea id="review-day-note" name="note" required maxlength="3000" placeholder="What is expected today, and why are any items excused?">${esc(day.note || '')}</textarea></div>${rewardFormFooter('Save daily expectations')}</form>`);
  wireRewardForm(async fields => {
    const excusedItems = {};
    for (const [key,value] of fields.entries()) if (key.startsWith('excuse:') && value.trim()) excusedItems[key.slice(7)] = value.trim();
    await api('/api/parent/rewards/day','POST',{date,expectedSchoolIds:fields.getAll('school'),excusedItems,note:fields.get('note').trim()});
  });
}

function rewardHolidaysEditor() {
  openDialog('Holiday freebies', `<p class="dialog-description">Mark a holiday to give Student a freebie day toward his weekly prize. No note needed. Future holidays count when that date arrives. Task records and the daily braces routine stay as they are.</p>${state.rewards.week.days.map(day => `<div class="checklist-day"><strong>${esc(dateText(day.date))}</strong><p class="small muted">${day.holiday ? 'Holiday · freebie day' : day.qualifying ? 'Regular qualifying day' : 'Outside the qualifying schedule'}${day.holiday && !day.qualifying ? ' · Outside the qualifying schedule' : ''}</p>${button(day.holiday ? 'Undo holiday freebie' : 'Mark holiday freebie', 'reward-holiday-toggle', '', day.holiday ? 'outline small' : 'primary small', `data-date="${esc(day.date)}" data-enabled="${!day.holiday}" aria-label="${day.holiday ? 'Undo' : 'Mark'} holiday freebie for ${esc(dateText(day.date))}"`)}</div>`).join('')}<div class="dialog-footer">${button('Done','close-dialog','','secondary')}</div>`);
}

function rewardSettingsEditor() {
  const settings = state.rewards.settings;
  const weekdays = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  openDialog('Set the prize week', `<p class="dialog-description">Choose when the week starts and which days count toward earning a prize. Daily braces stay on the plan every day. Chores follow each day’s after-school setting.</p><form id="reward-form"><div class="field"><label for="week-start">Week begins</label><select name="weekStart" id="week-start">${weekdays.map((day,index) => `<option value="${index}" ${settings.weekStart === index ? 'selected' : ''}>${day}</option>`).join('')}</select></div><fieldset class="field" style="border:0;padding:0"><legend style="font-size:.85rem;font-weight:650">Qualifying days</legend>${weekdays.map((day,index) => `<label class="check-label"><input name="qualifying" type="checkbox" value="${index}" ${settings.qualifyingDays.includes(index) ? 'checked' : ''}><span>${day}</span></label>`).join('')}</fieldset>${rewardFormFooter('Save week settings')}</form>`);
  wireRewardForm(fields => api('/api/parent/rewards/settings','POST',{weekStart:Number(fields.get('weekStart')),qualifyingDays:fields.getAll('qualifying').map(Number)}));
}

function rewardCatalogEditor(id) {
  const prize = state.rewards.catalog.find(item => item.id === id);
  openDialog(prize ? 'Edit a prize choice' : 'Add a prize choice', `<p class="dialog-description">Student chooses one prize for an earned week. Parent review remains the final redemption step.</p><form id="reward-form"><div class="field"><label for="prize-title">Prize</label><input id="prize-title" name="title" required maxlength="200" value="${esc(prize?.title || '')}"></div><div class="field"><label for="prize-description">What Student can look forward to</label><textarea id="prize-description" name="description" required maxlength="3000">${esc(prize?.description || '')}</textarea></div><div class="field"><label for="prize-kind">How it is redeemed</label><select id="prize-kind" name="kind"><option value="parent_review" ${prize?.kind !== 'chore_swap' ? 'selected' : ''}>Arrange it with the parent</option><option value="chore_swap" ${prize?.kind === 'chore_swap' ? 'selected' : ''}>Parent takes a chosen chore next week</option></select></div><label class="check-label"><input type="checkbox" name="active" ${!prize || prize.active ? 'checked' : ''}><span>Available for Student to choose</span></label>${rewardFormFooter('Save prize')}</form>`);
  wireRewardForm(fields => api('/api/parent/rewards/catalog','POST',{...(prize ? {id:prize.id} : {}),title:fields.get('title').trim(),description:fields.get('description').trim(),kind:fields.get('kind'),active:fields.has('active')}));
}

function requestPrizeEditor(prizeId, awardId) {
  const rewards = state.rewards;
  const prize = rewards.catalog.find(item => item.id === prizeId);
  if (!prize) throw new Error('That prize is no longer available. Refresh your plan.');
  openDialog('Choose your prize', `<p class="dialog-description"><strong>${esc(prize.title)}</strong><br>${esc(prize.description)}</p><form id="reward-form">${prize.kind === 'chore_swap' ? `<div class="field"><label for="swap-chore">Which chore will Parent take?</label><select id="swap-chore" name="chore" required>${(rewards.choreChoices || []).map(chore => `<option value="${esc(chore.id)}">${esc(chore.title)}</option>`).join('')}</select></div><p class="small muted">Your parent will confirm the swap and its dates before Parent takes the chore.</p>` : '<p class="small muted">Your parent will review the choice and arrange it with you.</p>'}<p class="small muted">This uses one earned week’s prize choice.</p>${rewardFormFooter('Ask for this prize')}</form>`);
  wireRewardForm(fields => api('/api/rewards/request','POST',{awardId,prizeId,...(prize.kind === 'chore_swap' ? {chore:fields.get('chore')} : {})}));
}

async function action(actionName, id = '', element) {
  const task = currentTask(id);
  const path = `/api/tasks/${encodeURIComponent(id)}`;
  const parentPath = `/api/parent/tasks/${encodeURIComponent(id)}`;
  switch (actionName) {
    case 'refresh': await refresh(true); break;
    case 'close-dialog': dialog.close(); break;
    case 'select-task': selectedId = id; render(true); main.scrollIntoView({behavior:'smooth',block:'start'}); break;
    case 'next-school': { const next = state.tasks.find(item => item.kind === 'school' && item.planDate === state.date && !item.completedAt); selectedId = next?.id || null; render(true); if (!next) toast('Your schoolwork plan will appear when your parent adds it.'); break; }
    case 'choose-another': document.getElementById('plan-heading')?.scrollIntoView({behavior:'smooth',block:'start'}); toast('Choose an open task from your plan.'); break;
    case 'report': await api(`${path}/report`,'POST',{}); selectedId=null; await refresh(true); toast('Ready report saved for your parent.'); break;
    case 'dishwasher-empty': await api(`${path}/report`,'POST',{note:'No clean dishes to put away'}); selectedId = null; await refresh(true); toast('Observation saved for your parent’s review.'); break;
    case 'complete': await api(`${path}/complete`,'POST',{}); selectedId = null; await refresh(true); toast('Finished. Your evidence is saved with the assignment.'); break;
    case 'help': noteDialog({title:'Let’s make the next step smaller.',description:'Tell the assistant where you got stuck. Save a short note here so your parent can see what help you need.',label:'Where would a little help be useful?',required:true,submit:'Save help note',path:`${path}/help`,initial:task.helpNote || ''}); break;
    case 'pause-task': noteDialog({title:'A pause is okay.',description:'Your place is saved. Take a break, or choose another task when you’re ready.',label:'Anything to remember when you return?',submit:'Save my place',path:`${path}/help`,initial:'Taking a pause. Next step: '}); break;
    case 'correct': noteDialog({title:'Make a correction.',description:'Your ready report and finished checkoff will reopen. Earlier evidence stays in the history so the assistant or your parent can review what changed.',required:true,submit:'Save correction',path:`${path}/correct`}); break;
    case 'braces-start': await timerAudio.arm(); await api('/api/braces/start','POST',{}); await refresh(true); toast('Timer started. Pause it if you take your braces off.'); break;
    case 'braces-pause': await api('/api/braces/pause','POST',{}); await refresh(true); toast('Timer paused. Your recorded time is saved.'); break;
    case 'school-start': await timerAudio.arm(); await api('/api/school-clock/start','POST',{}); await refresh(true); toast('Break resumed. Work time stays paused.'); break;
    case 'school-work':
    case 'school-break':
    case 'school-braces': await timerAudio.arm(); await api('/api/school-clock/start','POST',{kind:actionName === 'school-work' ? 'work' : actionName === 'school-break' ? 'break' : 'braces'}); await refresh(true); toast(actionName === 'school-braces' ? 'Braces break started. Schoolwork time is paused.' : actionName === 'school-work' ? 'Work timer started.' : 'Break started. Work time stays paused.'); break;
    case 'timer-sound': await timerAudio.toggle(); render(true); break;
    case 'timer-sound-test': if (!timerAudio.enabled) await timerAudio.toggle(); if (await timerAudio.arm()) { timerAudio.chime(); render(true); } else toast('Sound could not start. Check your browser audio settings.',true); break;
    case 'correct-school-time': correctSchoolTime(); break;
    case 'school-pause': await api('/api/school-clock/pause','POST',{}); await refresh(true); toast('Your schoolwork clock is paused. Your place is saved.'); break;
    case 'braces-reset': noteDialog({title:'Correct the braces timer.',description:'This starts the recorded time again at zero. The previous timer activity and your correction stay in the history.',required:true,submit:'Reset recorded time',path:'/api/braces/reset'}); break;
    case 'logout': await openToday(); if (!auth.parent) toast('Parent view locked.'); break;
    case 'review-today': reviewDate = ''; await refresh(true); break;
    case 'add-task': taskEditor(); break;
    case 'add-todo': todoEditor(); break;
    case 'edit-todo': todoEditor(task); break;
    case 'add-routine-chore': routineChoreEditor(); break;
    case 'routine-days': routineDaysManager(); break;
    case 'routine-edit-days': routineScheduleEditor(id); break;
    case 'routine-every-day':
    case 'routine-clear-days': $('#routine-mode').value='weekdays'; $('#routine-weekdays').hidden=false; for (const checkbox of document.querySelectorAll('#routine-weekdays input')) checkbox.checked=actionName==='routine-every-day'; break;
    case 'edit-task': taskEditor(task); break;
    case 'drafts': await showDrafts(); break;
    case 'move-up':
    case 'move-down': { const ids = state.tasks.filter(item => item.kind === 'school').map(item => item.id); const index = ids.indexOf(id); const to = index + (actionName === 'move-up' ? -1 : 1); if (to < 0 || to >= ids.length) break; [ids[index],ids[to]] = [ids[to],ids[index]]; await api('/api/parent/order','POST',{ids}); await refresh(true); toast('Plan order saved.'); break; }
    case 'confirm':
      await api(`${parentPath}/confirm`, 'POST', {});
      await refresh(true);
      toast(task.kind === 'todo' ? 'To-do confirmed.' : task.kind === 'chore' ? 'Chore confirmed.' : 'Schoolwork review confirmed.');
      break;
    case 'return-school': await api(`${parentPath}/return`,'POST',{}); await refresh(true); toast('Sent back to Student. Learning evidence is saved.'); break;
    case 'revoke-parent': noteDialog({title:'Correct this parent checkoff.',description:'The confirmation will be removed from the current checks. Its original record and this correction stay in the history.',required:true,submit:'Save correction',path:`${parentPath}/revoke`,body:{field:'parentConfirmed'}}); break;
    case 'revoke-evidence': noteDialog({title:'Correct the evidence record.',description:'This check will reopen. The earlier record stays in the history, together with your reason for changing it.',required:true,submit:'Reopen this check',path:`${parentPath}/revoke`,body:{field:element.dataset.field}}); break;
    case 'resolve': noteDialog({title:'Review this correction.',description:`Recorded correction: ${task.reviewHold}. Note what you reviewed. Student will still need to report the work ready again, and required checks still apply.`,required:true,submit:'Record correction review',path:`${parentPath}/resolve`}); break;
    case 'confirm-braces': await api('/api/parent/braces/confirm','POST',{date:state.date}); await refresh(true); toast('Braces hour confirmed.'); break;
    case 'revoke-braces': noteDialog({title:'Correct the braces confirmation.',description:'The parent confirmation will reopen. The earlier confirmation and your reason remain in the history.',required:true,submit:'Save correction',path:'/api/parent/braces/revoke',body:{date:state.date}}); break;
    case 'reward-catchup': await api('/api/parent/rewards/catchup','POST',{date:state.date,enabled:element.dataset.enabled==='true'}); await refresh(true); toast(element.dataset.enabled==='true' ? 'Saturday schoolwork catch-up enabled for this week.' : 'Saturday catch-up turned off for this week.'); break;
    case 'reward-day': await rewardDayEditor(element.dataset.date || state.date); break;
    case 'reward-holidays': rewardHolidaysEditor(); break;
    case 'reward-holiday-toggle': await api('/api/parent/rewards/holiday', 'POST', {date:element.dataset.date, enabled:element.dataset.enabled === 'true'}); await refresh(true); rewardHolidaysEditor(); toast(element.dataset.enabled === 'true' ? 'Holiday freebie saved.' : 'Holiday freebie removed.'); break;
    case 'reward-settings': rewardSettingsEditor(); break;
    case 'reward-catalog-add': rewardCatalogEditor(); break;
    case 'reward-catalog-edit': rewardCatalogEditor(id); break;
    case 'request-prize': requestPrizeEditor(id,element.dataset.award); break;
    case 'reward-award': await api('/api/parent/rewards/award','POST',{date:state.date}); await refresh(true); toast('Week confirmed earned.'); break;
    case 'reward-redeem': await api('/api/parent/rewards/redeem','POST',{awardId:id,requestRevision:Number(element.dataset.revision)}); await refresh(true); toast('Selected prize redeemed.'); break;
    case 'daily-history': { const data = await api(`/api/parent/history?date=${encodeURIComponent(state.date)}`); parentHistory = data.history || data.events || (Array.isArray(data) ? data : []); openDialog('Activity for the day', `<p class="dialog-description">${esc(dateText(state.date))}</p>${historyHtml(parentHistory)}<div class="dialog-footer">${button('Close', 'close-dialog', '', 'secondary')}</div>`); break; }
  }
}

document.addEventListener('click', async event => {
  const element = event.target.closest('[data-action]');
  if (!element || element.disabled || busy) return;
  event.preventDefault();
  busy = true;
  try { await action(element.dataset.action,element.dataset.id,element); } catch (error) { toast(error.message,true); } finally { busy = false; }
});

function clearPasscodes(form = $('#auth-form')) {
  if (!form) return;
  for (const input of form.querySelectorAll('input[type="password"]')) {
    input.value = '';
    input.defaultValue = '';
    delete input.dataset.freshEntry;
  }
}

function setAuthPending(value) {
  authPending = value;
  $('#today-tab').disabled = value;
  $('#parent-tab').disabled = value;
}

async function openToday() {
  if (auth.cloud) { location.assign('/student'); return; }
  if (authPending) return;
  setAuthPending(true);
  clearPasscodes();
  try {
    // Invalidate the cookie even if this tab's cached auth state is stale.
    await api('/api/auth/logout','POST',{});
    auth.parent = false;
    view = 'today'; reviewDate = '';
    await refresh(true);
  } catch(error) { toast(error.message,true); }
  finally { setAuthPending(false); }
}
$('#today-tab').addEventListener('click', openToday);
$('.brand').addEventListener('click', async event => { event.preventDefault(); await openToday(); });
$('#parent-tab').addEventListener('click', async () => { if (auth.cloud) { location.assign('/parent'); return; } if (authPending) return; clearPasscodes(); try { auth = await api('/api/auth'); view = 'parent'; reviewDate = ''; await refresh(true); } catch(error) { toast(error.message,true); } });

// Autofilled or restored values alone must never unlock the parent view.
main.addEventListener('beforeinput', event => {
  if (event.isTrusted && event.target.matches('#auth-form input[type="password"]') &&
      ['insertText', 'insertFromPaste', 'insertCompositionText'].includes(event.inputType)) {
    event.target.dataset.freshEntry = 'true';
  }
});

main.addEventListener('submit', async event => {
  if (event.target.id !== 'auth-form') return;
  event.preventDefault();
  if (authPending) return;
  const form = event.target;
  setAuthPending(true);
  try {
    await submitForm(form, async () => {
      const values = new FormData(form);
      let pin = values.get('pin');
      let confirmation = values.get('confirm');
      const fresh = $('#parent-pin', form).dataset.freshEntry === 'true';
      // Clear before validation or any network wait, including failed attempts.
      clearPasscodes(form);
      values.delete('pin'); values.delete('confirm');
      try {
        if (!fresh) throw new Error('Enter your passcode again to unlock parent review.');
        if (!/^\d{4}$/.test(pin)) throw new Error('Enter a four-digit passcode.');
        if (!auth.configured && pin !== confirmation) throw new Error('The passcodes do not match. Enter the same four numbers.');
        await api(auth.configured ? '/api/auth/login' : '/api/auth/setup','POST',{pin});
      } finally { pin = null; confirmation = null; }
      auth = await api('/api/auth');
      await refresh(true);
      toast('Parent review is open.');
    });
  } finally { clearPasscodes(form); setAuthPending(false); }
});
window.addEventListener('pagehide', () => clearPasscodes());
window.addEventListener('pageshow', () => clearPasscodes());

main.addEventListener('change', async event => {
  if (event.target.id === 'review-date') { reviewDate = event.target.value; await refresh(true); }
  if (event.target.id === 'after-school-toggle') {
    const element = event.target;
    try { await api('/api/parent/day','POST',{date:state.date,afterSchool:element.checked}); await refresh(true); toast('Routine setting saved for this date.'); } catch(error) { element.checked = !element.checked; toast(error.message,true); }
  }
});

dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
dialog.addEventListener('close', () => { $('#dialog-body').innerHTML = ''; render(true); });
window.addEventListener('focus', () => refresh());
setInterval(() => refresh(),5000);
setInterval(updateTimer,1000);
window.RiverApp = {api,escape:esc,refresh,openDialog,toast,schoolClockCard,updateSchoolClock,getState:()=>state,getView:()=>view};
(async () => { await window.RiverMemory.ready; timerAudio.enabled = window.RiverMemory.getItem('river-timer-sound') !== 'off'; try { timerAudio.heard = new Set(JSON.parse(window.RiverMemory.getItem('river-timer-heard') || '[]')); } catch {} try { auth = await api('/api/auth'); if (auth.parent && !auth.cloud) { await api('/api/auth/logout','POST',{}); auth.parent = false; } } catch {} await refresh(true); })();
