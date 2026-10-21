"""Parent-assigned one-time to-dos, separate from schoolwork and recurring chores."""
import copy
import re
import secrets


def mutate(store, path, body, actor):
    match=re.fullmatch(r'/api/parent/todos(?:/([^/]+))?',path)
    if not match:
        return None
    from server import AppError, required, clean, valid_date
    if actor!='parent':
        raise AppError('Unlock parent review to assign to-dos.',403)
    key=match.group(1)
    items=store.state.setdefault('todos',[])
    item=next((t for t in items if t['id']==key),None) if key else None
    if key and not item:
        raise AppError('That to-do was not found.',404)
    title=required(body,'title',300)
    instructions=clean(body.get('instructions',''),4000)
    plan_date=valid_date(body.get('planDate',store.today()))
    if plan_date < store.today() and (not item or plan_date!=item['planDate']):
        raise AppError('Assign new work to today or a future date.')
    counts=body.get('countsForReward',True)
    if type(counts) is not bool:
        raise AppError('Choose whether this to-do counts toward the weekly prize.')
    fields={'title':title,'instructions':instructions,'planDate':plan_date,'countsForReward':counts}
    if item:
        before={k:copy.deepcopy(item.get(k)) for k in fields}
        changed=any(before[k]!=fields[k] for k in ('title','instructions','planDate'))
        item.update(fields)
        if changed:
            item.update(reportedAt=None,reportedNote='',parentConfirmed=None,completedAt=None,reviewHold='')
        store.history(item,'parent','todo_edited',{'before':before,'after':fields,'freshCheckNeeded':changed})
    else:
        item=store.base_task('todo-'+secrets.token_hex(8),'todo','Other to-dos',title,instructions,plan_date,len(items))
        item.update(fields,plannedMinutes=0,scope='todo')
        store.history(item,'parent','todo_assigned',fields)
        items.append(item)
    return {'ok':True,'todo':store.view_task(item)}
