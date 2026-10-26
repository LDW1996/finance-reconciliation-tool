from email.message import EmailMessage
from io import BytesIO
import time

import pytest
from PIL import Image

from app.outlook_probe import MailStore, parse_eml


def png():
    out = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(out, format='PNG')
    return out.getvalue()


def test_import_keeps_encrypted_attachment_and_deduplicates(tmp_path):
    mail = EmailMessage()
    mail.set_content('付款凭证')
    mail.add_attachment(png(), maintype='image', subtype='png', filename='../receipt.png')
    row = parse_eml(mail.as_bytes())
    store = MailStore(tmp_path/'mail.sqlite3')
    store.save([row]); store.save([row])
    detail = store.get(row['id'])
    assert len(detail['attachment_details']) == 1
    asset = detail['attachment_details'][0]
    assert asset['name'] == 'receipt.png'
    assert asset['status'] == 'pending'
    assert store.attachment(row['id'], asset['id'])['raw'] == png()
    assert png() not in (tmp_path/'mail.sqlite3').read_bytes()
    assert '_attachments' not in detail
    store.clear()
    assert store.attachment(row['id'], asset['id']) is None


def test_later_read_recovers_missing_attachment_without_duplicating(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    row=dict(id='one',body='',attachments=['receipt.png'],_attachments=[dict(name='receipt.png',raw=b'',status='failed',error='读取失败')])
    store.save([row])
    row['_attachments']=[dict(name='receipt.png',raw=png(),status='pending',error='')]
    store.save([row])
    assets=store.get('one')['attachment_details']
    assert len(assets)==1 and assets[0]['status']=='pending'
    assert store.attachment('one',assets[0]['id'])['raw']==png()
    row['_attachments']=[dict(name='receipt.png',raw=b'',status='skipped',error='达到上限')]
    store.save([row])
    assert store.get('one')['attachment_details'][0]['status']=='pending'


def test_principal_fee_recipient_and_missing_payer():
    from app.mail_attachments import extract_fields
    lines = [dict(text=t, confidence=.99, box=None) for t in [
        'Beneficiary Name: SITC LOGISTIK INDONESIA',
        'Amount: IDR 1,500,000.00', 'Fee: IDR 2,500.00',
        '20 Oct 2026 10:12:04', 'Remarks: SLINXTF261019002']]
    fields = extract_fields(lines, 1)
    assert fields['payer'] == []
    assert fields['amount'][0]['value'] == '1500000.00'
    assert fields['fee'][0]['value'] == '2500.00'
    assert fields['date'][0]['value'] == '2026-10-20'
    assert fields['amount'][0]['page'] == 1


def test_bad_attachment_is_recorded_and_next_one_runs(tmp_path):
    from app.mail_tasks import MailManager
    def analyze(raw, name):
        if raw == b'bad': raise ValueError('文件损坏')
        return {'pages': [], 'fields': {}, 'warnings': []}
    store = MailStore(tmp_path/'mail.sqlite3')
    row = dict(id='one', body='', attachments=[], _attachments=[
        dict(name='bad.png', raw=b'bad', mime='image/png', status='pending'),
        dict(name='good.png', raw=png(), mime='image/png', status='pending')])
    store.save([row])
    manager = MailManager(store, analyzer=analyze)
    manager.analyze('one')
    for _ in range(100):
        if not manager.status()['analysis_busy']: break
        time.sleep(.01)
    assets = store.get('one')['attachment_details']
    assert [a['status'] for a in assets] == ['failed', 'done']
    assert assets[0]['error'] == '文件损坏'


def test_image_decode_rejects_invalid_data():
    from app.mail_attachments import analyze_attachment
    with pytest.raises(ValueError, match='图片'):
        analyze_attachment(b'not an image', 'bad.png')


def test_payer_labels_do_not_use_beneficiary():
    from app.mail_attachments import extract_fields
    lines = [dict(text=t, confidence=.99, box=None) for t in [
        'Debit A/C Name: PT. GOLDEN UNION OIL',
        'Credit A/C Name: SITC LOGISTIK INDONESIA',
        'Amount: IDR 11,062,665.00', 'Value Date: 20-Oct-26']]
    fields = extract_fields(lines, 1)
    assert fields['payer'][0]['value'] == 'PT. GOLDEN UNION OIL'
    assert fields['date'][0]['value'] == '2026-10-20'


def test_numeric_dates_and_indonesian_amount():
    from app.mail_attachments import extract_fields
    lines = [dict(text=t, confidence=.99, box=None) for t in [
        'Nama Pengirim: IIS KOMAIDAH', 'Nominal: Rp1.400.000',
        'Biaya Admin: Rp2.500', 'Waktu: 25-09-2026 09:13:26']]
    fields = extract_fields(lines, 1)
    assert fields['amount'][0]['value'] == '1400000.00'
    assert fields['payer'][0]['value'] == 'IIS KOMAIDAH'


def test_ocr_joined_date_time_and_ambiguous_decimal():
    from app.mail_attachments import extract_fields
    lines = [dict(text=t, confidence=.96, box=None) for t in [
        'Waktu:25-09-202609:13:26', 'Fee: IDR2.500.00',
        'Credit Name: SITCLOGISTIKINDONESIA']]
    fields = extract_fields(lines, 1)
    assert fields['date'][0]['value'] == '2026-09-25'
    assert fields['fee'] == []
    assert fields['identifiers'] == []


def text_pdf(count=1):
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>', b'']
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    kids=[]
    for _ in range(count):
        page_id=len(objects)+1
        content_id=page_id+1
        kids.append(f'{page_id} 0 R')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 300] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>'.encode())
        stream=b'BT /F1 18 Tf 20 250 Td (Debit A/C Name: TEST PAYER) Tj 0 -35 Td (Amount: IDR 1,400,000.00) Tj 0 -35 Td (Value Date: 25-09-2026) Tj ET'
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    objects[1]=f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {count} >>'.encode()
    data=b'%PDF-1.4\n'; offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    start=len(data)
    data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]: data+=f'{offset:010} 00000 n \n'.encode()
    return data+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


def test_text_pdf_does_not_call_ocr_and_limits_pages(monkeypatch):
    from app import mail_attachments
    monkeypatch.setattr(mail_attachments,'run_ocr',lambda image:pytest.fail('Text PDF should not call OCR'))
    result=mail_attachments.analyze_attachment(text_pdf(11),'receipt.pdf')
    assert len(result['pages']) == 10
    assert result['pages'][0]['method'] == 'pdf_text'
    assert result['fields']['amount'][0]['value'] == '1400000.00'
    assert result['fields']['payer'][0]['value'] == 'TEST PAYER'
    assert any('11' in warning for warning in result['warnings'])


def test_local_ocr_works_without_network(monkeypatch):
    import socket
    from PIL import ImageDraw, ImageFont
    from app.mail_attachments import analyze_attachment
    monkeypatch.setattr(socket.socket,'connect',lambda *args:pytest.fail('OCR must be offline'))
    image=Image.new('RGB',(1400,320),'white')
    draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=40)
    draw.text((30,30),'Amount: IDR 1,400,000.00',font=font,fill='black')
    draw.text((30,110),'Value Date: 25-09-2026',font=font,fill='black')
    output=BytesIO();image.save(output,format='PNG')
    result=analyze_attachment(output.getvalue(),'receipt.png')
    assert result['pages'][0]['method'] == 'ocr'
    assert result['fields']['amount'][0]['value'] == '1400000.00'
    assert result['fields']['date'][0]['value'] == '2026-09-25'
    assert result['pages'][0]['lines'][0]['box']


def test_attachment_routes_authorization_retry_and_cached_results(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    calls=[]
    def analyze(raw, name):
        calls.append(name)
        if len(calls)==1: raise ValueError('图片无法打开，请检查文件。')
        return dict(pages=[],fields={},warnings=[])
    store=MailStore(tmp_path/'mail.sqlite3')
    manager=mail_tasks.MailManager(store,analyzer=analyze)
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app)
    mail=EmailMessage();mail.set_content('synthetic only')
    mail.add_attachment(png(),maintype='image',subtype='png',filename='receipt.png')
    headers={'X-Finance-Local':'1','Origin':'http://127.0.0.1:5173'}
    response=client.post('/api/mail/import',headers=headers,files={'mail':('test.eml',mail.as_bytes())})
    assert response.status_code==200
    identity=response.json()['id']
    def wait():
        for _ in range(100):
            if not manager.status()['analysis_busy']: return
            time.sleep(.01)
        pytest.fail('Recognition did not finish')
    wait()
    route='/api/mail/messages/'+identity+'/analyze'
    assert client.post(route).status_code==403
    assert client.post(route,headers={**headers,'Origin':'https://example.invalid'}).status_code==403
    assert client.post(route,headers=headers).status_code==202
    wait()
    detail=client.get('/api/mail/messages/'+identity,headers=headers).json()
    assert detail['attachment_details'][0]['status']=='done'
    client.post('/api/mail/import',headers=headers,files={'mail':('test.eml',mail.as_bytes())})
    wait()
    assert calls==['receipt.png','receipt.png']
    assert client.post('/api/mail/messages/missing/analyze',headers=headers).status_code==404


def test_outlook_attachment_capture_is_local_and_tempfile_removed():
    from datetime import datetime, timezone
    from pathlib import Path
    from app.outlook_probe import scan_folder
    saved=[]
    class Attachment:
        FileName='receipt.png'
        Size=len(png())
        def SaveAsFile(self, path):
            saved.append(Path(path));Path(path).write_bytes(png())
    class Attachments:
        Count=1
        def Item(self, index): return Attachment()
    class Mail:
        Class=43;ReceivedTime=datetime.now(timezone.utc);EntryID='test'
        Subject='test';Body='test';SenderEmailAddress='test@example.invalid'
    Mail.Attachments=Attachments()
    class Items:
        Count=1
        def Sort(self,*args): pass
        def Item(self,index): return Mail()
    folder=type('Folder',(),{'Items':Items()})()
    result=scan_folder(folder,'store',datetime(2020,1,1,tzinfo=timezone.utc),1)
    assert result['rows'][0]['_attachments'][0]['raw']==png()
    assert saved and all(not path.exists() for path in saved)
