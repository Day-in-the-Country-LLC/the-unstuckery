"""Parent-confirmed outside-app review, without inventing learning or portal evidence."""
from datetime import datetime


def is_final(task):
    confirmation=task.get('parentConfirmed') or {}
    basis=task.get('completionBasis') or {}
    return bool(task.get('reportedAt') and task.get('completedAt') and confirmation.get('actor')=='parent'
                and basis.get('type')=='parent_outside_app_review'
                and basis.get('at')==confirmation.get('at')==task.get('completedAt'))


def complete(store,task,reconciled=False):
    confirmation=task['parentConfirmed']
    task['completedAt']=confirmation['at']
    task['completionBasis']={'type':'parent_outside_app_review','at':confirmation['at']}
    store.history(task,'system' if reconciled else 'parent','parent_completion_reconciled' if reconciled else 'completed',{
        'basis':'parent_outside_app_review','parentConfirmedAt':confirmation['at'],
        'note':'Completed from the parent’s outside-app review. Unrecorded assistant and portal evidence remains unrecorded.'})


def reconcile(store):
    migrations=store.state.setdefault('migrations',{})
    key='parent_school_confirmation_final_v1'
    if key in migrations:return
    changed=[]
    for task in store.state['tasks']:
        confirmation=task.get('parentConfirmed') or {}
        if (task.get('kind')!='school' or task.get('practiceQuizId') or task.get('completedAt')
                or not task.get('reportedAt') or task.get('reviewHold') or task.get('parentReturned')
                or confirmation.get('actor')!='parent'):
            continue
        try:
            ready=datetime.fromisoformat(task['reportedAt'])
            confirmed=datetime.fromisoformat(confirmation['at'])
            if not (ready<=confirmed<=store.now()) or task['planDate']>store.today():continue
            # A later correction is a deliberate reopening, not a stuck confirmation.
            if any(event.get('action') in ('correction','evidence_revoked','plan_edited','returned_to_river')
                   and datetime.fromisoformat(event['at'])>confirmed for event in task.get('history',[])):continue
        except (KeyError,ValueError,TypeError):
            continue
        complete(store,task,reconciled=True)
        changed.append(task['id'])
    migrations[key]={'at':store.stamp(),'taskIds':changed}
