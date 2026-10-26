from datetime import datetime, timezone
from email.message import EmailMessage

from app.outlook_probe import MailStore, candidates, parse_eml, scan_folder


def test_candidates_preserve_zeroes_and_are_only_suggestions():
    assert candidates('TRBTSTZH2606S, 082600028858 TRBTSTZH2606S\nhello 2026-10-22') == ['TRBTSTZH2606S', '082600028858']


def test_eml_parses_chinese_without_opening_attachments():
    mail = EmailMessage()
    mail['Subject'] = '请核对 TRBTSTZH2606S'
    mail['From'] = 'test@example.com'
    mail['Message-ID'] = '<sample@example.com>'
    mail.set_content('正文号码 082600028858')
    mail.add_attachment(b'not-executed', maintype='application', subtype='octet-stream', filename='test.xlsx')
    row = parse_eml(mail.as_bytes())
    assert row['candidates'] == ['TRBTSTZH2606S', '082600028858']
    assert row['attachments'] == ['test.xlsx']
    assert row['source'] == 'eml'


def test_store_deduplicates_and_preserves_chinese(tmp_path):
    store = MailStore(tmp_path / 'mail.sqlite3')
    row = {'id':'one','subject':'中文','body':'正文','sender':'a','received':'', 'attachments':[], 'candidates':[]}
    assert store.save([row]) == 1
    assert store.save([row]) == 0
    assert store.rows()[0]['body'] == '正文'
    changed = {**row, 'body':'更新内容'}
    assert store.save([changed]) == 0
    assert store.rows()[0]['body'] == '更新内容'


class Items:
    def __init__(self, rows): self.rows=rows; self.Count=len(rows)
    def Sort(self, field, descending): assert (field, descending) == ('[ReceivedTime]', True)
    def Item(self, index): return self.rows[index-1]


class Mail:
    Class = 43
    Subject = '请处理 TRBTSTZH2606S'
    Body = '正文'
    SenderEmailAddress = 'test@example.com'
    ReceivedTime = datetime(2026,10,22,tzinfo=timezone.utc)
    Attachments = type('Attachments', (), {'Count':0})()
    def __init__(self, identity): self.EntryID=identity


def test_scan_is_bounded_and_reports_partial_results():
    folder = type('Folder', (), {'Items':Items([Mail('one'), Mail('two')])})()
    result = scan_folder(folder, 'store', datetime(2026,10,1,tzinfo=timezone.utc), 1)
    assert len(result['rows']) == 1
    assert result['partial'] is True


def test_individual_message_failure_does_not_hide_later_messages():
    class Broken(Mail):
        @property
        def Body(self): raise RuntimeError('private internals')
    folder = type('Folder', (), {'Items':Items([Broken('bad'), Mail('good')])})()
    result = scan_folder(folder, 'store', datetime(2026,10,1,tzinfo=timezone.utc), 10)
    assert [r['subject'] for r in result['rows']] == [Mail.Subject]
    assert len(result['errors']) == 1
    assert 'private internals' not in str(result['errors'])


def test_old_messages_end_sorted_scan():
    mail=Mail('old'); mail.ReceivedTime=datetime(2026,9,1,tzinfo=timezone.utc)
    folder=type('Folder', (), {'Items':Items([mail])})()
    result=scan_folder(folder,'store',datetime(2026,10,1,tzinfo=timezone.utc),10)
    assert result['rows']==[] and result['partial'] is False


def test_cached_message_payload_is_encrypted(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='one',body='SENSITIVE_BODY_123',subject='secret')])
    with store.db() as db:
        raw=db.execute('SELECT payload FROM mail').fetchone()[0]
    assert 'SENSITIVE_BODY_123' not in raw and 'secret' not in raw
    assert store.get('one')['body']=='SENSITIVE_BODY_123'
    assert 'body' not in store.rows(include_body=False)[0]


def test_html_is_converted_to_text_and_does_not_load_external_resources():
    mail=EmailMessage(); mail['Subject']='html test'
    mail.set_content('<script>do_not_execute()</script><p>TESTBL20261022</p><img src="https://example.invalid/pixel">',subtype='html')
    row=parse_eml(mail.as_bytes())
    assert row['candidates']==['TESTBL20261022']
    assert 'do_not_execute' not in row['body'] and '<img' not in row['body']


def test_oversized_sample_is_rejected():
    import pytest
    with pytest.raises(ValueError): parse_eml(b'x'*(25*1024*1024+1))


def test_classic_outlook_dependencies_on_windows():
    import sys
    import pytest
    if sys.platform!='win32': pytest.skip('Windows COM dependencies')
    import pythoncom
    import pywintypes
    import win32com.client
    assert callable(pythoncom.CoInitialize) and callable(win32com.client.GetActiveObject)
