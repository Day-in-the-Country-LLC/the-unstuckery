"""Two simulated laptops, real SQLite, and deliberately reordered Drive files."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from drive_storage import DriveStorage, StorageError, MARKER, read_json, write_json
from onboarding import memory_folder, legacy_data, load_location
from server import RiverStore, AppError


class DriveStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.drive = self.root / 'Drive' / 'Student App Memory'
        self.drive.mkdir(parents=True)
        self.opened = []

    def tearDown(self):
        for sync, store in reversed(self.opened):
            if store:
                store.db.close()
            sync.close()
        self.temp.cleanup()

    def open(self, laptop='A', create=False, seed=None, drive=None):
        sync = DriveStorage(drive or self.drive, self.root / laptop)
        self.opened.append((sync, None))
        cache = sync.prepare(create=create, seed=seed)
        store = RiverStore(cache)
        store.storage = sync
        self.opened[-1] = (sync, store)
        sync.checkpoint(store, force=True)
        self.assertFalse(sync.error, sync.error)
        return sync, store

    def finish(self, sync, store):
        sync.finish(store)
        store.db.close()
        sync.close()
        self.opened.remove((sync, store))

    def test_full_handoff_restores_state_photos_pin_and_credentials_at_different_paths(self):
        sync, store = self.open(create=True)
        store.authenticate('5937', setup=True)
        store.state['syntheticMemory'] = {'answer': 'test-only answer', 'timer': 57}
        store.transaction(lambda: store.mutate('/api/browser-memory', {'key':'river-answer-synthetic-question', 'value':'An unfinished explanation.'}, 'river'))
        store.db.execute('INSERT INTO assignment_photos VALUES (?,?,?)', ('photo', 'submission', b'fake-image-for-storage-test'))
        store.save()
        (sync.cache / 'review.env').write_text('OPENAI_API_KEY=synthetic-key\n')
        (sync.cache / 'canvas.json').write_text('{"synthetic": "connection"}')
        token = store.assistant_token
        self.finish(sync, store)
        # Drive is mounted at a different absolute path on laptop B.
        other = self.root / 'different-mount' / 'memory'
        shutil.copytree(self.drive, other)
        second, restored = self.open('B', drive=other)
        self.assertEqual(restored.state['syntheticMemory']['answer'], 'test-only answer')
        self.assertEqual(restored.state['browserMemory']['river-answer-synthetic-question'], 'An unfinished explanation.')
        self.assertEqual(restored.db.execute('SELECT content FROM assignment_photos').fetchone()[0], b'fake-image-for-storage-test')
        self.assertEqual(restored.assistant_token, token)
        self.assertEqual((second.cache / 'review.env').read_text(), 'OPENAI_API_KEY=synthetic-key\n')
        self.assertEqual(json.loads((second.cache / 'canvas.json').read_text()), {'synthetic': 'connection'})
        self.assertTrue(restored.authenticate('5937'))
        self.assertEqual((second.cache / 'review.env').stat().st_mode & 0o777, 0o600)

    def test_other_laptop_cannot_open_active_session(self):
        self.open(create=True)
        with self.assertRaisesRegex(StorageError, 'still open'):
            self.open('B')

    def test_same_laptop_cannot_open_same_cache_twice(self):
        self.open(create=True)
        with self.assertRaisesRegex(StorageError, 'already open'):
            self.open('A')

    def test_interrupted_session_resumes_local_committed_work(self):
        sync, store = self.open(create=True)
        store.state['syntheticMemory'] = 'resumed'
        store.save()
        ident = sync.session['id']
        store.db.close(); sync.close(); self.opened.remove((sync, store))
        resumed, loaded = self.open('A')
        self.assertEqual(resumed.session['id'], ident)
        self.assertEqual(loaded.state['syntheticMemory'], 'resumed')

    def test_completion_record_arriving_before_archive_refuses_stale_progress(self):
        sync, store = self.open(create=True)
        ident = sync.session['id']
        self.finish(sync, store)
        archive = self.drive / 'sessions' / (ident + '.river')
        original = archive.read_bytes()
        archive.write_bytes(b'partly downloaded')
        with self.assertRaisesRegex(StorageError, 'finish.*download'):
            self.open('B')
        archive.write_bytes(original)

    def test_missing_drive_never_creates_an_empty_replacement(self):
        sync, store = self.open(create=True)
        self.finish(sync, store)
        moved = self.drive.with_name('temporarily-offline')
        self.drive.rename(moved)
        with self.assertRaisesRegex(StorageError, 'unavailable'):
            self.open('B')
        self.assertFalse(self.drive.exists())

    def test_checkpoint_failure_retains_commit_pauses_writes_and_retries(self):
        sync, store = self.open(create=True)
        original = __import__('drive_storage').atomic_bytes
        def fail_shared(path, value):
            if Path(path).parent == self.drive / 'sessions':
                raise OSError('synthetic disk unavailable')
            return original(path, value)
        with patch('drive_storage.atomic_bytes', side_effect=fail_shared):
            store.transaction(lambda: store.state.update(syntheticMemory='durable'))
        self.assertTrue(sync.error)
        self.assertEqual(json.loads(store.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])['syntheticMemory'], 'durable')
        with self.assertRaises(AppError):
            store.transaction(lambda: store.state.update(syntheticMemory='must not save'))
        sync.error = ''
        sync.checkpoint(store, force=True)
        self.assertFalse(sync.error)
        self.finish(sync, store)
        _, restored = self.open('B')
        self.assertEqual(restored.state['syntheticMemory'], 'durable')

    def test_divergent_sessions_pause_instead_of_overwriting(self):
        sync, store = self.open(create=True)
        self.finish(sync, store)
        branch1, first = self.open('A')
        ident = uuid.uuid4().hex
        write_json(self.drive / 'sessions' / (ident + '.json'), dict(branch1.session, id=ident, device='Synthetic B'))
        with self.assertRaisesRegex(AppError, 'Two laptops'):
            first.transaction(lambda: first.state.update(lost='no'))
        self.assertNotIn('lost', first.state)

    def test_original_data_import_is_nondestructive_and_requires_old_app_to_stop(self):
        old = self.root / 'old-data'
        old_store = RiverStore(old)
        old_store.state['syntheticMemory'] = 'from old app'
        old_store.save(); old_store.db.close()
        original = (old / 'river.sqlite3').read_bytes()
        with (old / '.server-lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(StorageError, 'Quit the old'):
                self.open(create=True, seed=old)
        _, imported = self.open('B', create=True, seed=old)
        self.assertEqual(imported.state['syntheticMemory'], 'from old app')
        self.assertEqual((old / 'river.sqlite3').read_bytes(), original)

    def test_parent_or_exact_memory_folder_resolves_to_same_location(self):
        self.open(create=True)
        self.assertEqual(memory_folder(self.drive.parent), self.drive.resolve())
        self.assertEqual(memory_folder(self.drive), self.drive.resolve())
        self.assertIsNone(load_location(self.root / 'missing-support'))

    def test_browser_memory_is_bounded_and_cannot_mutate_progress(self):
        _, store = self.open(create=True)
        for body in ({'key':'auth', 'value':'fake'}, {'key':'river-timer-sound', 'value':'a'*20001}):
            with self.assertRaises(AppError):
                store.transaction(lambda: store.mutate('/api/browser-memory', body, 'river'))
        with self.assertRaises(AppError):
            store.transaction(lambda: store.mutate('/api/browser-memory', {'key':'river-timer-sound', 'value':'off'}, 'assistant'))
        store.transaction(lambda: store.mutate('/api/browser-memory', {'key':'river-answer-demo', 'value':'draft'}, 'river'))
        store.transaction(lambda: store.mutate('/api/browser-memory', {'key':'river-answer-demo', 'value':None}, 'river'))
        self.assertNotIn('river-answer-demo', store.state['browserMemory'])


if __name__ == '__main__':
    unittest.main()
