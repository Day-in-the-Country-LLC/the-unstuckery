const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function harness(saved=new Map()) {
  let tones=0;
  class AudioContext {
    constructor(){this.state='running';this.currentTime=0;this.destination={};}
    async resume(){this.state='running';}
    createOscillator(){tones++; return {frequency:{},connect(){},start(){},stop(){},disconnect(){}};}
    createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}
  }
  const scope={window:{AudioContext},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)}};
  vm.createContext(scope);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/timer-audio.js'),'utf8'),scope);
  return {audio:new scope.window.RiverTimerAudio(),saved,tones:()=>tones};
}
test('a completion schedules one chime, including after reload',async()=>{
  const h=harness(); await h.audio.arm(); let notices=0;
  h.audio.alert('phase:test','Done',()=>notices++);
  h.audio.alert('phase:test','Done',()=>notices++);
  assert.equal(h.tones(),3); assert.equal(notices,1);
  const again=harness(h.saved); await again.audio.arm();
  again.audio.alert('phase:test','Done',()=>notices++);
  assert.equal(again.tones(),0); assert.equal(notices,1);
});
test('blocked audio keeps the pending alarm for a later user gesture',async()=>{
  const h=harness(); let notices=0;
  h.audio.alert('test','Done',()=>notices++);
  assert.equal(h.tones(),0);
  await h.audio.arm();
  h.audio.alert('test','Done',()=>notices++);
  assert.equal(h.tones(),3); assert.equal(notices,1);
});
test('muting still shows completion and does not replay it on unmute',async()=>{
  const h=harness(); await h.audio.arm(); await h.audio.toggle();
  let notices=0; h.audio.alert('test','Done',()=>notices++);
  assert.equal(h.tones(),0); assert.equal(notices,1);
  await h.audio.toggle(); h.audio.alert('test','Done',()=>notices++);
  assert.equal(h.tones(),0); assert.equal(notices,1);
});
