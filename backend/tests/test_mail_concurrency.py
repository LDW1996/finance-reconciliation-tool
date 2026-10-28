from threading import Event, Lock
import time

import pytest

from app.outlook_probe import MailStore
from app.mail_tasks import MailManager


def wait_idle(manager):
    deadline = time.monotonic() + 15
    while manager.status()['analysis_busy'] and time.monotonic() < deadline:
        time.sleep(.02)
    assert not manager.status()['analysis_busy']


def test_four_tasks_overlap_without_duplicates_and_fifth_waits(tmp_path, monkeypatch):
    monkeypatch.setattr("app.mail_tasks.configured_workers", lambda requested: 4)
    release = Event()
    full = Event()
    lock = Lock()
    calls = []
    active = 0
    peak = 0

    def analyze(raw, name):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(name)
            if active == 4:
                full.set()
        assert release.wait(10)
        with lock:
            active -= 1
        if name == '0.png':
            raise ValueError('损坏')
        return dict(pages=[], fields={}, warnings=[])

    store = MailStore(tmp_path/'mail.sqlite3')
    manager = MailManager(store, analyzer=analyze, analysis_workers=4)
    try:
        for i in range(5):
            store.save([dict(id=str(i), body='', _attachments=[dict(
                name=f'{i}.png', raw=f'image-{i}'.encode(), status='pending')])])
            manager.analyze(str(i))
        assert full.wait(5)
        manager.analyze('0')
        status = manager.status()
        assert status['analysis_active'] == 4
        assert status['analysis_pending'] == 5
        assert len(calls) == 4
    finally:
        release.set()
        wait_idle(manager)
    assert peak == 4
    assert sorted(calls) == [f'{i}.png' for i in range(5)]
    assert not manager.analysis_queue
    assert store.get('0')['attachment_details'][0]['status'] == 'failed'
    assert all(store.get(str(i))['attachment_details'][0]['status'] == 'done' for i in range(1, 5))


@pytest.mark.parametrize('cpu,available,requested,expected', [
    (16, 8, 4, 4), (4, 8, 4, 2), (16, 4, 4, 2),
    (16, 1, 4, 1), (16, None, 4, 1), (16, 8, 1, 1),
])
def test_resource_policy(cpu, available, requested, expected):
    from app.mail_workers import worker_limit
    memory = None if available is None else available * 1024**3
    assert worker_limit(requested, cpu, memory) == expected


def test_spawned_parser_preserves_text_pdf_result():
    from app.mail_workers import analysis_pool
    from app.mail_attachments import analyze_attachment
    from test_mail_attachments import text_pdf
    with analysis_pool(1) as pool:
        result = pool.submit(analyze_attachment, text_pdf(), 'test.pdf').result(timeout=30)
    assert result['fields']['amount'][0]['value'] == '1400000.00'


def spawned_receipt(raw, directory):
    import os
    from pathlib import Path
    from app.mail_attachments import analyze_attachment
    pid = os.getpid()
    root = Path(directory)
    (root/str(pid)).touch()
    deadline = time.monotonic() + 25
    while len(list(root.iterdir())) < 4:
        if time.monotonic() > deadline:
            raise RuntimeError('Four spawned workers did not start')
        time.sleep(.05)
    return pid, analyze_attachment(raw, 'receipt.png')


def test_four_spawned_ocr_processes_preserve_amount_and_date(tmp_path):
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    from app.mail_workers import analysis_pool
    image = Image.new('RGB', (1400, 320), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    draw.text((30, 30), 'Amount: IDR 1,400,000.00', font=font, fill='black')
    draw.text((30, 110), 'Value Date: 25-09-2026', font=font, fill='black')
    raw = BytesIO()
    image.save(raw, format='PNG')
    with analysis_pool(4) as pool:
        futures = [pool.submit(spawned_receipt, raw.getvalue(), str(tmp_path)) for _ in range(4)]
        results = [future.result(timeout=40) for future in futures]
    assert len({pid for pid, _ in results}) == 4
    for _, result in results:
        assert result['fields']['amount'][0]['value'] == '1400000.00'
        assert result['fields']['date'][0]['value'] == '2026-09-25'


def test_worker_crash_leaves_mail_recoverable_and_stops_dispatch(tmp_path, monkeypatch):
    from concurrent.futures.process import BrokenProcessPool
    monkeypatch.setattr('app.mail_tasks.configured_workers', lambda requested: 1)
    calls = []

    def crash(raw, name):
        calls.append(name)
        raise BrokenProcessPool('worker exited')

    store = MailStore(tmp_path/'mail.sqlite3')
    for i in range(2):
        store.save([dict(id=str(i), body='', _attachments=[dict(
            name=f'{i}.png', raw=b'image', status='pending')])])
    manager = MailManager(store, analyzer=crash)
    wait_idle(manager)
    assert calls == ['0.png']
    assert manager.status()['analysis_pending'] == 2
    assert '暂停' in manager.status()['analysis_error']
    assert store.get('0')['attachment_details'][0]['status'] == 'running'
    manager.analyzer = lambda raw, name: dict(pages=[], fields={}, warnings=[])
    manager.analyze('0')
    wait_idle(manager)
    assert manager.status()['analysis_pending'] == 0
    assert manager.status()['analysis_error'] == ''
    assert all(store.get(str(i))['attachment_details'][0]['status'] == 'done' for i in range(2))


def test_new_mail_waits_for_previous_pool_shutdown(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    exiting = Event()
    release = Event()
    pools = []

    class SlowShutdown(ThreadPoolExecutor):
        def __enter__(self):
            pools.append(self)
            return super().__enter__()

        def __exit__(self, *args):
            if len(pools) == 1:
                exiting.set()
                assert release.wait(10)
            return super().__exit__(*args)

    monkeypatch.setattr('app.mail_tasks.ThreadPoolExecutor', SlowShutdown)
    store = MailStore(tmp_path/'mail.sqlite3')
    manager = MailManager(store, analyzer=lambda raw, name: dict(pages=[], fields={}, warnings=[]))
    try:
        store.save([dict(id='first', body='')])
        manager.analyze('first')
        assert exiting.wait(5)
        assert manager.status()['analysis_busy']
        store.save([dict(id='second', body='')])
        manager.analyze('second')
        assert len(pools) == 1
    finally:
        release.set()
        wait_idle(manager)
    assert store.payment_index(['second']) == {'second': []}
    assert manager.status()['analysis_pending'] == 0
