import os
import time


def hanging_attachment(raw, name):
    if raw == b'hang':
        while True:
            time.sleep(.05)
    return {'pages': [{'text': raw.decode(), 'lines': []}], 'fields': {}, 'warnings': [], 'pid': os.getpid()}


def hang_once_attachment(raw, name):
    from pathlib import Path
    marker=Path(raw.decode())
    if not marker.exists():
        marker.touch()
        while True:
            time.sleep(.05)
    return hanging_attachment(b'recovered',name)


def exited_worker(raw, name):
    os._exit(23)
