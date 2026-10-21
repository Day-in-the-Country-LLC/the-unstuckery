"""Date-effective chore schedules; explicit weekdays also apply on weekends."""
import copy
from datetime import date

DEFAULTS = [
    {'id':'petey','title':'Take your pet outside','instructions':'Take your pet outside to go to the bathroom.','startDate':'0001-01-01'},
    {'id':'dogs','title':'Feed your pets','instructions':'Give your pets their food.','startDate':'0001-01-01'},
    {'id':'dishwasher','title':'Check the dishwasher','instructions':'If there are clean dishes, put them away. Otherwise, report that no clean dishes need putting away.','startDate':'0001-01-01'},
]


def validate_days(days):
    from server import AppError
    if days is None: # Existing after-school behavior is an explicit option.
        return None
    if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(7) for d in days) or len(days)!=len(set(days)):
        raise AppError('Choose at least one day, without duplicates. Monday is 0 and Sunday is 6.')
    return sorted(days)


def templates(store):
    return DEFAULTS + store.state.get('routineChores',[])


def rule(store, template, day):
    applicable=[r for r in store.state.get('routineSchedules',{}).get(template['id'],[]) if r['effectiveDate'] <= day]
    if applicable:
        return max(applicable,key=lambda r:r['effectiveDate'])['days']
    return template.get('days')


def scheduled(store, task, day_state):
    day=task['planDate']
    key=task['id'].removeprefix(day+'-')
    template=next((t for t in templates(store) if t['id']==key),None)
    if not template or day < template['startDate']:
        return False
    days=rule(store,template,day)
    return day_state['afterSchool'] if days is None else date.fromisoformat(day).weekday() in days


def catalog(store):
    result=[]
    for template in templates(store):
        revisions=store.state.get('routineSchedules',{}).get(template['id'],[])
        latest=max(revisions,key=lambda r:r['effectiveDate']) if revisions else None
        result.append(dict(copy.deepcopy(template), days=copy.deepcopy(latest['days'] if latest else template.get('days')),
                           effectiveDate=latest['effectiveDate'] if latest else template['startDate']))
    return result


def update_schedule(store, key, body):
    from server import AppError, valid_date
    template=next((t for t in templates(store) if t['id']==key),None)
    if not template:
        raise AppError('That routine chore was not found.',404)
    if 'days' not in body:
        raise AppError('Choose the days for this chore.')
    days=validate_days(body['days'])
    effective=valid_date(body.get('effectiveDate',store.today()))
    if effective < max(store.today(),template['startDate']):
        raise AppError('Schedule changes start today or later, after the chore begins. Earlier checklists stay as they were.')
    revisions=store.state.setdefault('routineSchedules',{}).setdefault(key,[])
    before=copy.deepcopy(revisions)
    revisions[:]=[r for r in revisions if r['effectiveDate']!=effective]
    revisions.append({'effectiveDate':effective,'days':days,'at':store.stamp(),'actor':'parent'})
    revisions.sort(key=lambda r:r['effectiveDate'])
    history=store.state.setdefault('routineScheduleHistory',{'history':[]})
    store.history(history,'parent','chore_schedule_changed',{'choreId':key,'before':before,'effectiveDate':effective,'days':days})
    return {'ok':True,'chore':next(t for t in catalog(store) if t['id']==key)}
