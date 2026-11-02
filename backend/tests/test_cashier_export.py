from io import BytesIO
import zipfile
import openpyxl
import pytest
from app.outlook_probe import MailStore
from app.mail_workspace import MailWorkspace
from test_mail_workspace import ledger_bytes, payment


def test_view_export_keeps_all_rows_literal_text_and_does_not_change_state(tmp_path):
    from app.cashier_export import build_cashier_package
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    store.set_payments('mail',[payment()])
    store.set_state('sync_progress',{'cursor':{'position':4},'scanned':100})
    before=store.get_state('sync_progress');reviews=workspace._review_map()
    path=build_cashier_package(workspace,day_window=3)
    try:
        with zipfile.ZipFile(path) as archive:
            assert 'cashier-review.xlsx' in archive.namelist()
            book=openpyxl.load_workbook(BytesIO(archive.read('cashier-review.xlsx')))
            assert book['银行流水'].max_row==3
            assert book['待核对'].max_row==3
            evidence=book['候选凭证']
            assert any(str(c.value).startswith('=Not a formula') and c.data_type=='s' for row in evidence for c in row)
            assert '原件缺失' in archive.read('README.txt').decode()
        assert workspace._review_map()==reviews
        assert store.get_state('sync_progress')==before
    finally:path.unlink()


def test_no_ledger_does_not_create_export(tmp_path):
    from app.cashier_export import build_cashier_package
    workspace=MailWorkspace(MailStore(tmp_path/'mail.sqlite3'))
    with pytest.raises(ValueError):build_cashier_package(workspace)


def test_attachments_are_deduplicated_safe_and_linked(tmp_path,monkeypatch):
    from app.cashier_export import build_cashier_package
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx');store.set_payments('mail',[payment()])
    monkeypatch.setattr(store,'attachment',lambda *args:{'name':'../../=bad.pdf','raw':b'%PDF-test'})
    path=build_cashier_package(workspace)
    try:
        with zipfile.ZipFile(path) as archive:
            assets=[name for name in archive.namelist() if name.startswith('attachments/')]
            assert len(assets)==1 and '..' not in assets[0] and archive.read(assets[0])==b'%PDF-test'
            book=openpyxl.load_workbook(BytesIO(archive.read('cashier-review.xlsx')))
            assert book['候选凭证']['N2'].hyperlink.target==assets[0]
    finally:path.unlink()


def test_oversized_package_fails_instead_of_silently_omitting_evidence(tmp_path,monkeypatch):
    from app import cashier_export
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx');store.set_payments('mail',[payment()])
    monkeypatch.setattr(store,'attachment',lambda *args:{'name':'test.pdf','raw':b'123456'})
    monkeypatch.setattr(cashier_export,'MAX_ATTACHMENT_BYTES',5)
    with pytest.raises(ValueError,match='512'):cashier_export.build_cashier_package(workspace)


def test_download_route_is_zip_and_uses_current_local_data(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    store=MailStore(tmp_path/'mail.sqlite3');manager=mail_tasks.MailManager(store)
    manager.workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    with TestClient(app) as client:
        response=client.get('/api/mail/matching/cashier-package',headers={'X-Finance-Local':'1'})
    assert response.status_code==200 and response.headers['content-type']=='application/zip'
    assert zipfile.ZipFile(BytesIO(response.content)).testzip() is None


def test_confirmed_state_and_english_output(tmp_path):
    from app.cashier_export import build_cashier_package
    from app.mail_online import OUTPUT_LANGUAGE
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    meta=workspace.import_ledger(ledger_bytes(),'bank.xlsx');store.set_payments('mail',[payment()])
    row=workspace.matching()['rows'][0]
    workspace.review(meta['id'],row['receipt']['id'],'payment','confirmed',3)
    before=workspace._review_map()
    token=OUTPUT_LANGUAGE.set('en')
    try:path=build_cashier_package(workspace)
    finally:OUTPUT_LANGUAGE.reset(token)
    try:
        with zipfile.ZipFile(path) as archive:
            book=openpyxl.load_workbook(BytesIO(archive.read('cashier-review.xlsx')))
            assert book['Bank transactions']['I2'].value=='Confirmed'
            assert book['Needs review'].max_row==2
            assert book['Candidate evidence']['D2'].value=='Confirmed'
        assert workspace._review_map()==before
    finally:path.unlink()
