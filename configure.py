#!/usr/bin/env python3
"""Interactive setup writes only ignored private files, never credentials to stdout."""
import getpass
import json
from pathlib import Path
from urllib.parse import urlparse


def private_file(path, content):
    with path.open('w', encoding='utf-8') as handle:
        path.chmod(0o600)
        handle.write(content)


def main():
    private = Path('private'); private.mkdir(mode=0o700, exist_ok=True)
    if any((private/name).exists() for name in ('cloud-config.json','review.env','installation.env')):
        raise SystemExit('Settings already exist in private/. Edit those files to keep your existing credentials.')
    print('The Unstuckery — one family, one student, one small next step.')
    hosted = input('Will this be available online? [y/N] ').strip().lower() == 'y'
    timezone = input('Time zone [America/New_York]: ').strip() or 'America/New_York'
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone)
    canvas = input('School Canvas origin, or leave blank (example: https://school.instructure.com): ').strip()
    if canvas and (urlparse(canvas).scheme != 'https' or urlparse(canvas).path not in ('','/')):
        raise SystemExit('Use an HTTPS Canvas origin with no path.')
    origin=''
    if hosted:
        origin=input('Your HTTPS address (example: https://learning.example.com): ').strip().rstrip('/')
        parsed=urlparse(origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise SystemExit('Use an HTTPS address with no path.')
        client=input('Google web client ID: ').strip()
        parent=input('Parent Google email: ').strip().lower()
        private_file(private/'cloud-config.json',json.dumps({'origin':origin,'googleClientId':client,'parentEmails':[parent]},indent=2)+'\n')
    model=input('OpenAI model [gpt-6.1-sol]: ').strip() or 'gpt-6.1-sol'
    key=getpass.getpass('Your OpenAI API key (hidden; leave blank to configure later): ').strip()
    if any('\n' in value or '\r' in value for value in (model,key,timezone,canvas)):
        raise SystemExit('Settings must be one line each.')
    private_file(private/'review.env',f'OPENAI_API_KEY={key}\nOPENAI_REVIEW_MODEL={model}\nRIVER_AI_MAX_REVIEWS_PER_DAY=30\n')
    domain=urlparse(origin).hostname or ''
    private_file(private/'installation.env',f'UNSTUCKERY_TIMEZONE={timezone}\nUNSTUCKERY_CANVAS_ORIGIN={canvas or "https://canvas.example.edu"}\nUNSTUCKERY_DOMAIN={domain}\n')
    Path('data').mkdir(mode=0o700,exist_ok=True)
    print('Saved in private/. Keep this folder and data/ out of Git. See SELF-HOSTING.md for the start command.')

if __name__=='__main__':main()
