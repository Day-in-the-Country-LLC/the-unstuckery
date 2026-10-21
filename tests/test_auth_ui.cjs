// Exercise the actual authentication event handlers without using real app data.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
function harness() {
  const events = {}, calls = [];
  const pin = {value:'',defaultValue:'',dataset:{}};
  const confirm = {value:'',defaultValue:'',dataset:{}};
  const form = {id:'auth-form',querySelectorAll:()=>[pin,confirm]};
  const element = {disabled:false,addEventListener(){}};
  const scope = {
    auth:{configured:true,parent:false}, authPending:false, view:'parent',reviewDate:'',
    main:{addEventListener:(name,fn)=>{events[name]=fn;}},
    window:{addEventListener(){}},
    $:(selector)=>selector === '#auth-form' ? form : selector === '#parent-pin' ? pin : element,
    FormData:class extends Map {constructor(){super([['pin',pin.value],['confirm',confirm.value]]);}},
    submitForm:async(form,fn)=>{try{await fn();}catch(e){scope.error=e.message;}},
    toast(){},refresh:async()=>{},
    api:async(path)=>{calls.push(path); if(path === '/api/auth/login') {
      assert.equal(pin.value,''); assert.equal(confirm.value,'');
      if(scope.rejectLogin) throw Error('Wrong passcode');
    } return {configured:true,parent:true};},
  };
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('function clearPasscodes('),source.indexOf("main.addEventListener('change'")),scope);
  return {scope,pin,confirm,form,calls,submit:()=>events.submit({target:form,preventDefault(){}})};
}

test('a restored passcode cannot unlock without a fresh entry',async()=>{
  const h=harness(); h.pin.value='6153';
  await h.submit();
  assert.equal(h.pin.value,'');
  assert.equal(h.calls.includes('/api/auth/login'),false);
  assert.match(h.scope.error,/Enter your passcode again/);
});
test('success and failure clear credentials before sending the request',async()=>{
  for(const reject of [false,true]) {
    const h=harness(); h.scope.rejectLogin=reject;
    h.pin.value='6153'; h.pin.dataset.freshEntry='true'; h.confirm.value='6153';
    await h.submit();
    assert.equal(h.pin.value,''); assert.equal(h.confirm.value,'');
    assert.equal(h.pin.dataset.freshEntry,undefined);
    assert.equal(h.scope.authPending,false);
    assert.equal(h.calls.filter(p=>p === '/api/auth/login').length,1);
  }
});
test('Today revokes access even when cached parent state is false',async()=>{
  const h=harness(); h.pin.value='6153';
  await h.scope.openToday();
  assert.equal(h.pin.value,'');
  assert.deepEqual(h.calls,['/api/auth/logout']);
  assert.equal(h.scope.view,'today'); assert.equal(h.scope.auth.parent,false);
});
test('Today waits until a pending login is finished',async()=>{
  const h=harness(); h.scope.authPending=true;
  await h.scope.openToday();
  assert.deepEqual(h.calls,[]); assert.equal(h.scope.view,'parent');
});
