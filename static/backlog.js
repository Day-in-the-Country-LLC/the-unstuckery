'use strict';
(()=>{
  const app=()=>window.RiverApp,esc=value=>app().escape(value);
  let snapshot={items:[]},host=null,version=0,search='',minutes='',status='available';
  const button=(action,label,id='',primary=false)=>`<button type="button" class="button ${primary?'primary':'outline'} small" data-backlog="${action}" data-id="${esc(id)}">${label}</button>`;
  const external=(url,label)=>url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`:'';
  function itemCards(items){
    return items.length?items.map(item=>`<article class="card review-card"><div class="section-heading"><div><h3>${esc(item.title)}</h3><p class="small muted">${esc(item.subject)} · about ${item.estimatedMinutes} minutes${item.status==='scheduled'?` · Assigned ${esc(item.assignedDate)}`:''}</p></div></div>${item.instructions?`<div class="practice-readable">${esc(item.instructions)}</div>`:''}${item.materialsUrl?`<p>${external(item.materialsUrl,'Open activity materials')}</p>`:''}<div class="actions">${item.status==='available'?button('today','Add to today',item.id,true)+button('schedule','Choose a day',item.id)+button('edit','Edit',item.id)+button('archive','Archive',item.id):item.status==='archived'?button('restore','Restore to backlog',item.id):'<p class="small muted">This activity is on the schoolwork plan. It will carry over until finished.</p>'}</div></article>`).join(''):'<p class="empty-state">No activities here yet. Save an idea or ask your planner to put one in the backlog.</p>';
  }
  function paintItems(){
    if(!host?.isConnected)return;
    const matching=snapshot.items.filter(item=>item.status===status&&(!minutes||item.estimatedMinutes<=Number(minutes))&&(!search||`${item.title} ${item.subject} ${item.instructions}`.toLowerCase().includes(search.toLowerCase())));
    host.querySelector('.backlog-items').innerHTML=matching.length||(!search&&!minutes)?itemCards(matching):'<p class="empty-state">No activities match these filters. Try another subject or a longer time.</p>';
  }
  function render(){
    host.innerHTML=`<section class="parent-section" aria-label="Activity backlog"><div class="section-heading"><div><h2>Activity backlog</h2><p class="small muted">Save practice and activities for a slow day. Nothing here is assigned or counted toward a prize until you choose a day.</p></div><div class="actions">${button('refresh','Refresh backlog')}${button('new','Save an activity','',true)}</div></div><p>${external(snapshot.driveFolderUrl,'Open Activity Backlog in Google Drive')}</p><p class="small muted">Keep worksheets and other materials in Drive, then add the file or folder link to an activity.</p><div class="form-grid"><label>Find an activity<input data-backlog-filter="search" value="${esc(search)}" placeholder="Subject, title, or topic"></label><label>Time available<select data-backlog-filter="minutes"><option value="">Any length</option>${[10,15,30,60].map(n=>`<option value="${n}" ${String(n)===minutes?'selected':''}>Up to ${n} minutes</option>`).join('')}</select></label><label>Show<select data-backlog-filter="status">${[['available','Saved for later'],['scheduled','Already assigned'],['archived','Archived']].map(([value,label])=>`<option value="${value}" ${value===status?'selected':''}>${label}</option>`).join('')}</select></label></div><div class="backlog-items"></div></section>`;
    paintItems();
  }
  async function load(){snapshot=await app().api('/api/parent/backlog');}
  async function reload(){await load();if(host?.isConnected)render();}
  async function mount(detail){
    const current=++version;
    if(detail.view!=='parent'||!detail.parent){host=null;snapshot={items:[]};return;}
    host=document.getElementById('backlog-parent');if(!host)return;
    try{const data=await app().api('/api/parent/backlog');if(current!==version||!host?.isConnected)return;snapshot=data;render();}catch(error){if(current===version&&host)host.textContent=error.message;}
  }
  function formFooter(label){return `<p class="form-error" role="alert" hidden></p><div class="dialog-footer"><button type="submit" class="button primary">${label}</button></div>`;}
  function formError(form,error){const output=form.querySelector('.form-error');output.hidden=false;output.textContent=error.message;}
  function edit(item=null){
    app().openDialog(item?'Edit saved activity':'Save an activity for later',`<form id="backlog-form"><div class="field"><label for="backlog-title">Activity</label><input id="backlog-title" name="title" required maxlength="250" value="${esc(item?.title||'')}"></div><div class="form-grid"><div class="field"><label for="backlog-subject">Subject or category</label><input id="backlog-subject" name="subject" required maxlength="100" value="${esc(item?.subject||'')}"></div><div class="field"><label for="backlog-minutes">Estimated minutes</label><input id="backlog-minutes" name="estimatedMinutes" type="number" min="1" max="90" required value="${item?.estimatedMinutes||15}"></div></div><div class="field"><label for="backlog-instructions">Instructions (optional)</label><textarea id="backlog-instructions" name="instructions" style="overflow:hidden;resize:none">${esc(item?.instructions||'')}</textarea></div><div class="field"><label for="backlog-materials">Drive file or folder link (optional)</label><input id="backlog-materials" name="materialsUrl" type="url" value="${esc(item?.materialsUrl||'')}"></div><p class="small muted">Saving puts this in the backlog, with no assignment date.</p>${formFooter('Save for later')}</form>`);
    const form=document.getElementById('backlog-form'),area=form.elements.instructions;
    const resize=()=>{area.style.height='auto';area.style.height=`${area.scrollHeight+2}px`;};resize();area.addEventListener('input',resize);
    form.addEventListener('submit',async event=>{
      event.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try{const fields=Object.fromEntries(new FormData(form));fields.estimatedMinutes=Number(fields.estimatedMinutes);if(item)fields.expectedRevision=item.revision;
        await app().api('/api/parent/backlog'+(item?'/'+item.id:''),'POST',fields);
        document.getElementById('action-dialog').close();await reload();app().toast('Saved in the backlog. Nothing scheduled yet.');
      }catch(error){formError(form,error);}finally{submit.disabled=false;}
    });
  }
  async function assign(item,date){
    await app().api(`/api/parent/backlog/${item.id}/schedule`,'POST',{date,expectedRevision:item.revision});
    await app().refresh(true);await reload();app().toast(`Activity added to ${date}.`);
  }
  function chooseDate(item){
    const today=app().getState().today;
    app().openDialog('Choose a day',`<p>${esc(item.title)} · about ${item.estimatedMinutes} minutes</p><form id="backlog-date-form"><div class="field"><label for="backlog-date">Assign on</label><input id="backlog-date" name="date" type="date" value="${today}" min="${today}" required></div><p>This becomes assigned schoolwork and counts toward that day’s plan.</p>${formFooter('Add to that day')}</form>`);
    const form=document.getElementById('backlog-date-form');form.addEventListener('submit',async event=>{event.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;try{await assign(item,new FormData(form).get('date'));document.getElementById('action-dialog').close();}catch(error){formError(form,error);}finally{submit.disabled=false;}});
  }
  document.addEventListener('click',async event=>{
    const target=event.target.closest('[data-backlog]');if(!target)return;event.preventDefault();target.disabled=true;
    try{const action=target.dataset.backlog,item=snapshot.items.find(item=>item.id===target.dataset.id);
      if(action==='new'){edit();return;}
      if(action==='refresh'){await reload();return;}
      if(!item)throw Error('Refresh the backlog to find this activity.');
      if(action==='edit')edit(item);else if(action==='schedule')chooseDate(item);else if(action==='today')await assign(item,app().getState().today);
      else{await app().api(`/api/parent/backlog/${item.id}/${action}`,'POST',{expectedRevision:item.revision});await reload();}
    }catch(error){app().toast(error.message,true);}finally{target.disabled=false;}
  });
  document.addEventListener('input',event=>{if(event.target.dataset.backlogFilter==='search'){search=event.target.value;paintItems();}});
  document.addEventListener('change',event=>{if(event.target.dataset.backlogFilter==='minutes'){minutes=event.target.value;paintItems();}if(event.target.dataset.backlogFilter==='status'){status=event.target.value;paintItems();}});
  document.addEventListener('river:render',event=>mount(event.detail));
})();
