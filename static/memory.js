'use strict';
// Draft text and sound preferences are saved by the server and included in Drive.
window.RiverMemory = (() => {
  let values = {}, pending = {}, workspace = null, timer = null, active = null;
  let error = '';
  const allowed = key => /^river-(?:timer-sound|timer-heard|answer-[a-zA-Z0-9_-]{1,180})$/.test(key);
  async function request(method='GET', body) {
    const response = await fetch('/api/browser-memory', {method, credentials:'same-origin', cache:'no-store',
      headers:{'Content-Type':'application/json','X-Student-Request':'1'}, body:body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Your draft has not saved yet. Keep Student open.');
    return result;
  }
  function preserve() {
    if (workspace) localStorage.setItem('river-pending-'+workspace, JSON.stringify(pending));
  }
  async function load() {
    const buffered = {...pending};
    const result = await request();
    workspace = result.workspaceId;
    values = result.values;
    try { pending = JSON.parse(localStorage.getItem('river-pending-'+workspace) || '{}'); } catch { pending = {}; }
    Object.assign(pending, buffered);
    for (const [key,value] of Object.entries(pending)) {
      if (!allowed(key)) {delete pending[key];continue;}
      if (value === null) delete values[key]; else values[key] = value;
    }
  }
  async function sync() {
    if (active) return active;
    active = (async () => {
      try {
        if (!workspace) await load();
        for (const [key,value] of Object.entries(pending)) {
          await request('POST', {key,value});
          if (pending[key] === value) delete pending[key];
          preserve();
        }
        error = '';
      } catch (exc) {error = exc.message;}
    })().finally(() => {active = null;});
    return active;
  }
  function setItem(key,value) {
    if (!allowed(key)) return;
    if (value === null) delete values[key]; else values[key] = String(value);
    pending[key] = value === null ? null : String(value);
    try { preserve(); } catch { error = 'Keep Student open until this draft is saved.'; }
    clearTimeout(timer); timer = setTimeout(sync, 500);
  }
  const ready = sync();
  window.addEventListener('beforeunload', event => {
    if (Object.keys(pending).length) {sync();event.preventDefault();event.returnValue='';}
  });
  return {ready, sync, getItem:key=>values[key] ?? null, setItem, removeItem:key=>setItem(key,null),
    status:()=>({error,pending:Object.keys(pending).length})};
})();
