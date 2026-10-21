"""HTTP boundaries use isolated fake identities; Google signature verification is mocked only here."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.cookies import SimpleCookie
from server import create_server

CONFIG={'origin':'https://home.example.com','googleClientId':'123-test.apps.googleusercontent.com','parentEmails':['parent@example.com']}

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cookie='';self.start()
    def start(self):
        self.server=create_server(self.tmp.name,port=0,cloud_config=CONFIG)
        self.auth=self.server.store.cloud_auth
        self.auth.verifier=lambda token:self.claims
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def stop(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.server.store.db.close()
    def tearDown(self):self.stop();self.tmp.cleanup()
    def call(self,path,body=None,cookie=None,origin=CONFIG['origin'],host='home.example.com',bearer=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Host':host,'Cookie':self.cookie if cookie is None else cookie}
        if origin:headers['Origin']=origin
        if body is not None:headers.update({'Content-Type':'application/json','X-Student-Request':'1'})
        if bearer:headers['Authorization']='Bearer '+bearer
        c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
        r=c.getresponse();raw=r.read();meta=dict(r.getheaders());status=r.status;c.close()
        data=json.loads(raw) if meta.get('Content-Type','').startswith('application/json') else raw.decode()
        if 'Set-Cookie' in meta:
            jar=SimpleCookie(self.cookie);jar.load(meta['Set-Cookie']);self.cookie='; '.join(k+'='+v.value for k,v in jar.items())
        return status,data,meta
    def login(self,email='parent@example.com',**overrides):
        _,challenge,_=self.call('/api/auth/config')
        self.claims={'sub':email,'email':email,'email_verified':True,'hd':'example.com','iss':'https://accounts.google.com',
                     'aud':CONFIG['googleClientId'],'exp':time.time()+3600,'nonce':challenge['nonce'],**overrides}
        return self.call('/api/auth/google',{'credential':'x'*200,'role':'parent'})
    def student(self):
        self.login();self.assertEqual(self.call('/api/parent/accounts',{'email':'student@example.com'})[0],200)
        self.call('/api/auth/logout',{});return self.login('student@example.com')
    def test_anonymous_has_no_data_or_pin_bypass(self):
        for path in ('/api/state','/api/practice','/api/browser-memory','/api/photos/fake','/api/parent/practice','/api/assistant/state'):
            self.assertEqual(self.call(path)[0],401,path)
        for path in ('/api/auth/setup','/api/auth/login'):
            self.assertIn(self.call(path,{'pin':'1234'})[0],(401,403))
        self.assertEqual(self.call('/parent')[2]['Location'],'/login')
        self.assertEqual(self.call('/student')[2]['Location'],'/login')
        self.assertEqual(self.call('/api/state',host='attacker.example')[0],403)
    def test_parent_route_cookie_restart_and_logout(self):
        status,data,meta=self.login();self.assertEqual(status,200);self.assertEqual(data['redirect'],'/parent')
        self.assertIn('Secure;',meta['Set-Cookie']);self.assertIn('HttpOnly;',meta['Set-Cookie'])
        self.assertEqual(self.call('/')[2]['Location'],'/parent')
        self.assertEqual(self.call('/parent')[0],200)
        self.assertEqual(self.call('/api/parent/practice')[0],200)
        self.stop();self.start();self.assertEqual(self.call('/api/parent/practice')[0],200)
        old=self.cookie;self.call('/api/auth/logout',{})
        self.assertEqual(self.call('/api/parent/practice',cookie=old)[0],401)
    def test_student_cannot_promote_self_or_read_parent_data(self):
        status,data,_=self.student();self.assertEqual(status,200);self.assertEqual(data['redirect'],'/student')
        self.assertEqual(self.call('/parent')[2]['Location'],'/student')
        self.assertEqual(self.call('/api/state')[0],200)
        for path in ('/api/parent/accounts','/api/parent/practice','/api/parent/history','/api/parent/backlog','/api/parent/canvas'):
            self.assertEqual(self.call(path)[0],401,path)
        self.assertEqual(self.call('/api/parent/accounts',{'email':'attacker@example.com','role':'parent'})[0],401)
        self.assertEqual(self.call('/api/auth/setup',{'pin':'1234'})[0],403)
        self.assertEqual(self.call('/api/browser-memory',{'key':'river-answer-test','value':'draft'})[0],200)
        self.assertEqual(self.call('/api/browser-memory')[1]['values']['river-answer-test'],'draft')
    def test_invalid_claims_and_uninvited_accounts_fail(self):
        for claim in ({'aud':'other-client'}, {'iss':'attacker'}, {'email_verified':False}, {'exp':0}, {'nonce':'wrong'}):
            self.assertEqual(self.login(**claim)[0],401)
        self.assertEqual(self.login('uninvited@example.com')[0],403)
        self.login();self.assertEqual(self.call('/api/auth/google',{'credential':'x'*200})[0],401)
    def test_cross_site_posts_and_forged_sessions_fail(self):
        self.login()
        for origin in (None,'https://attacker.example'):
            self.assertEqual(self.call('/api/parent/accounts',{'email':'student@example.com'},origin=origin)[0],403)
        self.assertEqual(self.call('/api/state',cookie='__Host-unstuckery=forged')[0],401)
    def test_google_json_cookie_does_not_hide_login_or_session(self):
        _,challenge,_=self.call('/api/auth/config')
        self.claims={'sub':'parent-sub','email':'parent@example.com','email_verified':True,'hd':'example.com',
                     'iss':'https://accounts.google.com','aud':CONFIG['googleClientId'],
                     'exp':time.time()+3600,'nonce':challenge['nonce']}
        google_cookie='g_state={"i_l":0,"i_ll":1234}; '
        status,_,_=self.call('/api/auth/google',{'credential':'x'*200},cookie=google_cookie+self.cookie)
        self.assertEqual(status,200)
        self.assertEqual(self.call('/parent',cookie=google_cookie+self.cookie)[0],200)
        self.assertTrue(self.call('/api/auth',cookie=google_cookie+self.cookie)[1]['parent'])
    def test_duplicate_session_or_challenge_cookies_are_rejected(self):
        self.login()
        self.assertEqual(self.call('/api/state',cookie=self.cookie+'; __Host-unstuckery=forged')[0],401)
        self.call('/api/auth/config')
        self.assertEqual(self.call('/api/auth/google',{'credential':'x'*200},
                                  cookie=self.cookie+'; __Host-unstuckery-login=forged')[0],401)
    def test_assistant_role_preserved_without_parent_bypass(self):
        token=self.server.store.assistant_token
        self.assertEqual(self.call('/api/assistant/state',bearer=token)[0],200)
        self.assertEqual(self.call('/api/parent/accounts',bearer=token)[0],403)
        self.assertEqual(self.call('/api/auth/google',{'credential':'x'*200},bearer=token)[0],403)
    def test_replacing_student_revokes_their_existing_session(self):
        self.student();student_cookie=self.cookie
        self.cookie='';self.login();self.call('/api/parent/accounts',{'email':'new@example.com'})
        self.assertEqual(self.call('/api/state',cookie=student_cookie)[0],401)
