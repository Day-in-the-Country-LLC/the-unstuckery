"""Synthetic worksheet uploads; all stores and network responses are disposable."""
import base64
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from server import RiverStore, AppError, create_server
from ai_review import ReviewWorker, jobs, AgentError
from photos import decode_image, view, image_bytes
from test_ai_review import FakeClient

JPEG=Path(__file__).with_name('photo_fixture.jpg').read_bytes()
IMAGE='data:image/jpeg;base64,'+base64.b64encode(JPEG).decode()
REVIEW={'summary':'Check problem 2 again.','allVisibleReviewed':True,'problems':[
    {'page':1,'label':'1','question':'7 + 5','studentAnswer':'12','status':'correct','feedback':'The sum is right.','hint':''},
    {'page':1,'label':'2','question':'6 x 4','studentAnswer':'20','status':'retry','feedback':'Check your multiplication.','hint':'Count six groups of four.'}]}

class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=RiverStore(self.tmp.name)
        self.task=self.store.transaction(lambda:self.store.create_task({'title':'Synthetic worksheet','subject':'Math','instructions':'Show your work.'}))
        p=Path(self.tmp.name)/'private.env';p.write_text('OPENAI_API_KEY=synthetic-key\n');p.chmod(0o600)
        self.fake=FakeClient();self.worker=ReviewWorker(self.store,p,lambda key:self.fake)
        self.store.ai_review_enabled=True;self.store.ai_review_worker=self.worker
        self.fake.session['required_actions'][0].update(name='save_photo_review',arguments=copy.deepcopy(REVIEW))
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def call(self,body=None,actor='river'):
        return self.store.transaction(lambda:self.store.mutate('/api/tasks/'+self.task['id']+'/photo-review',body or {'images':[IMAGE]},actor))
    def test_persists_images_separately_and_review_does_not_complete_task(self):
        self.call();self.worker.step();self.worker.step()
        result=view(self.store,self.task['id']);item=result['submissions'][0]
        self.assertEqual(item['review']['problems'][1]['status'],'retry')
        self.assertEqual(item['status'],'completed')
        self.assertNotIn('base64',json.dumps(self.store.state))
        self.assertEqual(image_bytes(self.store,item['photos'][0]['id']),JPEG)
        task=self.store.task(self.task['id']);self.assertIsNone(task['reportedAt']);self.assertIsNone(task['completedAt']);self.assertIsNone(task['understanding'])
        request=self.fake.calls[0][2];self.assertEqual(request['environment'],{'type':'none'})
        self.assertEqual(request['input'][0]['content'][-1]['type'],'input_image')
        self.assertNotIn('synthetic-key',json.dumps(result))
        self.store.db.close();self.store=RiverStore(self.tmp.name)
        self.assertEqual(image_bytes(self.store,item['photos'][0]['id']),JPEG)
    def test_correction_has_new_photos_and_preserves_prior_feedback(self):
        self.call();self.worker.step();self.worker.step();self.call()
        self.worker.step()
        result=view(self.store,self.task['id']);self.assertEqual(len(result['submissions']),2)
        self.assertIsNotNone(result['submissions'][0]['review'])
        last=[c for c in self.fake.calls if c[0]=='POST' and c[1]=='/agents/sessions'][-1]
        self.assertIn('Count six groups',last[2]['input'][0]['content'][0]['text'])
    def test_rejects_invalid_files_and_rolls_back_batch(self):
        for images in ([IMAGE,'https://example.com/photo.jpg'],[],[IMAGE]*4,['data:image/jpeg;base64,YmFk']):
            with self.assertRaises(AppError):self.call({'images':images})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM assignment_photos').fetchone()[0],0)
        self.assertFalse(self.store.state.get('photoSubmissions'))
        with self.assertRaises(AppError):decode_image('data:image/svg+xml;base64,AAAA')
    def test_assistant_cannot_upload_and_chore_cannot_receive_photos(self):
        with self.assertRaises(AppError):self.call(actor='assistant')
        original=self.task;self.task=self.store.day(self.store.today())['chores'][0]
        with self.assertRaises(AppError):self.call()
        self.task=original
    def test_duplicate_pending_upload_does_not_create_second_job(self):
        self.call()
        with self.assertRaises(AppError):self.call()
        self.assertEqual(len(jobs(self.store)),1)
    def test_stale_assignment_cannot_receive_review(self):
        self.call();self.worker.step()
        self.store.task(self.task['id'])['instructions']='Changed requirements.'
        self.worker.step()
        result=view(self.store,self.task['id'])['submissions'][0]
        self.assertEqual(result['status'],'failed');self.assertIsNone(result['review'])
    def test_review_rejects_fabricated_pages_and_completion_powers(self):
        self.call();self.worker.step();job=next(iter(jobs(self.store).values()))
        action=copy.deepcopy(self.fake.session['required_actions'][0]);action['arguments']['problems'][0]['page']=5
        result=self.worker.apply(job['attemptId'],action);self.assertFalse(result['success'])
        self.assertIsNone(view(self.store,self.task['id'])['submissions'][0]['review'])
        action['call_id']='other';action['arguments']=dict(REVIEW,completeTask=True)
        self.assertFalse(self.worker.apply(job['attemptId'],action)['success'])
    def test_unclear_upload_can_request_retake_without_marking_wrong(self):
        self.fake.session['required_actions'][0]['arguments']={'summary':'Please retake the blurry photo.','allVisibleReviewed':False,'problems':[]}
        self.call();self.worker.step();self.worker.step()
        review=view(self.store,self.task['id'])['submissions'][0]['review']
        self.assertFalse(review['allVisibleReviewed']);self.assertEqual(review['problems'],[])
    def test_future_assignment_uses_today_and_respects_daily_time_limit(self):
        from datetime import date,timedelta
        self.store.task(self.task['id'])['planDate']=(date.fromisoformat(self.store.today())+timedelta(days=4)).isoformat()
        self.call();self.assertTrue(self.store.day(self.store.today())['schoolClock']['running'])
        self.worker.step();self.worker.step()
        clock=self.store.day(self.store.today())['schoolClock'];clock.update(running=False,elapsedSeconds=5400,startedAt=None)
        from practice import PracticeError
        with self.assertRaises(PracticeError):self.call()
    def test_help_mode_guides_an_unanswered_problem_without_grading(self):
        self.fake.session['required_actions'][0]['arguments']={'summary':'Let’s start with equal groups.','allVisibleReviewed':True,'problems':[
            {'page':1,'label':'2','question':'6 x 4','studentAnswer':'','status':'guidance','feedback':'Multiplication counts equal-sized groups.','hint':'Draw six groups with four dots in each.'}]}
        self.call({'images':[IMAGE],'mode':'help','requestedHelp':'How do I start problem 2?'})
        self.worker.step();self.worker.step()
        item=view(self.store,self.task['id'])['submissions'][0]
        self.assertEqual(item['mode'],'help');self.assertEqual(item['review']['problems'][0]['status'],'guidance')
        request=self.fake.calls[0][2]
        self.assertIn('NOT a graded submission',request['agent']['instructions'])
        self.assertIn('How do I start problem 2?',request['input'][0]['content'][0]['text'])
        self.assertIsNone(self.store.task(self.task['id'])['completedAt'])
    def test_help_mode_cannot_return_a_grade(self):
        self.call({'images':[IMAGE],'mode':'help'})
        self.worker.step();self.worker.step()
        self.assertIsNone(view(self.store,self.task['id'])['submissions'][0]['review'])
        self.assertEqual(next(iter(jobs(self.store).values()))['failures'],1)
    def test_help_mode_and_question_are_validated_before_saving(self):
        for body in ({'images':[IMAGE],'mode':'complete'}, {'images':[IMAGE],'requestedHelp':['not text']}):
            with self.assertRaises(AppError):self.call(body)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM assignment_photos').fetchone()[0],0)
    def test_transaction_rollback_removes_image_blobs(self):
        def broken():
            self.store.mutate('/api/tasks/'+self.task['id']+'/photo-review',{'images':[IMAGE]},'river')
            raise ValueError('Simulated storage failure')
        with self.assertRaises(ValueError):self.store.transaction(broken)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM assignment_photos').fetchone()[0],0)
        self.assertFalse(self.store.state.get('photoSubmissions'))

class PhotoHTTPTests(unittest.TestCase):
    def test_upload_requires_same_origin_and_serves_only_image_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            server=create_server(d,port=0);server.store.ai_review_enabled=True
            task=server.store.transaction(lambda:server.store.create_task({'title':'HTTP fixture','subject':'Math','instructions':'Synthetic only.'}))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            route=base+'/api/tasks/'+task['id']+'/photo-review'
            try:
                with self.assertRaises(HTTPError) as e:urlopen(Request(route,data=json.dumps({'images':[IMAGE]}).encode(),headers={'Content-Type':'application/json'}))
                self.assertEqual(e.exception.code,403)
                with urlopen(Request(route,data=json.dumps({'images':[IMAGE]}).encode(),headers={'Content-Type':'application/json','Origin':base,'X-Student-Request':'1'})) as r:result=json.load(r)
                url=result['submissions'][0]['photos'][0]['url']
                with urlopen(base+url) as r:
                    self.assertEqual(r.headers['Content-Type'],'image/jpeg');self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff');self.assertEqual(r.read(),JPEG)
            finally:server.shutdown();thread.join();server.server_close()
