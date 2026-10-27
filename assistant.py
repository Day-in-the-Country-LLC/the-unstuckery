#!/usr/bin/env python3
"""Private assistant/planner/manager CLI. Credentials never impersonate a parent."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

ROOT=Path(__file__).resolve().parent

def installed_data_dir():
    from onboarding import load_location
    from drive_storage import read_json, MARKER, IDENT, StorageError
    support=Path(os.environ.get('RIVER_APP_SUPPORT', Path.home()/'Library/Application Support/RiverAfterSchool'))
    location=load_location(support)
    if not location:
        return ROOT/'data'
    ident=read_json(Path(location['folder'])/MARKER).get('workspaceId','')
    if not IDENT.fullmatch(str(ident)):
        raise StorageError('The selected Student workspace is not ready.')
    return support/'workspaces'/ident

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:4317')
    p.add_argument('--data-dir',type=Path)
    roles=p.add_mutually_exclusive_group()
    roles.add_argument('--planner',action='store_true',help='Use the opt-in exercise planner credential')
    roles.add_argument('--manager',action='store_true',help='Use the separate opt-in assistant manager credential')
    sub=p.add_subparsers(dest='command',required=True)
    state=sub.add_parser('state',help='Read authoritative task state, routine, timers, and rewards')
    state.add_argument('--date')
    sub.add_parser('backlog-state',help='Read unscheduled activities for future planning')
    backlog=sub.add_parser('backlog-add',help='Save an unscheduled activity; never assigns it')
    backlog.add_argument('--json-file',type=Path,required=True)
    sub.add_parser('reviews',help='Read manager review diagnostics and revisions')
    sub.add_parser('audit',help='Read the manager action audit')
    for name in ('task-add','correct-work-time','check-access'):
        sub.add_parser(name).add_argument('--json-file',type=Path,required=True)
    sub.add_parser('practice-state',help='Read quiz source, current question, attempts and review rubric')
    draft=sub.add_parser('practice-draft',help='Import prepared quizzes as drafts for parent review')
    draft.add_argument('--json-file',type=Path,required=True)
    for name in ('understanding','submission','report','help','correct','revoke','resolve','practice-review','practice-revoke','practice-update-draft','practice-publish','practice-remove-steps','backlog-edit','task-update','retry-review'):
        s=sub.add_parser(name)
        s.add_argument('item_id')
        s.add_argument('--json-file',type=Path,required=True,help='JSON evidence body; preserves text without shell quoting')
    args=p.parse_args()
    if args.planner and args.command not in ('state','practice-state','practice-draft','practice-update-draft','practice-publish'):
        p.error('Planner access only reads plans/practice, prepares drafts, and publishes its own exercises.')
    if args.manager and args.command not in ('state','practice-state','practice-draft','practice-update-draft','practice-publish','practice-remove-steps','task-add','task-update','correct-work-time','retry-review','reviews','audit','check-access'):
        p.error('Manager access is limited to plan/exercise management, timer corrections, review retries, and audit reads.')
    if args.command in ('task-add','task-update','correct-work-time','retry-review','reviews','audit','check-access','practice-remove-steps') and not args.manager:
        p.error('This command requires the separate --manager credential.')
    if args.command=='practice-publish' and not (args.planner or args.manager):
        p.error('Publication requires a separately enabled --planner or --manager credential.')
    if args.data_dir is None:
        try:
            args.data_dir=installed_data_dir()
        except RuntimeError as exc:
            p.error(str(exc))
    url=urlparse(args.url)
    scope='manager' if args.manager else 'planner' if args.planner else 'assistant'
    pinned=args.data_dir/f'.{scope}-origin'
    remote=pinned.read_text().strip() if pinned.exists() else ''
    local=url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')
    cloud=url.scheme=='https' and bool(remote) and args.url.rstrip('/')==remote.rstrip('/')
    if not (local or cloud) or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
        p.error(f'Use loopback HTTP, or pin your exact HTTPS origin in .{scope}-origin beside its private token.')
    if getattr(args,'item_id',None) and not re.fullmatch(r'[a-zA-Z0-9_-]+',args.item_id):
        p.error('Use the exact item ID returned by state.')
    try:
        if args.command=='state':
            path=f'/api/{scope}/state'+('?'+urlencode({'date':args.date}) if args.date else '')
            data=None
        elif args.command in ('reviews','audit'):
            path=f'/api/manager/{args.command}'
            data=None
        elif args.command=='backlog-state':
            path='/api/assistant/backlog'
            data=None
        elif args.command=='practice-state':
            path=f'/api/{scope}/practice'
            data=None
        else:
            if args.command=='check-access':
                path='/api/manager/check'
            elif args.command=='task-add':
                path='/api/manager/tasks'
            elif args.command=='task-update':
                path=f'/api/manager/tasks/{args.item_id}'
            elif args.command=='correct-work-time':
                path='/api/manager/school-clock/correct'
            elif args.command=='retry-review':
                path=f'/api/manager/reviews/{args.item_id}/retry'
            elif args.command=='backlog-add':
                path='/api/assistant/backlog'
            elif args.command=='backlog-edit':
                path=f'/api/assistant/backlog/{args.item_id}'
            elif args.command=='practice-draft':
                path=f'/api/{scope}/practice/drafts'
            elif args.command=='practice-update-draft':
                path=f'/api/{scope}/practice/drafts/{args.item_id}'
            elif args.command=='practice-publish':
                path=f'/api/{scope}/practice/quizzes/{args.item_id}/publish'
            elif args.command=='practice-remove-steps':
                path=f'/api/manager/practice/quizzes/{args.item_id}/remove-steps'
            elif args.command.startswith('practice-'):
                path=f'/api/assistant/practice/attempts/{args.item_id}/{args.command.removeprefix("practice-")}'
            else:
                task_scope='' if args.command in ('report','help','correct') else 'assistant/'
                path=f'/api/{task_scope}tasks/{args.item_id}/{args.command}'
            data=args.json_file.read_bytes()
            json.loads(data)
        token=(args.data_dir/f'.{scope}-token').read_text().strip()
        request=Request(args.url.rstrip('/')+path,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with build_opener(NoRedirect()).open(request,timeout=10) as response:
            result=json.load(response)
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except FileNotFoundError:
        print('Start the app and check the JSON file/data directory path.',file=sys.stderr)
        return 1
    except json.JSONDecodeError:
        print('The evidence file needs valid JSON.',file=sys.stderr)
        return 1
    except HTTPError as e:
        print(e.read().decode(),file=sys.stderr)
        return 1
    except URLError:
        print('Could not connect to the configured app. Check its address, connection, and certificate settings.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
