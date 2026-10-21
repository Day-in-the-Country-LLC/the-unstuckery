"""Unscheduled activity ideas; only explicit parent scheduling creates schoolwork."""
import copy
import json
import re
import secrets
from pathlib import Path


def data(store):
    return store.state.setdefault('activityBacklog',[])


def view(store,actor):
    from server import AppError,safe_url
    if actor not in ('parent','assistant'):raise AppError('Activity backlog is for planning.',403)
    config=store.data_dir/'backlog-config.json'
    if not config.exists():config=Path(__file__).with_name('backlog-config.json')
    folder=safe_url(json.loads(config.read_text()).get('driveFolderUrl','')) if config.exists() else ''
    rows=[]
    for entry in data(store):
        item=copy.deepcopy(entry)
        task=next((t for t in store.state['tasks'] if t['id']==item.get('assignedTaskId')),None)
        item['status']='scheduled' if item.get('assignedTaskId') else 'archived' if item['archived'] else 'available'
        item['assignedDate']=task['planDate'] if task else None
        rows.append(item)
    return {'items':rows,'driveFolderUrl':folder}


def fields(body):
    from server import AppError,required,clean,safe_url
    minutes=body.get('estimatedMinutes',15)
    if type(minutes) is not int or not 1<=minutes<=90:raise AppError('Estimate between 1 and 90 minutes.')
    return {'title':required(body,'title',250),'subject':required(body,'subject',100),
            'instructions':clean(body.get('instructions',''),8000),
            'estimatedMinutes':minutes,'materialsUrl':safe_url(body.get('materialsUrl',''))}


def mutate(store,path,body,actor):
    match=re.fullmatch(r'/api/(parent|assistant)/backlog(?:/([a-zA-Z0-9_-]+)(?:/(schedule|archive|restore))?)?',path)
    if not match:return None
    from server import AppError,valid_date
    scope,ident,action=match.groups()
    if actor!=scope:raise AppError('Use the appropriate planning access.',403)
    entries=data(store)
    if not ident:
        item=dict(fields(body),id=secrets.token_hex(8),createdAt=store.stamp(),createdBy=actor,
                  revision=1,archived=False,assignedTaskId=None,history=[])
        store.history(item,actor,'backlog_saved',{'note':'Saved for later; no assignment date.'})
        entries.append(item)
        return {'ok':True,'itemId':item['id'],'revision':1}
    item=next((entry for entry in entries if entry['id']==ident),None)
    if not item:raise AppError('That backlog activity was not found.',404)
    if actor=='assistant' and (action or item['createdBy']!='assistant'):
        raise AppError('Only a parent can schedule or archive activities, or edit parent-created ideas.',403)
    if action=='schedule' and item['assignedTaskId']:
        task=store.task(item['assignedTaskId'])
        if body.get('date',store.today())!=task['planDate']:raise AppError('This activity is already assigned. Edit its date in Schoolwork plan.',409)
        return {'ok':True,'itemId':ident,'taskId':task['id'],'alreadyAssigned':True}
    if type(body.get('expectedRevision')) is not int or body['expectedRevision']!=item['revision']:
        raise AppError('This activity changed. Refresh the backlog before trying again.',409)
    if item['assignedTaskId']:raise AppError('This activity is already assigned. Edit it in Schoolwork plan.',409)
    if action=='schedule':
        if item['archived']:raise AppError('Restore this activity before assigning it.',409)
        day=valid_date(body.get('date',store.today()))
        if day<store.today():raise AppError('Choose today or a future day.')
        task=store.create_task({'title':item['title'],'subject':item['subject'],
            'instructions':item['instructions'] or 'Complete this saved activity and report ready for parent review.',
            'plannedMinutes':item['estimatedMinutes'],'sourceUrl':item['materialsUrl'],
            'sourceNote':'Selected by parent from the Activity backlog.','scope':'practice','planDate':day,
            'requirements':{'parent':True,'understanding':False,'submission':False}})
        store.task(task['id'])['backlogItemId']=ident
        item['assignedTaskId']=task['id']
        store.history(item,actor,'backlog_scheduled',{'date':day,'taskId':task['id']})
    elif action in ('archive','restore'):
        item['archived']=action=='archive'
        store.history(item,actor,'backlog_'+action,{})
    else:
        before={key:copy.deepcopy(item[key]) for key in fields(body)}
        item.update(fields(body))
        store.history(item,actor,'backlog_edited',{'before':before})
    item['revision']+=1
    return {'ok':True,'itemId':ident,'taskId':item['assignedTaskId'],'revision':item['revision']}
