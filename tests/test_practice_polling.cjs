const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/practice.js'),'utf8');
const helpers=source.slice(source.indexOf('  function capturePracticeInputs('),source.indexOf('  setInterval(pollReviews'));
function fixture(){
 const scope={pageQuiz:'test',modalQuiz:'test',parentMode:false,reviewPollBusy:false,modal:{},document:{activeElement:null}};
 const forms=[];
 const form=(name,value,question='one')=>{
  const field={name,value,selectionStart:2,selectionEnd:5,selectionDirection:'forward',scrollTop:12,
   matches:()=>true,focus:()=>scope.document.activeElement=field,setSelectionRange:(start,end,direction)=>Object.assign(field,{selectionStart:start,selectionEnd:end,selectionDirection:direction})};
  return {dataset:{quiz:'test'},elements:{[name]:field,questionId:{value:question}}};
 };
 const quiz=status=>({id:'test',sequential:true,mastery:{questions:[{questionId:'one',status}]}});
 Object.assign(scope,{
  page:{hidden:false,contains:()=>true,querySelector:selector=>selector==='form[data-submitting]'?null:true,querySelectorAll:()=>forms},
  practice:{quizzes:[quiz('pending')]},aiReview:{enabled:true,jobs:{}},
  api:async path=>path==='/api/practice'?{quizzes:[quiz('retry')]}:{enabled:true,jobs:{}},
  currentSequentialStep:q=>q.mastery.questions[0].questionId,
  renderMastery:()=>{forms.splice(0,forms.length,form('answer','Saved answer'),form('message',''));}
 });
 vm.createContext(scope);vm.runInContext(helpers,scope);
 return {scope,forms,form,quiz};
}
test('review updates while tutor field is focused and preserves unsent text and caret',async()=>{
 const {scope,forms,form}=fixture();
 const tutor=form('message','My unsent tutor question');forms.push(tutor);scope.document.activeElement=tutor.elements.message;
 await scope.pollReviews();
 assert.equal(scope.practice.quizzes[0].mastery.questions[0].status,'retry');
 assert.equal(forms[1].elements.message.value,'My unsent tutor question');
 assert.equal(scope.document.activeElement,forms[1].elements.message);
 assert.equal(scope.document.activeElement.selectionStart,2);assert.equal(scope.document.activeElement.selectionEnd,5);
 assert.equal(scope.document.activeElement.scrollTop,12);assert.equal(scope.reviewPollBusy,false);
});
test('review updates preserve an answer being edited, including an intentionally emptied answer',async()=>{
 for(const draft of ['My unfinished revision','']){
  const {scope,forms,form}=fixture();const answer=form('answer',draft);forms.push(answer);scope.document.activeElement=answer.elements.answer;
  await scope.pollReviews();assert.equal(forms[0].elements.answer.value,draft);assert.equal(scope.document.activeElement,forms[0].elements.answer);
 }
});
test('a completed step never copies its unsent fields into a different step',async()=>{
 const {scope,forms,form,quiz}=fixture();forms.push(form('message','Old step question'));
 scope.api=async path=>path==='/api/practice'?{quizzes:[quiz('approved')]}:{enabled:true,jobs:{}};
 scope.renderMastery=()=>forms.splice(0,forms.length,form('answer','','two'),form('message','','two'));
 await scope.pollReviews();assert.equal(forms[0].elements.answer.value,'');assert.equal(forms[1].elements.message.value,'');
});
test('poll does not interfere with an in-flight answer submission',async()=>{
 const {scope}=fixture();let calls=0;scope.api=async()=>{calls++;};
 scope.page.querySelector=()=>true;
 await scope.pollReviews();assert.equal(calls,0);
});
test('poll ignores results if a submission starts while it is fetching',async()=>{
 const {scope}=fixture();let finish,rendered=false,submitting=false;
 scope.page.querySelector=selector=>selector==='form[data-submitting]'?submitting:true;
 scope.renderMastery=()=>rendered=true;
 const api=scope.api;scope.api=path=>new Promise(resolve=>{if(path==='/api/practice')finish=()=>api(path).then(resolve);else api(path).then(resolve);});
 const poll=scope.pollReviews();submitting=true;await finish();await poll;
 assert.equal(rendered,false);assert.equal(scope.reviewPollBusy,false);
});
test('background app refresh cannot consume an open practice page review update',async()=>{
 const {scope}=fixture();let calls=0;
 scope.getApp=()=>({});scope.document.getElementById=()=>({isConnected:true});scope.api=async()=>{calls++;};
 vm.runInContext(source.slice(source.indexOf('  async function mount('),source.indexOf('  function quizDayCards(')),scope);
 await scope.mount({view:'today',parent:false});assert.equal(calls,0);
 assert.equal(scope.practice.quizzes[0].mastery.questions[0].status,'pending');
});
test('background refresh started before practice opened cannot replace its current snapshot',async()=>{
 const {scope,quiz}=fixture();const resolvers=[];
 Object.assign(scope,{pageQuiz:null,mountId:0,loading:false,getApp:()=>({})});
 scope.document.getElementById=()=>({isConnected:true});scope.api=()=>new Promise(resolve=>resolvers.push(resolve));
 vm.runInContext(source.slice(source.indexOf('  async function mount('),source.indexOf('  function quizDayCards(')),scope);
 const mounting=scope.mount({view:'today',parent:false});scope.pageQuiz='test';
 resolvers[0]({quizzes:[quiz('retry')]});resolvers[1]({enabled:true,jobs:{}});await mounting;
 assert.equal(scope.practice.quizzes[0].mastery.questions[0].status,'pending');
});
