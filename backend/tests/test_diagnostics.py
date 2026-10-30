import io
import json
import zipfile
from app import diagnostics as d


def test_logs_are_bounded_private_and_exportable(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(d,'MAX_FILE',500)
    monkeypatch.setattr(d,'MAX_TOTAL',1500)
    d._seen.clear()
    for i in range(30):
        try:raise AttributeError('secret mail password body')
        except AttributeError as error:d.record('test',error,mail_id='private@example.com',index=i)
    files=list((tmp_path/'logs').glob('*.jsonl'))
    assert sum(f.stat().st_size for f in files)<=1500
    text=''.join(f.read_text() for f in files)
    assert 'private@example.com' not in text and 'secret mail' not in text
    with zipfile.ZipFile(io.BytesIO(d.export())) as archive:
        assert any(n.endswith('.jsonl') for n in archive.namelist())
    for file in files:
        import os
        os.utime(file,(1,1))
    d.prune(tmp_path/'logs')
    assert not list((tmp_path/'logs').glob('*.jsonl'))


def test_export_includes_queue_failure_logs_and_memory_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path))
    mail=tmp_path/'mail';mail.mkdir()
    for name in ('mail-errors.log','mail-errors.log.1'):
        (mail/name).write_text(json.dumps({'kind':'memory_exhausted','phase':'识别附件'})+'\n',encoding='utf-8')
    (mail/'mail.key').write_text('private key')
    d.record('memory_test',MemoryError())
    with zipfile.ZipFile(io.BytesIO(d.export())) as archive:
        assert 'mail-errors.log' in archive.namelist()
        assert 'mail-errors.log.1' in archive.namelist()
        assert 'mail.key' not in archive.namelist()
        rows=[json.loads(line) for n in archive.namelist() if n.endswith('.jsonl') for line in archive.read(n).decode().splitlines()]
        row=next(x for x in rows if x['stage']=='memory_test')
        assert row['memory']['pid']>0
        assert 'available_bytes' in row['memory']


def test_ocr_failure_records_dimensions_and_preserves_exception(tmp_path,monkeypatch):
    from app import mail_attachments as a
    from PIL import Image
    import pytest
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path))
    failure=MemoryError('private input must not be logged')
    def fail(image):raise failure
    monkeypatch.setattr(a,'run_ocr',fail)
    with Image.new('RGB',(20,30)) as image:
        with pytest.raises(MemoryError) as caught:a._page_result(image,2)
    assert caught.value is failure
    row=json.loads(next((tmp_path/'logs').glob('*.jsonl')).read_text(encoding='utf-8'))
    assert row['context']=={'page':2,'width':20,'height':30,'pixels':600}
    assert row['memory']['pid']>0
    assert 'private input' not in str(row)


def test_failed_diagnostic_write_is_visible_and_can_recover(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path))
    original=d.directory
    def fail():raise PermissionError('private sensitive path')
    monkeypatch.setattr(d,'directory',fail)
    assert d.record('failed_probe') is False
    assert d.health()['write_error']=='PermissionError'
    assert 'private sensitive' not in str(d.health())
    monkeypatch.setattr(d,'directory',original)
    assert d.record('recovered_probe') is True
    assert d.health()['write_error']==''


def test_fault_handler_does_not_swallow_disk_write_error(tmp_path,monkeypatch):
    import logging,pytest
    from app.mail_tasks import FaultLogHandler
    handler=FaultLogHandler(tmp_path/'fault.log',maxBytes=1024,backupCount=1,encoding='utf-8')
    def fail(record):raise OSError('disk write failed')
    monkeypatch.setattr(handler,'shouldRollover',fail)
    try:
        with pytest.raises(OSError,match='disk write failed'):
            handler.emit(logging.LogRecord('mail',logging.ERROR,'',0,'fault',(),None))
    finally:handler.close()


def test_diagnostic_status_exposes_actual_store_without_reading_database(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.mail_main import app
    from app import mail_tasks
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path))
    actual=tmp_path/'actual-mail';actual.mkdir()
    monkeypatch.setattr(mail_tasks,'_manager',SimpleNamespace(store=SimpleNamespace(path=actual/'mail.sqlite3'),state={'queue_log_write_error':'PermissionError'}))
    client=TestClient(app)
    response=client.get('/api/mail/diagnostics/status',headers={'X-Finance-Local':'1'})
    assert response.status_code==200
    result=response.json()
    assert result['data_directory']==str(actual)
    assert result['queue_log_exists'] is False
    assert result['queue_log_write_error']=='PermissionError'
    assert client.get('/api/mail/diagnostics/status').status_code==403
