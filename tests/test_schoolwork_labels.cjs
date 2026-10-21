const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const code=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const scope={URL,link:(url,label)=>label};
vm.createContext(scope);
vm.runInContext(code.slice(code.indexOf('function schoolworkKind('),code.indexOf('function currentTaskCard(')),scope);
test('practice stays in app even when its source is a graded Canvas assignment',()=>{
 const t={kind:'school',scope:'practice',sourceUrl:'https://school.instructure.com/courses/1/assignments/2'};
 assert.equal(scope.schoolworkKind(t),'practice');
 assert.match(scope.schoolworkNotice(t),/complete in this app/);
 assert.match(scope.schoolworkSource(t),/Reference material/);
});
test('teacher assignment status is unverified until receipt evidence exists',()=>{
 const t={kind:'school',scope:'assignment',sourceUrl:'https://school.instructure.com/courses/1/assignments/2'};
 assert.match(scope.schoolworkNotice(t),/has not been verified/);
 assert.match(scope.schoolworkNotice(t),/paper or another tool/);
 t.submission={at:'test'};
 assert.match(scope.schoolworkNotice(t),/confirmation is recorded/);
});
test('unknown legacy work is not asserted to be an unfinished teacher assignment',()=>{
 assert.equal(scope.schoolworkKind({kind:'school',sourceUrl:'https://www.youtube.com/watch?v=test'}),'unconfirmed');
 assert.equal(scope.schoolworkKind({kind:'chore'}),null);
});
