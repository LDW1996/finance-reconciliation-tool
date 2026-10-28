from copy import deepcopy
from threading import Event
import time

from app.mail_tasks import MailManager
from app.outlook_probe import MailStore
from test_mail_matching import mail


def recognition():
    page = mail()['attachment_details'][0]['result']['pages'][0]
    return dict(pages=[deepcopy(page)], fields=deepcopy(page['fields']), warnings=[], engine='RapidOCR / PDFium（本地）')


def save(store, identity, raw=b'same-image', name='receipt.png', status='pending', result=None):
    store.save([dict(id=identity, body='', _attachments=[dict(name=name, raw=raw, status=status, result=result)])])


def idle(manager):
    deadline = time.monotonic() + 10
    while manager.status()['analysis_busy'] and time.monotonic() < deadline:
        time.sleep(.02)
    assert not manager.status()['analysis_busy'], manager.status()


def test_identical_concurrent_attachments_run_once_and_keep_independent_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr('app.mail_tasks.configured_workers', lambda requested: 4)
    store = MailStore(tmp_path/'mail.sqlite3')
    release, started = Event(), Event()
    calls = []
    def analyze(raw, name):
        calls.append(name)
        started.set()
        assert release.wait(5)
        return recognition()
    manager = MailManager(store, analyzer=analyze)
    try:
        for i in range(4):
            save(store, str(i), name=f'{i}.png')
            manager.analyze(str(i))
        assert started.wait(5)
        time.sleep(.15)
    finally:
        release.set()
        idle(manager)
    assert len(calls) == 1
    assets = [store.get(str(i))['attachment_details'][0] for i in range(4)]
    assert len({a['id'] for a in assets}) == 4
    payments = store.payment_index()
    assert len({payments[str(i)][0]['id'] for i in range(4)}) == 4
    for i, asset in enumerate(assets):
        assert asset['result'] == recognition()
        assert payments[str(i)][0]['evidence']['amount'][0]['attachment_id'] == asset['id']
    # A later copy uses the persisted result, including after process restart.
    save(store, 'later', name='forwarded.jpg')
    second = MailManager(MailStore(store.path), analyzer=analyze)
    idle(second)
    assert len(calls) == 1
    assert store.get('later')['attachment_details'][0]['status'] == 'done'


def test_legacy_completed_attachment_is_reused_without_modification(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    save(store, 'old', status='done', result=recognition())
    store.set_payments('old', [])
    before = store.get('old')
    save(store, 'new')
    calls = []
    manager = MailManager(store, analyzer=lambda *args: calls.append(args) or recognition())
    idle(manager)
    assert calls == []
    assert store.get('old') == before
    assert store.get('new')['attachment_details'][0]['result'] == recognition()


def test_changed_content_or_parser_type_does_not_reuse(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    calls = []
    manager = MailManager(store, analyzer=lambda raw, name: calls.append((raw,name)) or recognition())
    for identity, raw, name in [('a', b'first', 'a.png'), ('b', b'second', 'b.png'), ('c', b'first', 'c.pdf')]:
        save(store, identity, raw, name)
        manager.analyze(identity)
        idle(manager)
    assert len(calls) == 3


def test_failure_is_not_cached_and_does_not_block_next_copy(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    calls = []
    def analyze(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ValueError('bad image')
        return recognition()
    manager = MailManager(store, analyzer=analyze)
    for identity in ['a', 'b']:
        save(store, identity)
        manager.analyze(identity)
        idle(manager)
    assert len(calls) == 2
    assert store.get('a')['attachment_details'][0]['status'] == 'failed'
    assert store.get('b')['attachment_details'][0]['status'] == 'done'


def test_partial_page_failure_is_not_reused(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    partial = recognition()
    partial['warnings'] = ['第 2 页识别失败（RuntimeError），请核对原 PDF。']
    save(store, 'old', name='old.pdf', status='done', result=partial)
    store.set_payments('old', [])
    save(store, 'new', name='new.pdf')
    calls = []
    manager = MailManager(store, analyzer=lambda *args: calls.append(args) or recognition())
    idle(manager)
    assert len(calls) == 1


def test_obsolete_recognition_version_is_not_reused(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    save(store, 'old', status='done', result=recognition())
    asset = store.get('old')['attachment_details'][0]
    store.update_attachment('old', asset['id'], recognition_version='obsolete-version')
    store.set_payments('old', [])
    save(store, 'new')
    calls = []
    manager = MailManager(store, analyzer=lambda *args: calls.append(args) or recognition())
    idle(manager)
    assert len(calls) == 1
    assert store.get('old')['attachment_details'][0]['recognition_version'] == 'obsolete-version'


def test_changed_cached_source_is_validated_and_clear_removes_lookup(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    calls = []
    manager = MailManager(store, analyzer=lambda *args: calls.append(args) or recognition())
    save(store, 'original')
    manager.analyze('original')
    idle(manager)
    # Replacing source bytes invalidates the reference for its former digest.
    save(store, 'original', raw=b'changed')
    save(store, 'copy')
    manager.analyze('copy')
    idle(manager)
    assert len(calls) == 2
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0] == 1
        columns = [row[1] for row in db.execute('PRAGMA table_info(mail_recognition_lookup)')]
        assert columns == ['digest', 'kind', 'version', 'attachment_id']
    manager.clear()
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0] == 0
    assert store.path.with_suffix('.key').exists()
