const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/practice.js'),'utf8');
function fixture(result){
 const removed=[],calls=[],notice={textContent:'',hidden:true};let focused=false;
 const form={dataset:{quiz:'synthetic'},elements:{answer:{value:'Same answer',focus:()=>focused=true}},querySelector:()=>notice};
 const scope={api:async(...args)=>{calls.push(args);return result;},window:{RiverMemory:{removeItem:key=>removed.push(key)}},draftKey:()=> 'saved-draft'};
 vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('  function answerFeedback('),source.indexOf("  modal.addEventListener('input',")),scope);
 return {scope,form,notice,removed,calls,focused:()=>focused,fields:new Map([['questionId','one'],['answer','Same answer']])};
}
test('duplicate stays editable with inline feedback and retains the saved draft',async()=>{
 const f=fixture({duplicate:true,message:'This answer is unchanged. Revise it first.'});
 assert.equal(await f.scope.submitMasteryAnswer(f.form,f.fields),false);
 assert.equal(f.form.elements.answer.value,'Same answer');assert.equal(f.removed.length,0);
 assert.equal(f.notice.hidden,false);assert.equal(f.notice.textContent,'This answer is unchanged. Revise it first.');assert.ok(f.focused());
 assert.equal(f.calls.length,1);assert.equal(f.calls[0][0],'/api/practice/mastery/synthetic/answer');
});
test('new answer follows the normal save flow and clears its saved draft',async()=>{
 const f=fixture({ok:true});assert.equal(await f.scope.submitMasteryAnswer(f.form,f.fields),true);
 assert.deepEqual(f.removed,['saved-draft']);assert.equal(f.notice.hidden,true);
});
test('editing the answer dismisses the duplicate message',()=>{
 const f=fixture({});f.scope.answerFeedback(f.form,'Duplicate');f.scope.answerFeedback(f.form);
 assert.equal(f.notice.hidden,true);assert.equal(f.notice.textContent,'');
});
test('a failed request preserves the draft',async()=>{
 const f=fixture({});f.scope.api=async()=>{throw new Error('Offline');};
 await assert.rejects(f.scope.submitMasteryAnswer(f.form,f.fields),/Offline/);assert.equal(f.removed.length,0);
});
