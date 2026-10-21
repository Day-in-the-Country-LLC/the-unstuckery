const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
function harness(kind='school') {
  const calls=[];
  const scope={currentTask:()=>({kind}),state:{date:'2026-09-08'},
    api:async(path,method,body)=>{calls.push({path,method,body});},refresh:async()=>{},toast(){},
    noteDialog(){throw Error('Confirmation must not ask for a note');}};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('async function action('),source.indexOf("document.addEventListener('click'")),scope);
  return {calls,click:(name,id='test',element={})=>scope.action(name,id,element)};
}
test('schoolwork, chores, and to-dos confirm with a single click and no dialog',async()=>{
  for (const kind of ['school','chore','todo']) {
    const h=harness(kind);await h.click('confirm');
    assert.equal(h.calls.length,1);
    assert.equal(h.calls[0].path,'/api/parent/tasks/test/confirm');
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].body)),{});
  }
});
test('braces and week confirmations need no text entry',async()=>{
  for (const [action,path] of [['confirm-braces','/api/parent/braces/confirm'],['reward-award','/api/parent/rewards/award']]) {
    const h=harness();await h.click(action);
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,path);
    assert.equal(h.calls[0].body.date,'2026-09-08');
  }
});
test('one-click redemption uses the exact displayed prize revision',async()=>{
  const h=harness();await h.click('reward-redeem','award-test',{dataset:{revision:'4'}});
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].path,'/api/parent/rewards/redeem');
  assert.equal(h.calls[0].body.requestRevision,4);
  assert.equal(h.calls[0].body.awardId,'award-test');
});
test('parent return and Student readiness each save without asking for text',async()=>{
  const h=harness();
  await h.click('return-school');await h.click('report');
  assert.equal(h.calls[0].path,'/api/parent/tasks/test/return');
  assert.equal(h.calls[1].path,'/api/tasks/test/report');
  for(const call of h.calls)assert.deepEqual(JSON.parse(JSON.stringify(call.body)),{});
});
test('schoolwork parent actions show readiness time and require readiness before Confirm',()=>{
  const scope={esc:String,timeText:String,button:(label,action,id,kind,extra='')=>`<button ${extra}>${label}</button>`};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('function parentSchoolActions('),source.indexOf('function parentTask(')),scope);
  const task={id:'fixture',requirements:{parent:true}};
  assert.match(scope.parentSchoolActions(task),/<button disabled>Confirm/);
  assert.doesNotMatch(scope.parentSchoolActions(task),/Send back to Student/);
  task.reportedAt='2026-09-08T16:10:00';
  const html=scope.parentSchoolActions(task);
  assert.match(html,/2026-09-08T16:10:00/);
  assert.match(html,/>Confirm/);assert.match(html,/Send back to Student/);
  assert.doesNotMatch(html,/<(?:textarea|input)/);
});
