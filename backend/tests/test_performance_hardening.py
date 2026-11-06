from io import BytesIO
import asyncio
from threading import Event
from zipfile import ZipFile, ZIP_DEFLATED
import httpx
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from openpyxl import Workbook, load_workbook
from app import excel_upload, excel_routes
from app.outlook_probe import MailStore


def test_upload_and_expanded_limits(monkeypatch):
    monkeypatch.setattr(excel_upload, 'MAX_UPLOAD_BYTES', 8)
    assert asyncio.run(excel_upload.read_excel_upload(UploadFile(BytesIO(b'12345678')))) == b'12345678'
    with pytest.raises(HTTPException) as error:
        asyncio.run(excel_upload.read_excel_upload(UploadFile(BytesIO(b'123456789'))))
    assert error.value.status_code == 413
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/test.xml', b'x'*100)
    monkeypatch.setattr(excel_upload, 'MAX_EXPANDED_BYTES', 99)
    with pytest.raises(HTTPException) as error:
        excel_upload.check_excel_archive(out.getvalue())
    assert error.value.status_code == 413


def test_excel_computation_does_not_block_health(monkeypatch):
    started, release = Event(), Event()
    def slow(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return {'ok': True}
    monkeypatch.setattr(excel_routes, 'inspect_combined_workbook', slow)
    app=FastAPI();app.include_router(excel_routes.router)
    @app.get('/health')
    async def health():return {'ok': True}
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            work=asyncio.create_task(client.post('/api/inspect-combined', files={'workbook': ('test.xls', b'test')}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert (await asyncio.wait_for(client.get('/health'), 1)).status_code == 200
            finally:
                release.set()
                result=await work
            assert result.status_code==200
    asyncio.run(check())


def test_legacy_filter_index_reuses_metadata_and_refreshes_changes(tmp_path, monkeypatch):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='one',subject='Needle',sender='sender',body='private-body')])
    store.set_payment_summary('one',dict(status='ready',payers=['历史 Payer'],dates=[],amounts=[]))
    assert store.message_page(keyword='历史 payer',payment_filter='payment')['total']==1
    decoded=[];real=store.decode
    def track(value):decoded.append(value);return real(value)
    monkeypatch.setattr(store,'decode',track)
    assert store.message_page(payment_filter='ready')['total']==1
    assert len(decoded)==1  # only the requested mail, no summary/body scans
    with store.db(write=False) as db:
        encrypted=db.execute('SELECT search FROM mail_list_index').fetchone()[0]
        assert 'Payer' not in encrypted and 'Needle' not in encrypted
    store.set_payment_summary('one',dict(status='pending',payers=[],dates=[],amounts=[]))
    assert store.message_page(payment_filter='ready')['total']==0
    assert store.message_page(payment_filter='pending')['total']==1
    reopened=MailStore(store.path)
    assert reopened.message_page(keyword='needle')['total']==1
    store.clear()
    assert store.message_page(payment_filter='ready')['total']==0


def test_generated_strings_are_literal_and_numbers_remain_numeric():
    from app.reconciliation import _literal_export_strings
    book=Workbook();sheet=book.active
    sheet.append(['=1+1', '+sum(A1)', '@external', '-identifier', 42])
    _literal_export_strings(book)
    out=BytesIO();book.save(out)
    values=list(load_workbook(BytesIO(out.getvalue())).active[1])
    assert [c.data_type for c in values]==['s','s','s','s','n']
    assert values[0].value=='=1+1' and values[-1].value==42


def test_marked_source_retains_existing_formulas():
    import pandas as pd
    from app.reconciliation import _write_marked_data_excel
    book=Workbook();sheet=book.active
    sheet.append(['分配','公式']);sheet.append(['A','=1+1'])
    out=BytesIO();book.save(out)
    result=_write_marked_data_excel(out.getvalue(),'source.xlsx',pd.DataFrame({'分配':['A']}),'分配',{'A':'金额差异'})
    cell=load_workbook(BytesIO(result)).active['B2']
    assert cell.data_type=='f' and cell.value=='=1+1'
    assert cell.fill.fgColor.rgb=='FFD7B899'


def test_matching_batches_preserve_every_row_and_order(tmp_path, monkeypatch):
    from app.mail_workspace import MailWorkspace
    from app import mail_matching
    from test_mail_match_index import ledger_bytes, payment
    workspace=MailWorkspace(MailStore(tmp_path/'mail.sqlite3'))
    workspace.import_ledger(ledger_bytes(rows=205), 'bank.xlsx')
    workspace.store.set_payments('mail',[payment()])
    batches=[];real=mail_matching.rank_receipts
    def track(receipts, *args, **kwargs):
        batches.append(len(receipts))
        return real(receipts,*args,**kwargs)
    monkeypatch.setattr(mail_matching,'rank_receipts',track)
    workspace.matches.request(3,wait=True)
    rows=list(workspace.matches.rows(full=True))
    assert batches==[200,5]
    assert len(rows)==205 and workspace.matches.completed==205
    assert len({r['receipt']['id'] for r in rows})==205
    expected=real(workspace._dataset()['entries'],[payment()],limit=None)
    assert [(r['receipt']['id'],[(c['payment_id'],c['score']) for c in r['candidates']]) for r in rows]==[(r['receipt']['id'],[(c['payment_id'],c['score']) for c in r['candidates']]) for r in expected]
