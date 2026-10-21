// Run the real timer renderer with paused legacy and interval clock snapshots.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
function harness(clock) {
  const nodes = {};
  const scope = {state:{schoolClock:clock,braces:{elapsedSeconds:3600,running:false}},stateReceivedAt:Date.now(),Date,
    $:id=>nodes[id] ||= {dataset:{},classList:{toggle(){}},textContent:'',innerHTML:''},
    compactTime:seconds=>String(seconds),button:(label,action)=>`<button data-action="${action}">${label}</button>`};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('function currentTimerValues('),source.indexOf('function plannedTimeNote(')),scope);
  return {scope,nodes,render:()=>{scope.updateSchoolClock();return nodes['#school-clock-actions'].innerHTML;}};
}
test('a paused legacy clock offers Resume schoolwork even when a break is due',()=>{
  const h=harness({elapsedSeconds:64*60,running:false,phase:null,restComplete:false});
  assert.match(h.render(),/data-action="school-work">Resume schoolwork/);
  assert.equal(h.nodes['#school-clock-title'].textContent,'Schoolwork paused');
});
test('pausing an interval replaces Pause with a working Resume control',()=>{
  for(const kind of ['work','break','braces']) {
    const clock={elapsedSeconds:600,running:kind==='work',phase:{id:'test',kind,durationSeconds:1800,elapsedSeconds:600,running:true}};
    const h=harness(clock);
    assert.match(h.render(),/data-action="school-pause">Pause interval/);
    clock.running=false;clock.phase.running=false;
    assert.match(h.render(),new RegExp(`data-action="school-start">Resume ${kind==='work'?'schoolwork':'break'}`));
  }
});
test('finished intervals offer the next interval instead of resuming finished work',()=>{
  const h=harness({elapsedSeconds:1800,running:false,phase:{id:'done',kind:'work',durationSeconds:1800,elapsedSeconds:1800,running:false}});
  assert.match(h.render(),/Start work session 2 of 3/);
  assert.doesNotMatch(h.render(),/Resume/);
});
