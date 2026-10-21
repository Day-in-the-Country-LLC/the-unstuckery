'use strict';
(() => {
  const app = () => window.RiverApp;
  const esc = value => app().escape(value);
  let host = null, snapshot = null, epoch = 0, poll = null;
  let search = '', course = '', filter = 'unplanned';
  const callbackNotice = location.hash.startsWith('#canvas-') ? location.hash : '';
  let callbackShown = false;
  const post = (action, body = {}) => app().api('/api/parent/canvas/' + action, 'POST', body);
  const btn = (action, label, key = '', primary = false, disabled = false) => `<button type="button" class="button ${primary ? 'primary' : 'outline'} small" data-canvas="${action}" data-key="${esc(key)}" ${disabled ? 'disabled' : ''}>${label}</button>`;
  const dateTime = value => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString('en-US', {timeZone:app().getState()?.timezone || 'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'No date listed';
  const dayOf = value => value && !Number.isNaN(Date.parse(value)) ? new Intl.DateTimeFormat('en-CA', {timeZone:app().getState()?.timezone || 'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value)) : '';
  const link = (url, label) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
  function receipt(s) {
    if (!s) return 'Submission status unavailable';
    const parts = [];
    if (s.submitted_at) parts.push('Submitted ' + dateTime(s.submitted_at));
    else if (s.workflow_state === 'unsubmitted') parts.push('No submission recorded');
    else parts.push('Submission time not listed');
    if (s.missing === true) parts.push('Marked missing in Canvas');
    if (s.late === true) parts.push('Marked late');
    if (s.excused === true) parts.push('Excused');
    if (s.score !== null && s.score !== undefined) parts.push('Canvas score: ' + s.score);
    return parts.join(' · ');
  }
  function matching(item) {
    return (!course || item.courseId === course) && (!search || `${item.title} ${item.course} ${item.instructions}`.toLowerCase().includes(search.toLowerCase())) &&
      (filter === 'all' || filter === 'unplanned' && !item.plannedTasks.length || filter === 'planned' && item.plannedTasks.length ||
       filter === 'attention' && (item.submission?.missing === true || item.submission?.workflow_state === 'unsubmitted'));
  }
  function paintItems() {
    if (!host?.isConnected || !snapshot) return;
    const items = snapshot.items.filter(matching).sort((a,b) => (a.dueAt || '9999').localeCompare(b.dueAt || '9999'));
    const target = host.querySelector('.canvas-items');
    if (!target) return;
    target.innerHTML = items.length ? items.map(item => `<article class="card canvas-assignment"><div><p class="eyebrow">${esc(item.course)}</p><h3>${esc(item.title)}</h3><p class="small"><strong>${item.dueAt ? 'Due ' + esc(dateTime(item.dueAt)) : 'No due date listed'}</strong>${item.dueBasis === 'course' ? ' · Check Student’s deadline' : ''}</p><p class="small muted">${esc(receipt(item.submission))}</p><div class="badge-list">${item.plannedTasks.length ? '<span class="badge neutral">Already in the plan</span>' : ''}${item.changedSinceImport ? '<span class="badge waiting">Changed since added</span>' : ''}${!item.available ? '<span class="badge waiting">Earlier record · refresh unavailable</span>' : ''}${item.locked ? '<span class="badge neutral">Locked in Canvas</span>' : ''}</div></div><div class="actions">${btn('review',item.plannedTasks.length ? 'View assignment' : 'Review & plan',item.key,!item.plannedTasks.length)}${link(item.sourceUrl,'Open Canvas')}</div></article>`).join('') : `<p class="empty-state">${snapshot.lastSyncAt ? 'No assignments match these filters. Try All assignments or another course.' : 'Connect your account, then refresh to see Canvas assignments here.'}</p>`;
    host.querySelector('.canvas-count').textContent = `${items.length} of ${snapshot.items.length} assignments`;
  }
  function render() {
    if (!host?.isConnected || !snapshot) return;
    const connected = Object.values(snapshot.accounts).some(a => a.connected);
    host.innerHTML = `<section class="parent-section canvas-section" aria-label="Canvas schoolwork"><div class="section-heading"><div><p class="eyebrow">School planning</p><h2>Canvas schoolwork</h2><p class="small muted">Review the school’s instructions, then choose what belongs in Student’s day.</p></div><div class="actions">${btn('settings','Connections')}${btn('refresh','Refresh Canvas','',true,!connected || !snapshot.student || snapshot.sync.running)}</div></div><div class="canvas-connection-line"><span>${snapshot.student ? 'Planning for ' + esc(snapshot.student.name) : 'No student selected'}</span><span>${snapshot.lastSyncAt ? 'Last read ' + esc(dateTime(snapshot.lastSyncAt)) : 'No Canvas refresh yet'}</span></div>${!connected ? `<div class="card canvas-welcome"><h3>Bring assignments into the plan.</h3><p>Connect your parent account to start a personal test. A school-approved connection can also add Student’s student account.</p>${btn('test','Connect parent account','',true)}</div>` : ''}${callbackNotice ? `<p class="inline-note">${callbackNotice === '#canvas-connected' ? 'Canvas sign-in completed. Refresh to read the school records.' : 'Canvas sign-in was not completed. Check your account and app settings, then try again.'}</p>` : ''}<p class="canvas-sync small ${snapshot.sync.error ? 'form-error' : 'muted'}" role="status">${esc(snapshot.sync.message)}</p>${snapshot.settingsError ? `<p role="alert">${esc(snapshot.settingsError)}</p>` : ''}${snapshot.warnings.map(w => `<p class="inline-note">${esc(w)}</p>`).join('')}${connected && !snapshot.student ? `<label class="field">Linked student<select id="canvas-student"><option value="">Choose Student…</option>${(snapshot.observees || []).map(u => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join('')}</select></label>` : ''}<div class="canvas-filters"><label>Find an assignment<input data-canvas-filter="search" value="${esc(search)}" placeholder="Title, topic, or instructions"></label><label>Course<select data-canvas-filter="course"><option value="">All courses</option>${snapshot.courses.map(c => `<option value="${esc(c.id)}" ${c.id === course ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label><label>Show<select data-canvas-filter="status">${[['unplanned','Not yet in the plan'],['all','All assignments'],['attention','Missing or unsubmitted'],['planned','Already in the plan']].map(([value,label]) => `<option value="${value}" ${value === filter ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div><p class="small muted canvas-count"></p><div class="canvas-items"></div><p class="small muted">Canvas records are read when you refresh. A receipt shows what Canvas recorded; you still review the work with Student. PowerSchool, paper work, course pages, and external learning tools need their own review.</p></section>`;
    paintItems();
  }
  async function load() {
    const version = epoch;
    const data = await app().api('/api/parent/canvas');
    if (version !== epoch || !host?.isConnected) return;
    const wasRunning = snapshot?.sync.running;
    snapshot = data;
    if (wasRunning && data.sync.running && host.querySelector('.canvas-sync')) host.querySelector('.canvas-sync').textContent = data.sync.message;
    else render();
    clearTimeout(poll);
    if (data.sync.running) poll = setTimeout(() => load().catch(e => app().toast(e.message, true)), 1500);
  }
  function footer(label) { return `<p class="form-error" role="alert" hidden></p><div class="dialog-footer"><button class="button primary" type="submit">${label}</button></div>`; }
  function wire(form, action) {
    form.addEventListener('submit', async event => {
      event.preventDefault(); const submit = form.querySelector('[type=submit]'); submit.disabled = true;
      const fields = new FormData(form);
      form.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
      try { await action(fields); } catch (e) { const error = form.querySelector('.form-error'); error.hidden = false; error.textContent = e.message; }
      finally { submit.disabled = false; }
    });
  }
  function close() { document.getElementById('action-dialog').close(); }
  function testConnection() {
    app().openDialog('Connect your parent account', `<p>This first connection is a personal API test using your own parent account. It reads assignments and submission records when you request them.</p><ol><li>Open ${link((snapshot?.origin || '') + '/profile/settings','Canvas account settings')} and check that your name is shown.</li><li>Under Approved Integrations, create a New Access Token for “Student planning test” with an expiration date.</li><li>Enter it privately below. It is stored privately by your installation.</li></ol><form id="canvas-test-form" autocomplete="off"><div class="field"><label for="canvas-token">Your parent Canvas token</label><input id="canvas-token" name="token" type="password" autocomplete="off" required maxlength="8000"></div><label class="check-label"><input name="ownAccount" type="checkbox" required><span>This token belongs to my own parent account.</span></label><p class="small muted">For Student’s separate account, use the school-approved sign-in under Connections. Keep tokens out of chat.</p>${footer('Connect & check account')}</form>`);
    wire(document.getElementById('canvas-test-form'), async fields => {
      await post('test-connect', {token: fields.get('token'), ownAccount: fields.has('ownAccount')}); close(); await load(); app().toast('Parent account connected. Refresh Canvas when you are ready.');
    });
  }
  function settings() {
    app().openDialog('Canvas connections', `<p>Each account keeps its own access. Refresh uses Student’s student connection when available, otherwise the parent connection.</p>${['parent','student'].map(role => {const a = snapshot.accounts[role]; return `<div class="canvas-account"><h3>${role === 'parent' ? 'Parent account' : 'Student’s student account'}</h3><p>${a ? esc(a.name) + (a.connected ? ' · Connected' : ' · Reconnect needed') + (a.mode === 'personal_test' ? ' · Personal test' : '') : 'Not connected'}</p><div class="actions">${role === 'parent' ? btn('test','Personal test connection') : ''}${btn('oauth','Sign in with Canvas',role,false,!snapshot.oauthConfigured || role === 'student' && !snapshot.student)}${a?.connected ? btn('disconnect','Disconnect from this app',role) : ''}</div></div>`;}).join('')}<details class="task-detail"><summary>School-approved app setup</summary><p>Your school’s Canvas administrator supplies the application client ID and secret. Register this return address:</p><p class="canvas-url">${esc(location.origin + '/api/canvas/oauth/callback')}</p><p>Use the read-only scopes listed in CANVAS-SETUP.md. This enables separate parent and student sign-ins.</p><form id="canvas-oauth-form" autocomplete="off"><div class="field"><label for="canvas-client">Application client ID</label><input id="canvas-client" name="clientId" required autocomplete="off"></div><div class="field"><label for="canvas-secret">Application client secret</label><input id="canvas-secret" name="clientSecret" type="password" required autocomplete="off"></div>${footer('Save app connection settings')}</form></details><p class="small muted">Disconnect removes this app’s local credential. Revoke it in ${link((snapshot?.origin || '') + '/profile/settings','Canvas settings')} to end its authorization there. Imported work and dated source records remain available.</p>`);
    wire(document.getElementById('canvas-oauth-form'), async fields => { await post('oauth-config', Object.fromEntries(fields)); close(); await load(); app().toast('App settings saved. You can sign in with Canvas.'); });
  }
  function review(item) {
    const reviewedRevision = snapshot.revision;
    const today = app().getState().today;
    const chosen = app().getState().date >= today ? app().getState().date : today;
    const text = item.instructions.length <= 4000 ? item.instructions : 'Open the full Canvas instructions and work through the next unfinished part. Review the required components with your parent.';
    app().openDialog(item.title, `<p class="dialog-description">${esc(item.course)} · ${link(item.sourceUrl,'Open original assignment')}</p><p><strong>${item.dueAt ? 'Due ' + esc(dateTime(item.dueAt)) : 'No due date listed'}</strong>${item.dueBasis === 'course' ? ' · This is the course deadline. Check whether Student has a different date.' : ''}</p><p class="small muted">Read ${esc(dateTime(item.fetchedAt))} through the ${esc(item.accountRole)} account.${item.lockAt ? ' Availability ends ' + esc(dateTime(item.lockAt)) + '.' : ''}</p>${!item.available ? `<p class="inline-note">${esc(item.unavailableReason)}</p>` : ''}<details class="task-detail" open><summary>Teacher’s instructions</summary><div class="canvas-instructions">${esc(item.instructions || 'No written instructions were returned. Open the Canvas assignment for details.')}</div>${item.links.length ? `<ul class="canvas-links">${item.links.map((url,i) => `<li>${link(url,'Assignment resource ' + (i+1))}</li>`).join('')}</ul>` : ''}<p class="small muted">Submission options: ${esc(item.submissionTypes.map(t=>t.replaceAll('_',' ')).join(', ') || 'Not listed')}.</p></details><div class="canvas-receipt"><h3>Canvas submission record</h3><p>${esc(receipt(item.submission))}</p><div id="canvas-fresh-receipt"></div>${btn('submission','Check receipt & feedback',item.key,false,!item.available)}<p class="small muted">This does not check the contents of the submitted work or complete the app task.</p></div>${item.plannedTasks.length ? `<p class="inline-note">Already in the plan: ${item.plannedTasks.map(t => esc(t.title) + ' (' + esc(t.date) + ')').join(', ')}. Use Schoolwork plan to edit its next steps.${item.changedSinceImport ? ' Canvas changed after it was added; compare the instructions before editing.' : ''}</p>` : !item.available ? '' : `<form id="canvas-plan-form"><h3>Choose Student’s next step</h3><div class="form-grid"><div class="field"><label for="canvas-title">Task title</label><input id="canvas-title" name="title" required maxlength="250" value="${esc(item.title.slice(0,250))}"></div><div class="field"><label for="canvas-subject">Subject</label><input id="canvas-subject" name="subject" required maxlength="100" value="${esc(item.course.slice(0,100))}"></div><div class="field"><label for="canvas-day">Plan for</label><input id="canvas-day" name="planDate" type="date" min="${today}" value="${chosen}" required></div><div class="field"><label for="canvas-minutes">Planned minutes</label><input id="canvas-minutes" name="plannedMinutes" type="number" min="1" max="90" value="25" required></div><div class="field"><label for="canvas-scope">This task covers</label><select id="canvas-scope" name="scope"><option value="assignment">The whole assignment</option><option value="work_block">One work session</option></select></div><div class="field"><label for="canvas-due">Due date (check against Student’s deadline)</label><input id="canvas-due" name="dueDate" type="date" value="${esc(dayOf(item.dueAt))}"></div></div><div class="field"><label for="canvas-next">Instructions for this task</label><textarea id="canvas-next" name="instructions" required maxlength="4000">${esc(text || 'Open the Canvas assignment and check the teacher’s requirements with your parent before starting.')}</textarea></div><div class="field"><label for="canvas-criteria">Finished when</label><textarea id="canvas-criteria" name="criteria" maxlength="4000" required>Review the required work with your parent, then report ready for parent review.</textarea></div><p class="small muted">For one work session, describe its next step and stopping point above. Student’s daily schoolwork budget is 90 minutes. This task uses the usual ready → parent confirmation flow.</p><p id="canvas-budget-note" class="inline-note"></p>${footer('Add to Student’s plan')}</form>`}`);
    const form = document.getElementById('canvas-plan-form');
    if (!form) return;
    function budget() {
      const day = form.elements.planDate.value;
      const planned = app().getState().tasks.filter(t => t.kind === 'school' && !t.completedAt && t.planDate <= day).reduce((sum,t)=>sum + (t.plannedMinutes || 0),0);
      const total = planned + Number(form.elements.plannedMinutes.value || 0);
      document.getElementById('canvas-budget-note').textContent = `Visible unfinished work plus this task: about ${total} minutes. ${total > 90 ? 'Choose a manageable session or spread the work over days.' : 'Leave room for review within the 90-minute day.'}`;
    }
    form.addEventListener('input',budget); budget();
    wire(form, async fields => { const task = Object.fromEntries(fields); task.plannedMinutes = Number(task.plannedMinutes);
      const result = await post('import',{key:item.key,revision:reviewedRevision,task}); close(); await app().refresh(true); await load(); app().toast(result.alreadyPlanned ? 'This assignment is already in the plan.' : 'Canvas assignment added to Student’s plan.'); });
  }
  document.addEventListener('click', async event => {
    const target = event.target.closest('[data-canvas]'); if (!target) return;
    event.preventDefault(); target.disabled = true;
    try {
      const action = target.dataset.canvas, key = target.dataset.key;
      if (action === 'settings') settings();
      else if (action === 'test') testConnection();
      else if (action === 'review') review(snapshot.items.find(i=>i.key===key));
      else if (action === 'refresh') { await post('sync'); await load(); }
      else if (action === 'disconnect') { await post('disconnect',{role:key}); close(); await load(); app().toast('Local connection removed.'); }
      else if (action === 'oauth') { const result = await post('oauth-start',{role:key}); location.assign(result.url); }
      else if (action === 'submission') {
        const result = await post('submission',{key});
        const output = document.getElementById('canvas-fresh-receipt');
        if (output) output.innerHTML = `<p><strong>${esc(receipt(result.submission))}</strong></p><p class="small muted">Checked ${esc(dateTime(result.observedAt))}</p>${result.submission.comments.map(c=>`<blockquote><p>${esc(c.text)}</p><cite>${esc(c.author)} · ${esc(dateTime(c.at))}</cite></blockquote>`).join('')}${result.submission.files.map(f=>`<p>${link(f.url || snapshot.items.find(i=>i.key===key).sourceUrl,f.name)}</p>`).join('')}`;
      }
    } catch (e) { app().toast(e.message, true); }
    finally { target.disabled = false; }
  });
  document.addEventListener('input', event => {if (event.target.dataset.canvasFilter === 'search') { search=event.target.value; paintItems(); }});
  document.addEventListener('change', async event => {
    if (event.target.dataset.canvasFilter === 'course') {course=event.target.value; paintItems();}
    if (event.target.dataset.canvasFilter === 'status') {filter=event.target.value; paintItems();}
    if (event.target.id === 'canvas-student') {try {await post('student',{id:event.target.value}); await load();} catch(e) {app().toast(e.message,true);}}
  });
  document.addEventListener('river:render', async event => {
    epoch++; clearTimeout(poll);
    if (!event.detail.parent || event.detail.view !== 'parent') {
      host=null; snapshot=null;
      if (callbackNotice && !callbackShown) {callbackShown=true; setTimeout(()=>document.getElementById('parent-tab').click(),0);}
      return;
    }
    host=document.getElementById('canvas-parent'); if (!host) return;
    try {await load();} catch(e) {if(host?.isConnected) host.textContent=e.message;}
  });
})();
