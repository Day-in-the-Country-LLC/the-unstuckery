const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/memory.js'),'utf8');
function client(server, local=new Map()){
  let fail=false;
  const scope={window:{addEventListener:()=>{}}, localStorage:{getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v)},
    setTimeout:()=>1,clearTimeout:()=>{},fetch:async(url,options)=>{
      if(fail)throw new Error('Synthetic connection interruption');
      if(options.method==='POST'){
        const {key,value}=JSON.parse(options.body);
        if(value===null)delete server.values[key];else server.values[key]=value;
        return {ok:true,json:async()=>({ok:true})};
      }
      return {ok:true,json:async()=>JSON.parse(JSON.stringify(server))};
    }};
  vm.runInNewContext(source,scope);
  return {memory:scope.window.RiverMemory, fail:value=>{fail=value;},local};
}
test('drafts and sound preference appear on a second laptop and draft removal persists',async()=>{
  const server={workspaceId:'synthetic',values:{}};
  const a=client(server);await a.memory.ready;
  await a.memory.sync();await a.memory.sync(); // Idle refreshes must not stall later writes.
  a.memory.setItem('river-answer-session-question','Unfinished response');
  a.memory.setItem('river-timer-sound','off');await a.memory.sync();
  const b=client(server);await b.memory.ready;
  assert.equal(b.memory.getItem('river-answer-session-question'),'Unfinished response');
  assert.equal(b.memory.getItem('river-timer-sound'),'off');
  b.memory.removeItem('river-answer-session-question');await b.memory.sync();
  const c=client(server);await c.memory.ready;
  assert.equal(c.memory.getItem('river-answer-session-question'),null);
});
test('failed saves remain queued across a browser restart and cannot leak into another workspace',async()=>{
  const server={workspaceId:'one',values:{}};
  const a=client(server);await a.memory.ready;a.fail(true);
  a.memory.setItem('river-answer-session-question','Keep this draft');await a.memory.sync();
  assert.equal(a.memory.status().pending,1);assert.ok(a.memory.status().error);
  const other=client({workspaceId:'two',values:{}},a.local);await other.memory.ready;
  assert.equal(other.memory.getItem('river-answer-session-question'),null);
  const resumed=client(server,a.local);await resumed.memory.ready;
  assert.equal(server.values['river-answer-session-question'],'Keep this draft');
  assert.equal(resumed.memory.status().pending,0);
});
