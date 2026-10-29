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
