const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/practice.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/writing-quiz.json'),'utf8'));
function reader(){
  let html='',formValues=new Map();
  const scope={editor:null,esc:String,act:(name,label,id)=>`<button type="button" data-practice="${name}" data-id="${id}">${label}</button>`,head:title=>`<h2>${title}</h2>`,open:value=>{html=value;},modal:{scrollTop:0,querySelectorAll:()=>[],querySelector:()=>null},FormData:class{constructor(){return formValues;}}};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  const emptyQuestion='),source.indexOf('  async function changed()')),scope);
  scope.makeEditor({...fixture,id:'fixture'});
  return {scope,html:()=>html,edit:key=>vm.runInContext(`editingParts.add(${JSON.stringify(key)});renderEditor();`,scope),fields:rows=>{formValues=new Map(rows);}};
}
test('question reader shows complete paired text with no editable fields initially',()=>{
  const r=reader();
  assert.doesNotMatch(r.html(),/<(?:textarea|input|select)\b/);
  assert.equal((r.html().match(/>Edit question<\/button>/g)||[]).length,7);
  assert.equal((r.html().match(/>Edit answer<\/button>/g)||[]).length,7);
  for(const q of fixture.questions){assert.ok(r.html().includes(q.prompt));assert.ok(r.html().includes(q.rubric));}
  assert.doesNotMatch(r.html(),/>Save changes<\/button>/);
});
test('editing one question preserves hidden answers, sentence requirements, hints, and concept targets',()=>{
  const r=reader();r.edit('question:0');
  assert.equal((r.html().match(/<textarea/g)||[]).length,1);
  r.fields([['q0-prompt','Changed synthetic question.']]);r.scope.collectEditor();
  const expected={...fixture,id:'fixture'};expected.questions[0].prompt='Changed synthetic question.';
  assert.deepEqual(JSON.parse(JSON.stringify(r.scope.editor)),expected);
});
test('answer editing opens only that answer and keeps the associated question intact',()=>{
  const r=reader();r.edit('answer:6');
  assert.equal((r.html().match(/<textarea/g)||[]).length,1);
  assert.match(r.html(),/name="q6-rubric"/);
  r.fields([['q6-rubric','Changed synthetic answer criteria.']]);r.scope.collectEditor();
  assert.equal(r.scope.editor.questions[6].rubric,'Changed synthetic answer criteria.');
  assert.equal(r.scope.editor.questions[6].prompt,fixture.questions[6].prompt);
  assert.deepEqual(JSON.parse(JSON.stringify(r.scope.editor.questions[6].writingRequirements)),fixture.questions[6].writingRequirements);
});
