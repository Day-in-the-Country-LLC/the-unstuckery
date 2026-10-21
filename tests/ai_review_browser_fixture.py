"""Disposable browser fixture with fake API transport. Never run with real data."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import create_server
from review_demo import seed
from ai_review import ReviewWorker

class BrowserFakeClient:
    def __init__(self):self.payloads={};self.polls={}
    def request(self,method,path,body=None):
        import json
        if method=='POST' and path=='/agents/sessions':
            ident='sess_browser_'+str(len(self.payloads)+1)
            self.payloads[ident]=json.loads(body['input']);self.polls[ident]=0
            return {'id':ident,'status':'in_progress'}
        ident=path.split('/')[3]
        if method=='GET':
            self.polls[ident]+=1
            if self.polls[ident]<3:return {'id':ident,'status':'in_progress','required_actions':[]}
            payload=self.payloads[ident]
            correct='Multiplying' in payload['answer']
            return {'id':ident,'status':'requires_action','required_actions':[{'type':'function_call','turn_id':'turn_browser','call_id':'call_browser','name':'save_review','arguments':{
                'correct':correct,'independent':True,'rubricEvidence':'PRIVATE synthetic grading evidence','feedback':'Your explanation connects both parts of the fraction.' if correct else 'Explain the change to both parts of the fraction.',
                'hint':'' if correct else 'Think about the numerator and denominator together.'}}]}
        return {}

if __name__=='__main__':
    directory=Path(tempfile.mkdtemp(prefix='river-ai-browser-'))
    server=create_server(directory,4319)
    seed(server.store)
    config=directory/'fixture.env';config.write_text('OPENAI_API_KEY=synthetic-fixture\n');config.chmod(0o600)
    fake=BrowserFakeClient();server.store.ai_review_enabled=True
    server.store.ai_review_worker=ReviewWorker(server.store,config,lambda _:fake)
    server.store.ai_review_worker.start()
    print('Synthetic fake-transport browser fixture on port 4319',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();server.store.db.close()
