// Exercise the real renderer with two visible copies of the same saved timer.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
function harness(clock) {
  const cards = [0,1].map(()=>({nodes:{},classList:{toggle(){}},querySelector(id){return this.nodes[id] ||= {textContent:'',innerHTML:''};}}));
  const scope = {state:{schoolClock:clock,braces:{elapsedSeconds:0,running:false}},stateReceivedAt:Date.now(),Date,
    document:{querySelectorAll:()=>cards},compactTime:seconds=>String(seconds),
    button:(label,action)=>`<button data-action="${action}">${label}</button>`};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('function currentTimerValues('),source.indexOf('function plannedTimeNote(')),scope);
  return {scope,cards,render:()=>{scope.updateSchoolClock();return cards[0].nodes['[data-clock-actions]'].innerHTML;},title:()=>cards[0].nodes['[data-clock-title]'].textContent};
}
test('a paused legacy clock offers Resume work',()=>{
  const h=harness({elapsedSeconds:64*60,running:false,phase:null});
  assert.match(h.render(),/data-action="school-work">Resume work/);
  assert.equal(h.title(),'Work paused');
});
test('work changes to a break countdown between polls without spending more work time',()=>{
  const h=harness({elapsedSeconds:1799,running:true,phase:{id:'work',kind:'work',durationSeconds:1800,elapsedSeconds:1799,running:true}});
  h.scope.stateReceivedAt=Date.now()-4000;
  assert.match(h.render(),/data-action="school-work">Resume work/);
  assert.match(h.title(),/Time for a break · 597/);
  assert.equal(h.scope.currentTimerValues().budget,1800);
  for(const card of h.cards)assert.equal(card.nodes['[data-clock-title]'].textContent,h.title());
  h.scope.stateReceivedAt=Date.now()-700000;
  h.render();
  assert.equal(h.title(),'Break finished — ready when you are');
  assert.equal(h.scope.currentTimerValues().budget,1800);
});
test('a saved break stays a break after finishing and provides explicit Resume work',()=>{
  const h=harness({elapsedSeconds:3600,running:false,phase:{id:'rest',kind:'break',durationSeconds:600,elapsedSeconds:600,running:false}});
  assert.match(h.render(),/data-action="school-work">Resume work/);
  assert.doesNotMatch(h.render(),/Pause|Start work session/);
  assert.equal(h.title(),'Break finished — ready when you are');
});
test('pausing work and rest keeps the appropriate resume control',()=>{
  for(const kind of ['work','break']) {
    const clock={elapsedSeconds:600,running:kind==='work',phase:{id:'test',kind,durationSeconds:1800,elapsedSeconds:600,running:true}};
    const h=harness(clock);assert.match(h.render(),/data-action="school-pause"/);
    clock.running=false;clock.phase.running=false;
    assert.match(h.render(),kind==='work'?/data-action="school-work">Resume work/:/data-action="school-start">Resume break/);
  }
});
test('90-minute limit stays a stopping point without another work button',()=>{
  const h=harness({elapsedSeconds:5400,running:false,phase:{id:'last',kind:'work',durationSeconds:1800,elapsedSeconds:1800,running:false}});
  assert.equal(h.render(),'');
  assert.equal(h.title(),'Schoolwork is finished for today');
});
test('the practice page mounts the shared timer without replacing an existing draft',()=>{
  const practiceSource=fs.readFileSync(require('node:path').join(__dirname,'../static/practice.js'),'utf8');
  let inserted='',updates=0,restores=0;
  const scope={pageQuiz:null,modalQuiz:null,page:{innerHTML:'',hidden:true,querySelector:()=>({insertAdjacentHTML:(position,html)=>{assert.equal(position,'afterend');inserted=html;}})},
    window:{scrollY:10},document:{body:{classList:{add(){}}}},restoreDrafts:()=>restores++,
    getApp:()=>({schoolClockCard:id=>`<section id="${id}" data-school-clock>Timer</section>`,updateSchoolClock:()=>updates++}),
    requestAnimationFrame:()=>{}};
  vm.createContext(scope);
  vm.runInContext(practiceSource.slice(practiceSource.indexOf('  function openPracticePage('),practiceSource.indexOf('  function closePracticePage(')),scope);
  scope.openPracticePage('<header class="practice-page-header">Practice</header>','synthetic',false,true);
  assert.match(inserted,/practice-school-clock/);assert.equal(updates,1);assert.equal(restores,1);
});
