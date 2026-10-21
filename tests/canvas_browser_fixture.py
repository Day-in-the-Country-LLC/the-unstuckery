"""Disposable Canvas UI fixture. Fake network and school records only."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from test_canvas import FakeCanvas

folder = tempfile.TemporaryDirectory(prefix='river-canvas-browser-')
fake = FakeCanvas()
fake.rows.extend([
    {'id': 10, 'name': 'Synthetic reading — no deadline', 'description': '<p>Read the first chapter. Bring two questions to class.</p><a href="https://docs.google.com/example">Reading</a>', 'submission_types': ['on_paper']},
    {'id': 11, 'name': 'Synthetic revision <script>ignored</script>', 'description': '<p>Show your reasoning for each step.</p>', 'due_at': '2026-09-08T15:00:00Z', 'submission_types': ['online_upload'], 'submissions': [{'user_id': 42, 'workflow_state': 'unsubmitted', 'missing': True}]},
])
server = create_server(Path(folder.name)/'data', 4320, canvas_transport=fake, canvas_vault=Path(folder.name)/'private.json')
session = server.store.authenticate('2468', setup=True)  # Synthetic fixture only.
server.canvas.action('test-connect', {'token': 'parent-secret', 'ownAccount': True}, session, 'http://127.0.0.1:4320')
server.canvas.action('sync', {}, session, 'http://127.0.0.1:4320')
server.canvas.worker.join(3)
print('Synthetic Canvas fixture ready at http://127.0.0.1:4320; test-only PIN 2468.', flush=True)
try: server.serve_forever()
except KeyboardInterrupt: pass
finally:
    server.server_close(); server.store.db.close(); folder.cleanup()
