"""Closed SQLite checkpoints for sequential use through Drive for desktop.

Each session has a unique archive. Completed archives never change. A completion
record names the archive's SHA-256, so files arriving out of order cannot be
mistaken for a complete handoff. File sync is not a distributed locking service.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

MARKER = 'river-memory.json'
IDENT = re.compile(r'^[a-f0-9]{32}$')
FILES = ('river.sqlite3', '.assistant-token', 'review.env', 'canvas.json', 'backlog-config.json')


class StorageError(RuntimeError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:
        result = json.loads(Path(path).read_text())
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError) as exc:
        raise StorageError('Student’s Drive files are not ready. Wait for Google Drive to finish syncing, then reopen Student.') from exc


def atomic_bytes(path, value):
    """Write locally, fsync, then atomically replace; never expose partial files."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.river-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, sort_keys=True).encode())


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_database(path):
    try:
        db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError()
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if not isinstance(state, dict) or 'tasks' not in state or 'days' not in state:
                raise ValueError()
        finally:
            db.close()
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise StorageError('That saved copy is incomplete or damaged. Your existing files have been preserved.') from exc


def import_legacy(source, destination, support):
    """Copy a stopped old installation, including its private connection settings."""
    source, destination = Path(source), Path(destination)
    if not (source / 'river.sqlite3').is_file():
        raise StorageError('Choose the old Student data folder containing river.sqlite3.')
    with (source / '.server-lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StorageError('Quit the old Student app before importing its saved progress. Its files have not been changed.') from exc
        check_database(source / 'river.sqlite3')
        for name in ('river.sqlite3', '.assistant-token'):
            if (source / name).exists():
                atomic_bytes(destination / name, (source / name).read_bytes())
        key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:20]
        for source_path, name in ((Path(support) / 'review.env', 'review.env'),
                                  (Path(support) / 'canvas' / (key + '.json'), 'canvas.json'),
                                  (source.parent / 'backlog-config.json', 'backlog-config.json')):
            if source_path.exists():
                atomic_bytes(destination / name, source_path.read_bytes())


class DriveStorage:
    def __init__(self, folder, support):
        self.folder, self.support = Path(folder).expanduser(), Path(support)
        self.cache = None
        self.lock = None
        self.session = None
        self.workspace_id = None
        self.last_fingerprint = None
        self.error = ''
        self.last_saved = None

    def prepare(self, seed=None, create=False):
        """Acquire the local cache before importing; never touch a running DB."""
        if not self.folder.is_dir():
            raise StorageError('The chosen Google Drive folder is unavailable. Reconnect Drive; Student will not create an empty replacement.')
        if not (self.folder / MARKER).exists():
            if not create or any(self.folder.iterdir()):
                raise StorageError('Choose an existing Student App Memory folder, or an empty folder for a new setup.')
            self.workspace_id = uuid.uuid4().hex
            initial = uuid.uuid4().hex
            self._open_cache()
            self.session = {'id': initial, 'parent': None, 'device': socket.gethostname(), 'startedAt': stamp()}
            self._record()
            if seed:
                import_legacy(seed, self.cache, self.support)
            for name in ('sessions', 'completed'):
                (self.folder / name).mkdir()
            write_json(self.folder / MARKER, {'format': 1, 'workspaceId': self.workspace_id, 'initialSession': initial})
            write_json(self.folder / 'sessions' / (initial + '.json'), self.session)
        else:
            descriptor = read_json(self.folder / MARKER)
            if descriptor.get('format') != 1 or not IDENT.fullmatch(str(descriptor.get('workspaceId', ''))) or not IDENT.fullmatch(str(descriptor.get('initialSession', ''))):
                raise StorageError('This Student memory folder needs a compatible app version.')
            self.workspace_id = descriptor['workspaceId']
            self._open_cache()
            records, completed, head = self._graph()
            local = read_json(self.cache / 'session.json') if (self.cache / 'session.json').exists() else {}
            if head not in completed:
                if local.get('id') != head:
                    device = records[head].get('device', 'another laptop')
                    raise StorageError(f'River is still open, or its close has not synced, on {device}. Quit it there and wait for Drive to finish syncing on both laptops.')
                # Same Mac can resume its interrupted session from the durable cache.
                self.session = records[head]
                if not (self.cache / 'river.sqlite3').exists():
                    raise StorageError('This interrupted session needs recovery from its Drive archive. Your saved copies are preserved.')
            else:
                if local.get('pending'):
                    raise StorageError('This Mac has unsynced changes from an earlier session. They are preserved in its cache; resolve them before switching saved copies.')
                archive = self.folder / 'sessions' / (head + '.river')
                self._restore(archive, completed[head].get('sha256'))
                self.session = {'id': uuid.uuid4().hex, 'parent': head, 'device': socket.gethostname(), 'startedAt': stamp()}
                self._record()
                write_json(self.folder / 'sessions' / (self.session['id'] + '.json'), self.session)
        for name, default in (('review.env', 'OPENAI_API_KEY=\n'), ('canvas.json', '{}'), ('backlog-config.json', '{"driveFolderUrl":""}')):
            if not (self.cache / name).exists():
                atomic_bytes(self.cache / name, default.encode())
        return self.cache

    def _open_cache(self):
        self.cache = self.support / 'workspaces' / self.workspace_id
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.cache / '.desktop-lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            self.lock = None
            raise StorageError('This Student memory folder is already open on this Mac.') from exc

    def _record(self, pending=False):
        write_json(self.cache / 'session.json', dict(self.session, pending=pending))

    def _graph(self):
        descriptor = read_json(self.folder / MARKER)
        if descriptor.get('workspaceId') != self.workspace_id:
            raise StorageError('The selected Drive folder has changed. Quit Student and choose the correct folder.')
        records, completed = {}, {}
        for path in (self.folder / 'sessions').glob('*.json'):
            data = read_json(path)
            ident = data.get('id', '')
            if not IDENT.fullmatch(str(ident)) or path.stem != ident or data.get('parent') is not None and not IDENT.fullmatch(str(data['parent'])):
                raise StorageError('Drive contains a conflicting session file. No saved copy has been overwritten.')
            records[ident] = data
        if descriptor.get('initialSession') not in records:
            raise StorageError('The first saved session has not finished syncing. Wait for Google Drive, then reopen Student.')
        for ident, data in records.items():
            if data.get('parent') and data['parent'] not in records:
                raise StorageError('Some Student session files have not synced yet. Wait for Google Drive, then reopen Student.')
            path = self.folder / 'completed' / (ident + '.json')
            if path.exists():
                done = read_json(path)
                if done.get('id') != ident or not re.fullmatch(r'[a-f0-9]{64}', str(done.get('sha256', ''))):
                    raise StorageError('A Student completion record has not synced correctly.')
                completed[ident] = done
        heads = set(records) - {r['parent'] for r in records.values() if r.get('parent')}
        if len(heads) != 1:
            raise StorageError('Two laptops started from the same saved copy. Both sessions are preserved in Drive. Stop Student on both laptops and resolve the saved copies before continuing.')
        head = heads.pop()
        if any(ident not in completed for ident in records if ident != head):
            raise StorageError('A previous session has not finished syncing. Wait for Google Drive before continuing.')
        return records, completed, head

    def ensure_write(self):
        from server import AppError
        try:
            if self.error:
                raise StorageError(self.error)
            _, completed, head = self._graph()
            if head != self.session['id'] or head in completed:
                raise StorageError('Another session changed this Student folder. Quit and reopen after Drive has finished syncing.')
        except (StorageError, OSError) as exc:
            self.error = str(exc)
            raise AppError(self.error, 503) from exc

    def _restore(self, archive, expected):
        if not archive.is_file() or digest(archive) != expected:
            raise StorageError('The latest saved progress has not finished downloading. Wait for Google Drive to finish syncing; no older copy will be opened.')
        try:
            with tempfile.TemporaryDirectory(dir=self.cache) as temp:
                staging = Path(temp)
                with zipfile.ZipFile(archive) as saved:
                    if set(saved.namelist()) != set(FILES):
                        raise ValueError()
                    for name in FILES:
                        atomic_bytes(staging / name, saved.read(name))
                check_database(staging / 'river.sqlite3')
                for name in FILES:
                    os.replace(staging / name, self.cache / name)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise StorageError('The saved Student archive could not be opened. Your existing files are preserved; wait for Drive and retry.') from exc

    def checkpoint(self, store, force=False):
        """Post-commit failures keep durable local data and pause further mutations."""
        with store.lock:
            if self.error:
                return
            try:
                if store.db.in_transaction:
                    raise StorageError('A database update is still pending; it must finish before Student can make a checkpoint.')
                credentials = [(self.cache / name).read_bytes() for name in FILES[1:]]
                fingerprint = hashlib.sha256(store._saved_body.encode() + b''.join(credentials)).hexdigest()
                if not force and fingerprint == self.last_fingerprint:
                    return
                self._record(pending=True)
                self.ensure_write()
                with tempfile.TemporaryDirectory(dir=self.cache) as temp:
                    db_path = Path(temp) / 'river.sqlite3'
                    target = sqlite3.connect(db_path)
                    try:
                        store.db.backup(target)
                    finally:
                        target.close()
                    archive = Path(temp) / 'checkpoint.river'
                    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as saved:
                        saved.write(db_path, 'river.sqlite3')
                        for name, value in zip(FILES[1:], credentials):
                            saved.writestr(name, value)
                    # One closed archive contains the database, photos, and settings.
                    atomic_bytes(self.folder / 'sessions' / (self.session['id'] + '.river'), archive.read_bytes())
                self.last_fingerprint = fingerprint
                self.last_saved = stamp()
                self._record(pending=False)
                self.error = ''
            except Exception as exc:
                self.error = 'Student could not save to its Google Drive folder. Your latest work is safe on this Mac. Reconnect Drive and use Retry saving in the Student menu. ' + str(exc)

    def finish(self, store):
        self.checkpoint(store, force=True)
        if self.error:
            raise StorageError(self.error)
        archive = self.folder / 'sessions' / (self.session['id'] + '.river')
        write_json(self.folder / 'completed' / (self.session['id'] + '.json'),
                   {'id': self.session['id'], 'sha256': digest(archive), 'finishedAt': stamp()})

    def close(self):
        if self.lock:
            self.lock.close()
            self.lock = None

    def status(self):
        return {'mode': 'google-drive', 'workspaceId': self.workspace_id, 'lastSavedAt': self.last_saved,
                'error': self.error, 'message': self.error or 'Saved to your Google Drive folder. Check Google Drive for upload status. Quit Student and wait for sync before switching laptops.'}
