#!/usr/bin/env python3
"""Narrow CLI for the existing Codex assistant. No parent approval privileges."""
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
    sub=p.add_subparsers(dest='command',required=True)
    state=sub.add_parser('state',help='Read authoritative task state, routine, timers, and rewards')
    state.add_argument('--date')
    sub.add_parser('backlog-state',help='Read unscheduled activities for future planning')
    backlog=sub.add_parser('backlog-add',help='Save an unscheduled activity; never assigns it')
    backlog.add_argument('--json-file',type=Path,required=True)
    sub.add_parser('practice-state',help='Read quiz source, current question, attempts and review rubric')
    draft=sub.add_parser('practice-draft',help='Import prepared quizzes as drafts for parent review')
    draft.add_argument('--json-file',type=Path,required=True)
    for name in ('understanding','submission','report','help','correct','revoke','resolve','practice-review','practice-revoke','practice-update-draft','backlog-edit'):
        s=sub.add_parser(name)
        s.add_argument('item_id')
        s.add_argument('--json-file',type=Path,required=True,help='JSON evidence body; preserves text without shell quoting')
    args=p.parse_args()
    if args.data_dir is None:
        try:
            args.data_dir=installed_data_dir()
        except RuntimeError as exc:
            p.error(str(exc))
    url=urlparse(args.url)
    pinned=args.data_dir/'.assistant-origin'
    remote=pinned.read_text().strip() if pinned.exists() else ''
    local=url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')
    cloud=url.scheme=='https' and bool(remote) and args.url.rstrip('/')==remote.rstrip('/')
    if not (local or cloud) or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
        p.error('Use loopback HTTP, or pin your exact HTTPS origin in .assistant-origin beside the private assistant token.')
    if getattr(args,'item_id',None) and not re.fullmatch(r'[a-zA-Z0-9_-]+',args.item_id):
        p.error('Use the exact item ID returned by state.')
    try:
        if args.command=='state':
            path='/api/assistant/state'+('?'+urlencode({'date':args.date}) if args.date else '')
            data=None
        elif args.command=='backlog-state':
            path='/api/assistant/backlog'
            data=None
        elif args.command=='practice-state':
            path='/api/assistant/practice'
            data=None
        else:
            if args.command=='backlog-add':
                path='/api/assistant/backlog'
            elif args.command=='backlog-edit':
                path=f'/api/assistant/backlog/{args.item_id}'
            elif args.command=='practice-draft':
                path='/api/assistant/practice/drafts'
            elif args.command=='practice-update-draft':
                path=f'/api/assistant/practice/drafts/{args.item_id}'
            elif args.command.startswith('practice-'):
                path=f'/api/assistant/practice/attempts/{args.item_id}/{args.command.removeprefix("practice-")}'
            else:
                scope='' if args.command in ('report','help','correct') else 'assistant/'
                path=f'/api/{scope}tasks/{args.item_id}/{args.command}'
            data=args.json_file.read_bytes()
            json.loads(data)
        token=(args.data_dir/'.assistant-token').read_text().strip()
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
        print('The local app is not running. Open Launch Student.command.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
