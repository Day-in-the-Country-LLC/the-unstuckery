'use strict';
(() => {
  const app=()=>window.RiverApp;
  const esc=value=>app().escape(value);
  const modal=document.createElement('dialog');
  modal.className='practice-dialog photo-dialog';modal.setAttribute('aria-label','Assignment photo review');document.body.append(modal);
  let taskId=null, parent=false, data=null, selected=[], busy=false, preparing=false, polling=false, generation=0, requestedHelp='';
  const active=s=>['queued','creating','running'].includes(s.status);
  const date=value=>new Date(value).toLocaleString();
  const preview=(url,label)=>`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(label)}" loading="lazy"></a>`;
  function results(item,index){
    const review=item.review;
    const statuses={correct:'Looks right',retry:'Try again',unclear:'Clearer photo or context needed',guidance:'Let’s work through it'};
    return `<article class="photo-submission"><h3>${item.mode==='help'?'Help request':'Work review'} ${index+1} <span class="small muted">${esc(date(item.at))}</span></h3><div class="photo-previews">${item.photos.map((p,i)=>preview(p.url,`Photo ${i+1} from upload ${index+1}`)).join('')}</div>${item.requestedHelp?`<p><strong>Your question:</strong> ${esc(item.requestedHelp)}</p>`:''}${review?`<p>${esc(review.summary)}</p>${!review.allVisibleReviewed?'<p class="notice">Some work could not be checked. Include clearer photos or a smaller set of problems.</p>':''}<div class="photo-problems">${review.problems.map(p=>`<section class="photo-problem ${esc(p.status)}"><h4>Photo ${p.page} · ${esc(p.label.replace(/^Photo\s+\d+[, ·:]*\s*/i,''))} <span class="badge ${p.status==='correct'?'':'waiting'}">${statuses[p.status]}</span></h4><p><strong>Problem read:</strong> ${esc(p.question)}</p><p><strong>Your answer read:</strong> ${esc(p.studentAnswer||'No answer visible')}</p><p>${esc(p.feedback)}</p>${p.hint?`<p><strong>Next step:</strong> ${esc(p.hint)}</p>`:''}</section>`).join('')}</div><p class="small muted">${item.mode==='help'?'Try the next step, then upload your work for review when you’re ready.':'Feedback covers the problems visible in these photos. Your parent confirms the assignment when it’s ready.'}</p>`:`<p role="status">${esc(item.message)}</p>${item.status==='failed'&&item.tries<3&&!parent&&!data.task.completed?`<button class="button outline" data-photo-retry="${esc(item.id)}">Retry photo review</button>`:''}`}</article>`;
  }
  function render(){
    if(!data)return;
    const waiting=data.submissions.some(active);
    const history=data.submissions.map((s,i)=>({s,i})).reverse();
    const scroll=modal.scrollTop;
    modal.innerHTML=`<div class="dialog-heading"><div><h2>${esc(data.task.title)}</h2><p>Photos & feedback</p></div><button class="button secondary" data-photo-close>Close</button></div>${!parent&&!data.task.completed?`<section class="photo-upload"><h3>${history.length?'Send updated photos':'Upload your assignment'}</h3><p>Upload a problem to ask how to start, or show your answers and working for review. Help is always welcome.</p><label class="photo-file-label">Choose photos or screenshots<input id="worksheet-photos" type="file" accept="image/jpeg,image/png,image/webp" multiple ${waiting||busy||preparing?'disabled':''}></label><p class="small muted">Up to 3 photos at a time · JPG, PNG, or WebP. Include the problem numbers when sending corrections.</p><div class="photo-previews">${selected.map((p,i)=>`<figure><img src="${esc(p)}" alt="Selected photo ${i+1}"><figcaption>Photo ${i+1} <button class="button quiet small" data-photo-remove="${i}" ${busy?'disabled':''}>Remove</button></figcaption></figure>`).join('')}</div><label class="photo-question-label">Problem numbers or a question (optional)<input id="photo-help-question" maxlength="1000" placeholder="For example: How do I start problem 4?" value="${esc(requestedHelp)}" ${busy?'disabled':''}></label><p class="photo-error" role="alert"></p><div class="actions"><button class="button outline" data-photo-help ${!selected.length||waiting||busy||preparing||!data.ai.ready?'disabled':''}>Ask for help</button><button class="button primary" data-photo-send ${!selected.length||waiting||busy||preparing||!data.ai.ready?'disabled':''}>${busy?'Uploading…':preparing?'Preparing photos…':'Review my work'}</button></div><p class="small muted">Photos are saved with this assignment and sent to OpenAI when you ask for help or a review.</p>${!data.ai.ready?`<p role="status">${esc(data.ai.message)}</p>`:''}${waiting?'<p role="status">Reviewing your photos. Feedback will appear here automatically; you can close this window and return later.</p>':''}</section>`:''}<div aria-live="polite">${history.map(({s,i},n)=>n===0?results(s,i):`<details><summary>Earlier upload ${i+1} · ${esc(date(s.at))}</summary>${results(s,i)}</details>`).join('')||'<p>Your photo feedback will appear here.</p>'}</div>`;
    modal.scrollTop=scroll;
  }
  function error(message){const el=modal.querySelector('.photo-error');if(el)el.textContent=message;else app().toast(message,true);}
  async function prepare(file){
    if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw new Error('Choose JPG, PNG, or WebP. For an iPhone HEIC photo, export it as JPG first.');
    if(file.size>20*1024*1024)throw new Error('This photo is too large. Choose a file smaller than 20 MB.');
    const url=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(new Error('This photo could not be opened.'));r.readAsDataURL(file);});
    const img=new Image();img.src=url;await img.decode().catch(()=>{throw new Error('This image could not be read. Try another photo.');});
    if(!img.naturalWidth||!img.naturalHeight||img.naturalWidth*img.naturalHeight>60000000)throw new Error('Choose a smaller photo.');
    const scale=Math.min(1,2400/Math.max(img.naturalWidth,img.naturalHeight));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));
    const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);
    const jpeg=canvas.toDataURL('image/jpeg',0.9);
    if(jpeg.length>3*1024*1024*4/3)throw new Error('This photo is still too large. Crop to the worksheet and try again.');
    return jpeg;
  }
  async function load(){const id=taskId;const result=await app().api(`/api/tasks/${id}/photos`);if(id===taskId)data=result;}
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-photos-task]');if(!button)return;
    button.disabled=true;
    try{generation++;taskId=button.dataset.photosTask;parent=app().getView()==='parent';selected=[];requestedHelp='';await load();render();if(!modal.open)modal.showModal();}catch(e){app().toast(e.message,true);}finally{button.disabled=false;}
  });
  modal.addEventListener('input',event=>{if(event.target.id==='photo-help-question')requestedHelp=event.target.value;});
  modal.addEventListener('change',async event=>{
    if(event.target.id!=='worksheet-photos')return;
    const files=[...event.target.files];if(!files.length)return;
    if(selected.length+files.length>3){error('Choose up to three photos per upload. Remove one first or send another batch later.');event.target.value='';return;}
    const version=generation;preparing=true;render();
    try{const prepared=[];for(const f of files)prepared.push(await prepare(f));if(version===generation)selected.push(...prepared);}
    catch(e){if(version===generation){preparing=false;render();error(e.message);return;}}
    finally{preparing=false;}
    if(version===generation)render();
  });
  modal.addEventListener('click',async event=>{
    const target=event.target.closest('button');if(!target)return;
    if(target.hasAttribute('data-photo-close')){modal.close();return;}
    if(target.hasAttribute('data-photo-remove')){selected.splice(Number(target.dataset.photoRemove),1);render();return;}
    if(busy||preparing)return;
    if(target.hasAttribute('data-photo-send')||target.hasAttribute('data-photo-help')||target.hasAttribute('data-photo-retry')){
      busy=true;render();
      try{
        const retry=target.dataset.photoRetry;
        await app().api(`/api/tasks/${taskId}/${retry?'photo-review-retry':'photo-review'}`,'POST',retry?{attemptId:retry}:{images:selected,mode:target.hasAttribute('data-photo-help')?'help':'review',requestedHelp});
        selected=[];requestedHelp='';await load();busy=false;render();await app().refresh(true);
      }catch(e){busy=false;render();error(e.message);}
    }
  });
  modal.addEventListener('close',()=>{generation++;selected=[];requestedHelp='';});
  document.addEventListener('river:render',()=>{if(modal.open&&parent&&app().getView()!=='parent')modal.close();});
  setInterval(async()=>{
    if(!modal.open||busy||preparing||polling||!data?.submissions.some(active))return;
    polling=true;
    try{const before=JSON.stringify(data);await load();if(modal.open&&before!==JSON.stringify(data))render();}catch{}finally{polling=false;}
  },3000);
})();
