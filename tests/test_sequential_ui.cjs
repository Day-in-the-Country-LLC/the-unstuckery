const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/practice.js'),'utf8');
function render(states){
 let html=''; const scope={aiReview:{enabled:true,ready:true,jobs:{}},practice:{settings:{sessionMinutes:20},tutorChats:[]},esc:String,stamp:String,dayLabel:String,act:(n,l)=>`<button>${l}</button>`,head:String,open:s=>html=s,openPracticePage:s=>html=s};vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('  function masterySummary('),source.indexOf('  const emptyQuestion=')),scope);
 const questions=states.map((status,i)=>({questionId:`s${i}`,prompt:`Unique step ${i}`,status,previousAnswer:'Earlier approved writing',responses:['approved','pending','retry'].includes(status)?[{answer:`Answer ${i}`,feedback:`Feedback ${i}`}]:[],hintHistory:[]}));
 scope.renderMastery({id:'test',taskId:'task',published:true,sequential:true,subject:'Test',title:'Synthetic steps',mastery:{questions,allApproved:states.every(s=>s==='approved')}});return html;
}
test('only current instruction is visible and progress counts approvals',()=>{
 const html=render(['unanswered','locked','locked']);assert.match(html,/0 of 3 steps passed · 3 remaining/);assert.match(html,/<progress[^>]*max="3" value="0"/);assert.match(html,/Unique step 0/);assert.doesNotMatch(html,/Unique step [12]/);assert.equal((html.match(/<form class="mastery-answer"/g)||[]).length,1);assert.doesNotMatch(html,/<ul>/);
});
test('current step includes the saved tutor chat without future-step instructions',()=>{
 const html=render(['approved','unanswered','locked']);
 assert.match(html,/Ask the tutor/);assert.match(html,/What do you want help understanding/);
 assert.equal((html.match(/<form class="tutor-chat-form"/g)||[]).length,1);
 assert.match(html,/Tutoring chat for question s1/);
 assert.doesNotMatch(html,/returns on the next weekday/);
});
test('pending and retry stay on current step and remain pollable',()=>{
 for(const status of ['pending','retry']){const html=render([status,'locked']);assert.match(html,/aria-label="Quiz completion summary"/);assert.doesNotMatch(html,/Unique step 1/);assert.match(html,/Feedback 0/);assert.equal((html.match(/<form class="mastery-answer"/g)||[]).length,status==='pending'?0:1);}
});
test('approval clearly opens the next step with an empty answer and optional previous writing',()=>{
 const html=render(['approved','unanswered','locked']);assert.match(html,/1 of 3 steps passed · 2 remaining/);assert.match(html,/Current step: 2 of 3/);assert.doesNotMatch(html,/Pass — step 1/);assert.match(html,/<textarea[^>]+><\/textarea>/);assert.match(html,/Your passed response from step 1/);assert.match(html,/Use this as a starting point/);assert.match(html,/Feedback 0/);assert.match(html,/Unique step 1/);assert.doesNotMatch(html,/Unique step 2/);
});
test('all passed displays full progress and readiness without another form',()=>{
 const html=render(['approved','approved']);assert.match(html,/2 of 2 steps passed · 0 remaining/);assert.match(html,/Ready for parent review/);assert.doesNotMatch(html,/<form class="mastery-answer"/);
});

test('retry keeps the current answer and collapses history below the answer box',()=>{
 const html=render(['approved','retry','locked']);
 assert.match(html,/Current step: 2 of 3/);
 assert.match(html,/Answer 1<\/textarea>/);
 assert.doesNotMatch(html,/<details class="response-history" open/);
 assert.ok(html.indexOf('Submit another try')<html.indexOf('Earlier responses'));
});
test('using previous writing saves it as a draft and never overwrites an existing draft',()=>{
 const saved=[],notices=[];let focused=0;
 const scope={getApp:()=>({toast:s=>notices.push(s)}),window:{RiverMemory:{setItem:(...args)=>saved.push(args)}},draftKey:()=> 'draft'};
 vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('  function currentSequentialStep('),source.indexOf('  function renderMastery(')),scope);
 const form={elements:{answer:{value:'',focus:()=>focused++}}};
 scope.usePreviousResponse(form,'Previous writing');
 assert.equal(form.elements.answer.value,'Previous writing');assert.equal(saved.length,1);assert.equal(focused,1);
 form.elements.answer.value='My new draft';scope.usePreviousResponse(form,'Previous writing');
 assert.equal(form.elements.answer.value,'My new draft');assert.equal(saved.length,1);assert.equal(notices.length,1);
});
test('automatic review moves the viewport to a newly opened step but preserves it for same-step feedback',async()=>{
 for(const advances of [false,true]){
  const quiz=status=>({id:'test',sequential:true,mastery:{questions:[{questionId:'one',status},{questionId:'two',status:status==='approved'?'unanswered':'locked'}]}});
  const before=quiz('pending'),after=quiz(advances?'approved':'retry'),renders=[];
  const page={hidden:false,querySelector:selector=>selector==='form[data-submitting]'?null:true,contains:()=>false};
  const scope={pageQuiz:'test',modalQuiz:'test',page,modal:{},parentMode:false,reviewPollBusy:false,aiReview:{enabled:true,jobs:{}},practice:{quizzes:[before]},document:{activeElement:null},capturePracticeInputs:()=>[],restorePracticeInputs:()=>{},api:async path=>path==='/api/practice'?{quizzes:[after]}:{enabled:true,jobs:{}},renderMastery:(...args)=>renders.push(args)};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  function currentSequentialStep('),source.indexOf('  function usePreviousResponse(')),scope);
  vm.runInContext(source.slice(source.indexOf('  async function pollReviews('),source.indexOf('  setInterval(pollReviews')),scope);
  await scope.pollReviews();assert.equal(renders.length,1);assert.equal(renders[0][2],!advances);
 }
});
