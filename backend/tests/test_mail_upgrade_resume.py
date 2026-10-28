"""Compatibility with the persisted single-worker queue and encrypted mail data."""
import time

from app.mail_storage import AnalysisQueue
from app.mail_tasks import MailManager
from app.mail_workspace import MailWorkspace
from app.outlook_probe import MailStore


def wait_idle(manager):
    deadline = time.monotonic() + 10
    while manager.status()['analysis_busy'] and time.monotonic() < deadline:
        time.sleep(.02)
    assert not manager.status()['analysis_busy']


def test_upgrade_keeps_completed_evidence_reviews_and_resumes_only_unfinished(tmp_path, monkeypatch):
    monkeypatch.setattr('app.mail_tasks.configured_workers', lambda requested: 4)
    path = tmp_path/'mail.sqlite3'
    old = MailStore(path)
    MailWorkspace(old)
    for identity, states in [('completed', ['done']), ('interrupted', ['done', 'running', 'pending']),
                             ('not_queued', ['pending']), ('failed', ['failed'])]:
        old.save([dict(id=identity, subject=identity, body='Original body', _attachments=[
            dict(name=f'{identity}-{n}.png', raw=f'{identity}-{n}'.encode(), status=state,
                 result={'legacy': 'keep exactly'} if state == 'done' else None)
            for n, state in enumerate(states)])])
        old.set_payments(identity, [], {'status': 'legacy-summary'})
    queue = AnalysisQueue(old)
    queue.append(('interrupted', False))
    assert queue.popleft() == ('interrupted', False)  # old process died after claim
    progress = dict(source=['pst', 'inbox', 90], phase='history', cursor={'position': 2823},
                    scanned=2823, complete=False, start='2026-08-01', end='2026-10-28')
    settings = dict(enabled=False, store='pst', folder='inbox', days=90, limit=100,
                    seconds=300, resume_on_start=False)
    old.set_state('sync_progress', progress)
    old.set_state('poll_settings', settings)
    with old.db() as db:
        db.execute('INSERT INTO mail_review VALUES(?,?)', ('receipt-1', old.encode(dict(
            state='confirmed', payment_id='legacy-payment', receipt_id='receipt-1'))))
        db.execute('INSERT INTO mail_ledger VALUES(?,?,?)', ('ledger-1', old.encode({'name': 'old.xlsx'}), b'old-encrypted-ledger'))
        # Encrypted bytes must remain identical, not merely equivalent after decode.
        before = {table: db.execute(f'SELECT * FROM {table}').fetchall()
                  for table in ('mail', 'mail_review', 'mail_ledger', 'mail_state')}
        completed_metadata = db.execute("SELECT metadata,content FROM mail_attachment WHERE mail_id='completed'").fetchall()
        completed_payments = db.execute("SELECT payload FROM mail_payments WHERE mail_id='completed'").fetchone()
    key = path.with_suffix('.key').read_bytes()
    first_page = old.get('interrupted')['attachment_details'][0]
    calls = []

    def analyze(raw, name):
        calls.append(name)
        return dict(pages=[], fields={}, warnings=[])

    reopened = MailStore(path)
    manager = MailManager(reopened, analyzer=analyze, analysis_workers=4)
    wait_idle(manager)
    assert sorted(calls) == ['interrupted-1.png', 'interrupted-2.png', 'not_queued-0.png']
    assert reopened.mail_count() == 4
    assert path.with_suffix('.key').read_bytes() == key
    assert reopened.get('interrupted')['attachment_details'][0] == first_page
    assert reopened.get('failed')['attachment_details'][0]['status'] == 'failed'
    assert reopened.get_state('sync_progress') == progress
    assert reopened.get_state('poll_settings') == settings
    with reopened.db() as db:
        for table in ('mail', 'mail_review', 'mail_ledger'):
            assert db.execute(f'SELECT * FROM {table}').fetchall() == before[table]
        assert db.execute("SELECT metadata,content FROM mail_attachment WHERE mail_id='completed'").fetchall() == completed_metadata
        assert db.execute("SELECT payload FROM mail_payments WHERE mail_id='completed'").fetchone() == completed_payments
    assert manager.status()['analysis_pending'] == 0
    # A second launch must perform no OCR at all.
    second = MailManager(MailStore(path), analyzer=analyze)
    wait_idle(second)
    assert len(calls) == 3


def test_missing_original_key_refuses_to_open_existing_data(tmp_path):
    import pytest
    path = tmp_path/'mail.sqlite3'
    store = MailStore(path)
    store.save([dict(id='old', body='keep')])
    original_database = path.read_bytes()
    path.with_suffix('.key').unlink()
    with pytest.raises(ValueError, match='密钥缺失'):
        MailStore(path)
    assert not path.with_suffix('.key').exists()
    assert path.read_bytes() == original_database
