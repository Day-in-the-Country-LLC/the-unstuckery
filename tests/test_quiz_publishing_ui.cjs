const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/practice.js'),'utf8');
const quiz=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/writing-quiz.json'),'utf8'));
function context(){
  const q={...quiz,id:'fixture',revision:2,published:false};
  const scope={practice:{quizzes:[q],attempts:[]},openParentQuizzes:new Set(),esc:String,act:(name,label,id)=>`<button data-practice="${name}" data-id="${id}">${label}</button>`,getApp:()=>({getState:()=>({tasks:[]})})};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  function gradingSummary('),source.indexOf('  async function showQuiz(')),scope);
  return scope;
}
test('draft exposes Publish quiz and groups seven items into two grading summaries',()=>{
  const scope=context(),html=scope.parentSummary();
  const visible=html.replace(/<details[\s\S]*?<\/details>/g,'');
  assert.match(visible,/>Publish quiz<\/button>/);
  assert.match(visible,/Short explanations · 6 questions/);
  assert.match(visible,/Extended writing · 1 question/);
  assert.match(visible,/8–12 sentences; at least 8/);
  assert.equal((html.match(/aria-label="Grading summary"/g)||[]).length,1);
  assert.doesNotMatch(html,/<(?:input|textarea)|I reviewed the source/);
  for(const q of quiz.questions){assert.ok(!html.includes(q.prompt));assert.ok(!html.includes(q.rubric));}
});
test('exact and choice summaries describe the saved grading behavior',()=>{
  const scope=context();
  const html=scope.gradingSummary({questions:[{type:'exact'},{type:'choice'}]});
  assert.match(html,/Exact answers · 1 question/);
  assert.match(html,/Multiple choice · 1 question/);
  assert.match(html,/capitalization and extra spacing are ignored/);
});
test('parent writing expectations expose automatic progression without text entry',()=>{
  const scope=context();
  scope.practice.writingCoach={level:'gentle',automatic:true,levels:{gentle:{label:'Getting ideas across',description:'Clear meaning matters.'}},progress:{responses:3,targetResponses:5,days:2,targetDays:3,nextLevel:'building'}};
  const html=scope.writingCoachSummary();
  assert.match(html,/Automatic increases on/);
  assert.match(html,/3 of 5/);
  assert.match(html,/2 of 3 days/);
  assert.match(html,/Adjust writing expectations/);
  assert.match(html,/meet the current level/);
  assert.match(html,/adds one new skill with a practice hint/);
  assert.doesNotMatch(html,/meet the next level/);
  assert.doesNotMatch(html,/<textarea/);
});
test('one publish click approves the displayed revision without opening a review dialog',async()=>{
  const scope=context(),calls=[];
  scope.api=async(...args)=>{calls.push(args);};
  scope.changed=async()=>{};
  scope.getApp=()=>({toast:()=>{}});
  scope.document={getElementById:()=>null};
  vm.runInContext(source.slice(source.indexOf('  async function action('),source.indexOf("  document.addEventListener('click'")),scope);
  await scope.action('publish','fixture');
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'/api/parent/practice/quizzes/fixture/publish');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][2])),{approved:true,expectedRevision:2});
});
