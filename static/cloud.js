'use strict';
// This script is included only by the authenticated HTTPS installation.
(async () => {
  const auth = await window.RiverApp.api('/api/auth');
  const nav = document.querySelector('nav');
  const logout = document.createElement('button');
  logout.className='nav-tab';logout.textContent='Sign out';
  logout.addEventListener('click', async () => {
    await window.RiverMemory.sync();
    const memory = window.RiverMemory.status();
    if (memory.error || memory.pending) {window.RiverApp.toast('Your draft is still saving. Please try signing out again in a moment.',true);return;}
    await window.RiverApp.api('/api/auth/logout','POST',{});
    location.assign('/login');
  });
  nav.append(logout);
  document.getElementById('parent-tab').hidden = !auth.parent;
  document.addEventListener('river:render', async event => {
    if (!auth.parent || event.detail.view !== 'parent') return;
    const host = document.createElement('section');
    host.className='card';host.id='family-access';
    host.innerHTML='<h2>Student sign-in</h2><p class="small muted">This installation holds one student’s work. Choose their personal Google account. Changing the account changes who can open that work.</p><form id="student-access-form"><label>Student’s Google email<input type="email" name="email" required autocomplete="off"></label><button class="button secondary">Save student account</button><p class="small" role="status"></p></form>';
    document.getElementById('main').append(host);
    const form = host.querySelector('form'), field = form.elements.email, status = host.querySelector('[role=status]');
    try {
      const result = await window.RiverApp.api('/api/parent/accounts');
      if (!host.isConnected) return;
      field.value = result.accounts.find(account=>account.role==='student')?.email || '';
    } catch (error) {status.textContent=error.message;}
    form.addEventListener('submit',async event=>{
      event.preventDefault();const button=form.querySelector('button');button.disabled=true;
      try {await window.RiverApp.api('/api/parent/accounts','POST',{email:field.value});status.textContent='Saved. The student can sign in at this same address.';}
      catch(error) {status.textContent=error.message;}
      finally {button.disabled=false;}
    });
  });
  await window.RiverApp.refresh(true);
})();
