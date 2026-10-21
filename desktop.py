"""macOS menu-bar launcher; the application server stays on loopback."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from urllib.request import urlopen
import webbrowser

APP_SUPPORT = Path(os.environ.get('RIVER_APP_SUPPORT', Path.home() / 'Library/Application Support/RiverAfterSchool'))


def default_data_dir():
    value = os.environ.get('RIVER_DATA_DIR')
    return Path(value).expanduser() if value else None


def is_ready(url):
    try:
        with urlopen(url + '/api/health', timeout=1) as response:
            return json.load(response).get('app') == 'river-after-school'
    except Exception:
        return False


def self_test():
    """Exercise bundled resources and backend without real data or network accounts."""
    from server import create_server, ROOT
    from drive_storage import DriveStorage
    from onboarding import memory_folder
    with tempfile.TemporaryDirectory(prefix='river-package-test-') as folder:
        root = Path(folder)
        server = create_server(root / 'data', port=0, canvas_vault=root / 'canvas.json')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            assert is_ready(url), 'Health endpoint failed'
            for resource in (ROOT / 'static').iterdir():
                if resource.is_file():
                    path = '/' if resource.name == 'index.html' else '/' + resource.name
                    with urlopen(url + path, timeout=5) as response:
                        assert response.status == 200 and response.read(), path
            assert json.loads((ROOT / 'draft-plan.json').read_text())['items'] == []
            assert json.loads((ROOT / 'backlog-config.json').read_text())['driveFolderUrl'] == ''
            from ai_review import read_config
            config = root / 'review.env'
            config.write_text('OPENAI_API_KEY=\n')
            config.chmod(0o600)
            assert not read_config(config)['key']
            print(json.dumps({'ok': True, 'app': 'river-after-school', 'bundled': bool(getattr(sys, 'frozen', False))}), flush=True)
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
            server.store.db.close()
        shared = root / 'Drive' / 'Student App Memory'
        shared.mkdir(parents=True)
        assert memory_folder(shared) == shared.resolve()
        first = DriveStorage(shared, root / 'laptop-a')
        cache = first.prepare(create=True)
        initial = create_server(cache, port=0, storage=first, canvas_vault=cache / 'canvas.json')
        initial.store.transaction(lambda: initial.store.state.update(packagedHandoff='synthetic'))
        initial.server_close()
        first.finish(initial.store)
        initial.store.db.close()
        first.close()
        second = DriveStorage(shared, root / 'laptop-b')
        cache = second.prepare()
        restored = create_server(cache, port=0, storage=second, canvas_vault=cache / 'canvas.json')
        assert restored.store.state['packagedHandoff'] == 'synthetic'
        restored.server_close()
        second.finish(restored.store)
        restored.store.db.close()
        second.close()
        print(json.dumps({'driveHandoff': 'ok'}), flush=True)


def run_desktop(data_dir, port, drive_folder=None, seed=None):
    import rumps
    from server import create_server
    from drive_storage import DriveStorage, StorageError, read_json, MARKER
    from onboarding import load_location, configure

    url = f'http://127.0.0.1:{port}'
    if is_ready(url):
        if data_dir is None:
            location = load_location(APP_SUPPORT)
            with urlopen(url + '/api/health', timeout=1) as response:
                running = json.load(response)
            expected = read_json(Path(location['folder']) / MARKER).get('workspaceId') if location else None
            if not expected or expected != running.get('workspaceId'):
                rumps.alert('Quit the other Student app first', 'A different Student instance is already open on this Mac. Quit it, then reopen this app to choose your Google Drive folder.')
                return 1
        webbrowser.open(url)
        return

    APP_SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    handler = RotatingFileHandler(APP_SUPPORT / 'river.log', maxBytes=1_000_000, backupCount=2)
    logging.basicConfig(handlers=[handler], level=logging.INFO)
    storage = None
    try:
        if data_dir is None:
            location = {'folder': str(drive_folder), 'create': True, 'seed': str(seed) if seed else None} if drive_folder else load_location(APP_SUPPORT) or configure(APP_SUPPORT)
            if location is None:
                handler.close()
                return 0
            storage = DriveStorage(location['folder'], APP_SUPPORT)
            data_dir = storage.prepare(seed=location.get('seed'), create=location.get('create', False))
            server = create_server(data_dir, port=port, ai_review=True, storage=storage,
                                   review_config=data_dir / 'review.env', canvas_vault=data_dir / 'canvas.json')
        else:
            server = create_server(data_dir, port=port, ai_review=True)
    except Exception as exc:
        if storage:
            storage.close()
        logging.exception('Could not start Student')
        logging.getLogger().removeHandler(handler)
        handler.close()
        if storage:
            if rumps.alert('Student could not open this saved folder', str(exc), ok='Choose folder', cancel='Close'):
                if configure(APP_SUPPORT):
                    return run_desktop(None, port)
        else:
            rumps.alert('Student could not start', str(exc) + '\nSee ~/Library/Application Support/RiverAfterSchool/river.log.')
        return 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    closed = False

    @rumps.events.before_quit
    def close_server():
        # Cocoa termination can bypass Python's finally block.
        nonlocal closed
        if closed:
            return
        closed = True
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        try:
            if storage:
                storage.finish(server.store)
        finally:
            server.store.db.close()
            if storage:
                storage.close()

    class RiverApp(rumps.App):
        def __init__(self):
            super().__init__('Student After School', title='Student', quit_button=None)
            self.menu = ['Open Student', 'Open saved data folder', 'Open connection settings folder', None, 'Quit Student']
            if storage:
                self.menu.insert_before('Quit Student', rumps.MenuItem('Google Drive status', callback=self.storage_status))
                self.menu.insert_before('Quit Student', rumps.MenuItem('Retry saving', callback=self.retry_saving))
                self.menu.insert_before('Quit Student', rumps.MenuItem('Choose folder for next launch…', callback=self.choose_storage))

        def storage_status(self, _):
            rumps.alert('Google Drive storage', str(storage.folder) + '\n\n' + storage.status()['message'])

        def retry_saving(self, _):
            storage.error = ''
            storage.checkpoint(server.store, force=True)
            self.storage_status(None)

        def choose_storage(self, _):
            try:
                location = configure(APP_SUPPORT)
                if location:
                    rumps.alert('Folder selected for next launch', 'Quit and reopen Student to use the chosen folder. The current saved copy stays in its existing folder.')
            except StorageError as exc:
                rumps.alert('Folder setup needs attention', str(exc))

        @rumps.clicked('Open Student')
        def open_river(self, _):
            webbrowser.open(url)

        @rumps.clicked('Open saved data folder')
        def open_data(self, _):
            subprocess.run(['open', str((storage.folder if storage else data_dir).resolve())], check=False)

        @rumps.clicked('Open connection settings folder')
        def open_settings(self, _):
            subprocess.run(['open', str(storage.cache if storage else APP_SUPPORT)], check=False)

        @rumps.clicked('Quit Student')
        def quit(self, _):
            if storage:
                storage.checkpoint(server.store, force=True)
                if storage.error:
                    rumps.alert('Your latest work still needs to save', storage.error)
                    return
            rumps.quit_application()

    try:
        webbrowser.open(url)
        application = RiverApp()
        timer = None
        if storage:
            def check_storage(_):
                storage.checkpoint(server.store)
                application.title = 'Student · Save paused' if storage.error else 'Student'
            timer = rumps.Timer(check_storage, 5)
            timer.start()
        application.run()
    finally:
        if 'timer' in locals() and timer:
            timer.stop()
        close_server()
        rumps.events.before_quit.unregister(close_server)
        logging.getLogger().removeHandler(handler)
        handler.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description='Student After School for macOS')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--port', type=int, default=4317)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--drive-folder', type=Path, help='Use this Student memory folder; normally selected during onboarding')
    parser.add_argument('--import-data', type=Path, help='Seed a new Drive folder from a stopped older installation')
    parser.add_argument('--choose-drive-folder', action='store_true', help='Choose a different Drive folder before launching')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.data_dir and args.drive_folder:
        parser.error('Choose --data-dir or --drive-folder, not both.')
    if args.choose_drive_folder:
        from onboarding import configure
        if configure(APP_SUPPORT) is None:
            return 0
    return run_desktop(args.data_dir, args.port, args.drive_folder, args.import_data)


if __name__ == '__main__':
    raise SystemExit(main())
