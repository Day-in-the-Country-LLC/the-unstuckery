const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/practice.js'),'utf8');
function render(allApproved,parentConfirmed,parent){
  const scope={esc:String,stamp:String,act:(name,label)=>`<button data-action="${name}">${label}</button>`};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  function masterySummary('),source.indexOf('  function renderMastery(')),scope);
  return scope.masterySummary({id:'quiz',mastery:{allApproved,parentConfirmed,totalAttempts:7,totalHints:2,totalTips:1,totalHelpedAttempts:2,questions:[{questionId:'q1',attempts:2,hints:2,tips:1,helpedAttempts:2,status:allApproved?'approved':'retry'}]}},parent);
}
test('quiz summary never asks the parent to perform a special quiz confirmation',()=>{
  assert.doesNotMatch(render(false,null,true),/Confirm quiz completion/);
  assert.doesNotMatch(render(true,null,true),/Confirm quiz completion/);
  assert.match(render(true,null,true),/Student can report ready/);
  assert.doesNotMatch(render(true,null,false),/Confirm quiz completion/);
  assert.doesNotMatch(render(true,{at:'saved'},true),/Confirm quiz completion/);
});
test('completion panel contains total and per-question attempt and help counts',()=>{
  const html=render(true,null,true);
  assert.match(html,/7 total attempts · 2 hints · 1 tips/);
  assert.match(html,/Q1: 2 attempts · 2 hints · 1 tips · 2 responses with help/);
  assert.doesNotMatch(html,/<(?:input|textarea)/);
});

test('focused practice page puts instructions first, keeps approved answers closed, and gives every question tutoring chat',()=>{
  let html='';
  const scope={
    aiReview:{enabled:true,ready:true,jobs:{}},
    practice:{settings:{sessionMinutes:20},tutorChats:[]},
    esc:String,stamp:String,
    dayLabel:value=>new Date(`${value}T12:00:00`).toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric'}),
    act:(name,label)=>`<button data-action="${name}">${label}</button>`,
    head:title=>`<h2>${title}</h2>`,
    open:value=>{html=value;},
    openPracticePage:value=>{html=value;}
  };
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  function masterySummary('),source.indexOf('  const emptyQuestion=')),scope);
  const questions=['q1','q4','q5','q9','q13','q14','q18'].map(id=>({questionId:id,prompt:id==='q18'?'Extra practice — complete in this app.\n\nExplain all three characters.':'Existing objective review.',status:id==='q18'?'retry':id==='q1'?'scheduled_review':'approved',revisit:id==='q1'?{status:'scheduled',dueDate:'2026-09-09'}:null,responses:[],hintHistory:[],attempts:1,hints:0,tips:0,helpedAttempts:0,writingRequirements:id==='q18'?{expectedSentenceRange:'8–12',minQualifiedSentences:8}:null}));
  scope.renderMastery({id:'quiz',taskId:'task',subject:'English',title:'Missed-question review',published:true,writingPolicy:{label:'Starting with capitals',description:'One new skill.',focus:'Start each sentence with a capital',practiceHint:'Check the first letter of each sentence.'},mastery:{questions,allApproved:false,reportedAt:null,parentConfirmed:null,totalAttempts:7,totalHints:1,totalTips:0,totalHelpedAttempts:0}});
  assert.equal((html.match(/<form class="mastery-answer"/g)||[]).length,1);
  assert.equal((html.match(/<form class="tutor-chat-form"/g)||[]).length,7);
  assert.match(html,/How this practice works/);
  assert.match(html,/Have a real conversation with the tutor/);
  assert.match(html,/work through this question with you until it makes sense/);
  assert.match(html,/returns on the next weekday—never again the same day/);
  assert.match(html,/Returns Wednesday, Sep 9/);
  assert.match(html,/will not reopen again today/);
  assert.match(html,/Writing target: 8–12 sentences/);
  assert.match(html,/at least 8 complete and useful sentences/);
  assert.match(html,/Writing support: Starting with capitals/);
  assert.match(html,/Explain all three characters/);
  assert.doesNotMatch(html,/Extra practice — complete in this app\.<\/h3>/);
  assert.ok(html.indexOf('How this practice works')<html.indexOf('Questions'));
  assert.ok(html.indexOf('Check the first letter')<html.indexOf('<form class="mastery-answer"'));
});
