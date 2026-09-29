"""The receiver must report transport completion before import completion."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtNetwork import QLocalSocket

from app_common.send_to_app.receive import SingleInstanceReceiver


def test_chunked_receive_progress_precedes_import_completion() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    events = []
    completion = []
    paths = []

    def on_files(received, on_complete, on_progress):
        paths.extend(received)
        events.append('import_started')
        completion.append(on_complete)
        on_progress({'phase': 'import_pending', 'current': 0, 'total': len(received)})

    receiver = SingleInstanceReceiver(
        f'birdstamp_progress_test_{uuid4().hex}',
        on_files,
        on_transfer_progress=lambda payload: events.append(payload['phase']),
    )
    assert receiver.start()
    socket = QLocalSocket()
    try:
        socket.connectToServer(receiver._names[0])
        assert socket.waitForConnected(1000)
        frames = (
            {'type': 'begin', 'transfer_id': 'test', 'total_files': 1},
            {'type': 'chunk', 'transfer_id': 'test', 'total_files': 1, 'files': ['photo.jpg']},
            {'type': 'end', 'transfer_id': 'test', 'total_files': 1},
        )
        socket.write(b''.join(json.dumps(frame).encode('utf-8') + b'\n' for frame in frames))
        socket.flush()
        deadline = time.monotonic() + 2
        while not completion and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert len(completion) == 1
        assert len(paths) == 1
        assert events.index('received') < events.index('import_started')
        events.append('imported')
        completion[0]()
        app.processEvents()
        assert events[-1] == 'imported'
    finally:
        socket.abort()
        receiver.stop()
