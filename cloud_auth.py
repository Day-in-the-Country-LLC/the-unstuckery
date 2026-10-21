"""Google sign-in and role enforcement for one family's self-hosted installation.

No Google access/refresh tokens are requested. Only verified identity claims are
used, and opaque session secrets are stored as hashes in the private database.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

SESSION_SECONDS = 12 * 60 * 60
COOKIE = '__Host-unstuckery'


def email_address(value):
    from server import AppError
    if not isinstance(value, str) or not re.fullmatch(r'[^\s@]{1,100}@[^\s@]{1,150}\.[^\s@]{2,}', value.strip()):
        raise AppError('Enter a valid Google account email address.')
    return value.strip().lower()


def load_config(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('Keep cloud settings in a private file readable only by its owner (chmod 600).')
    return json.loads(path.read_text())


class CloudAuth:
    def __init__(self, store, config, verifier=None):
        self.store = store
        self.origin = config['origin'].rstrip('/')
        parsed = urlparse(self.origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Cloud origin must be an HTTPS origin with no path.')
        self.host = parsed.netloc
        self.client_id = config['googleClientId']
        if not re.fullmatch(r'[a-zA-Z0-9._-]+\.apps\.googleusercontent\.com', self.client_id):
            raise ValueError('Set the Google web client ID before enabling cloud mode.')
        self.nonces = {}
        self.requests = []
        self.login_lock = threading.Lock()
        self.verifier = verifier or self.verify_google
        self.google_request = None
        parents = {email_address(e) for e in config.get('parentEmails', [])}
        if not parents:
            raise ValueError('Configure at least one parent Google account.')
        def initialize():
            accounts = store.state.setdefault('webAccounts', {})
            # Deployment configuration is authoritative for parent access.
            for email, account in list(accounts.items()):
                if account['role'] == 'parent' and email not in parents:
                    del accounts[email]
            for email in parents:
                account = accounts.setdefault(email, {'role': 'parent', 'sub': None})
                if account['role'] != 'parent':
                    raise ValueError('A student account cannot also be a parent.')
            store.state.setdefault('webSessions', {})
        store.transaction(initialize)

    def verify_google(self, credential):
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        import requests
        import cachecontrol
        if self.google_request is None:
            self.google_request = Request(session=cachecontrol.CacheControl(requests.Session()))
        return id_token.verify_oauth2_token(credential, self.google_request, self.client_id)

    def cookie(self, token='', logout=False):
        return f'{COOKIE}={token}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age={0 if logout else SESSION_SECONDS}'

    def challenge(self):
        now = time.time()
        with self.login_lock:
            self.nonces = {k: v for k, v in self.nonces.items() if v['expires'] > now}
            if len(self.nonces) >= 1000:
                from server import AppError
                raise AppError('Please wait a moment before signing in.', 429)
            token, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.nonces[hashlib.sha256(token.encode()).hexdigest()] = {'nonce': nonce, 'expires': now + 600}
        cookie = f'__Host-unstuckery-login={token}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=600'
        return {'clientId': self.client_id, 'nonce': nonce}, cookie

    def login(self, body, login_cookie):
        from server import AppError
        credential = body.get('credential', '')
        if not isinstance(credential, str) or not 100 < len(credential) < 16000:
            raise AppError('Google sign-in was not completed. Please try again.', 401)
        # Bounded verification and nonce consumption make the credential one-use.
        with self.login_lock:
            now = time.time()
            self.requests = [t for t in self.requests if now - t < 60]
            if len(self.requests) >= 60:
                raise AppError('Please wait a minute before signing in again.', 429)
            self.requests.append(now)
            challenge = self.nonces.pop(hashlib.sha256((login_cookie or '').encode()).hexdigest(), None)
            if not challenge or challenge['expires'] <= now:
                raise AppError('This sign-in expired. Refresh and try again.', 401)
            try:
                claims = self.verifier(credential)
            except Exception:
                raise AppError('Google could not verify this sign-in. Please try again.', 401) from None
            if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
                    or claims.get('aud') != self.client_id or claims.get('exp', 0) <= now
                    or not isinstance(claims.get('sub'), str) or not claims['sub']
                    or claims.get('email_verified') is not True
                    or not isinstance(claims.get('nonce'), str)
                    or not hmac.compare_digest(claims['nonce'], challenge['nonce'])):
                raise AppError('Google could not verify this sign-in. Please try again.', 401)
            email = email_address(claims.get('email'))
            # Google must be authoritative for the allowlisted email address.
            if not email.endswith('@gmail.com') and not claims.get('hd'):
                raise AppError('Use an approved Gmail or Google Workspace account.', 403)
            def establish():
                account = self.store.state['webAccounts'].get(email)
                if not account or (account.get('sub') and account['sub'] != claims['sub']):
                    raise AppError('This Google account has not been invited to this family’s Unstuckery.', 403)
                account['sub'] = claims['sub']
                sessions = self.store.state['webSessions']
                for key, session in list(sessions.items()):
                    if session['expires'] <= now:
                        del sessions[key]
                # Bound private session history; never store the Google credential.
                if len(sessions) >= 100:
                    del sessions[min(sessions, key=lambda k: sessions[k]['expires'])]
                token = secrets.token_urlsafe(32)
                sessions[hashlib.sha256(token.encode()).hexdigest()] = {
                    'email': email, 'sub': claims['sub'], 'role': account['role'], 'expires': now + SESSION_SECONDS}
                return token, '/' + account['role']
            return self.store.transaction(establish)

    def status(self, token):
        session = self.store.state['webSessions'].get(hashlib.sha256((token or '').encode()).hexdigest(), {})
        account = self.store.state['webAccounts'].get(session.get('email'), {})
        valid = (session.get('expires', 0) > time.time() and account.get('role') == session.get('role')
                 and account.get('sub') == session.get('sub') and bool(account))
        role = session.get('role') if valid else None
        return {'cloud': True, 'configured': True, 'parent': role == 'parent', 'role': role,
                'email': session.get('email') if valid else None}

    def logout(self, token):
        self.store.state['webSessions'].pop(hashlib.sha256((token or '').encode()).hexdigest(), None)
        return {'ok': True}

    def accounts(self):
        return {'accounts': [{'email': email, 'role': item['role']} for email, item in self.store.state['webAccounts'].items()]}

    def set_student(self, body):
        from server import AppError
        email = email_address(body.get('email'))
        accounts = self.store.state['webAccounts']
        if accounts.get(email, {}).get('role') == 'parent':
            raise AppError('Choose a separate Google account for the student.')
        for previous, account in list(accounts.items()):
            if account['role'] == 'student' and previous != email:
                del accounts[previous]
        accounts.setdefault(email, {'role': 'student', 'sub': None})
        # Revocation takes effect immediately, including already open tabs.
        sessions = self.store.state['webSessions']
        for key, session in list(sessions.items()):
            if session['email'] not in accounts:
                del sessions[key]
        return self.accounts()
