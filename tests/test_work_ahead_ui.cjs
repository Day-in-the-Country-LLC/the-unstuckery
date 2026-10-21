const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const practice=fs.readFileSync(path.join(__dirname,'../static/practice.js'),'utf8');

test('default task favors today and Student can explicitly choose future work',()=>{
  const earlier={id:'earlier',kind:'school',planDate:'2026-09-10'},today={id:'today',kind:'school',planDate:'2026-09-11'},future={id:'future',kind:'school',planDate:'2026-09-14'};
  const scope={state:{date:'2026-09-11',tasks:[earlier,today],schoolwork:{upcoming:[future]}},selectedId:null};
  scope.currentTask=id=>[earlier,today,future].find(t=>t.id===id);
  vm.createContext(scope);
  vm.runInContext(app.slice(app.indexOf('function chooseCurrent('),app.indexOf('function nextAction(')),scope);
  assert.equal(scope.chooseCurrent().id,'today');
  scope.selectedId='future';assert.equal(scope.chooseCurrent().id,'future');
  scope.selectedId=null;today.completedAt='now';assert.equal(scope.chooseCurrent(),undefined);
});

test('a tutor follow-up due today is treated as current schoolwork',()=>{
  const review={id:'review',kind:'school',planDate:'2026-09-10',practiceReviewDate:'2026-09-11'};
  const scope={state:{date:'2026-09-11',tasks:[review],schoolwork:{upcoming:[]}},selectedId:null};
  scope.currentTask=id=>[review].find(t=>t.id===id);
  vm.createContext(scope);
  vm.runInContext(app.slice(app.indexOf('function chooseCurrent('),app.indexOf('function nextAction(')),scope);
  assert.equal(scope.chooseCurrent().id,'review');
});

test('other schoolwork is grouped by assigned dates with open controls',()=>{
  const scope={state:{schoolwork:{earlier:[{id:'e',planDate:'2026-09-10'}],upcoming:[{id:'f',planDate:'2026-09-14'}]}},esc:String,dateText:String,taskRow:t=>`<li>${t.id}<button>Open</button></li>`};
  vm.createContext(scope);
  vm.runInContext(app.slice(app.indexOf('function otherSchoolwork('),app.indexOf('function currentTaskCard(')),scope);
  const html=scope.otherSchoolwork();
  assert.match(html,/Earlier unfinished work/);assert.match(html,/Work ahead/);
  assert.match(html,/2026-09-10/);assert.match(html,/2026-09-14/);
  assert.equal((html.match(/<button>Open/g)||[]).length,2);
});

test('quiz cards separate today, earlier unfinished and published future work',()=>{
  const q=(id,planDate,published=true,criteriaMet=false)=>({id,title:id,planDate,published,progress:{criteriaMet}});
  const scope={practice:{today:'2026-09-11',quizzes:[q('today','2026-09-11'),q('earlier','2026-09-10'),q('done','2026-09-09',true,true),q('future','2026-09-14'),q('draft','2026-09-15',false)]},esc:String,act:(a,label)=>`<button>${label}</button>`};
  vm.createContext(scope);
  vm.runInContext(practice.slice(practice.indexOf('  function quizDayCards('),practice.indexOf('  function gradingSummary(')),scope);
  const html=scope.quizDayCards();
  for(const heading of ['Extra practice · complete in this app','Earlier extra practice','Extra practice to work ahead on'])assert.ok(html.includes(heading));
  assert.doesNotMatch(html,/<strong>done|<strong>draft/);
});
