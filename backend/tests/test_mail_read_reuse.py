from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.outlook_probe import MailStore, scan_folder, scan_folder_page

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = START + timedelta(days=30)


class Attachment:
    FileName = 'receipt.png'
    Size = 8
    saves = 0
    broken = False
    def SaveAsFile(self, path):
        self.saves += 1
        if self.broken:
            raise RuntimeError('download failed')
        Path(path).write_bytes(b'receipt!')


class Message:
    Class = 43
    Subject = 'test'
    SenderEmailAddress = 'test@example.invalid'
    ReceivedTime = START + timedelta(days=1)
    revision = b'first-change'
    body_reads = 0
    def __init__(self, identity):
        self.EntryID = identity
        self.attachment = Attachment()
        self.Attachments = type('Attachments', (), {'Count':1,'Item':lambda _, i:self.attachment})()
        self.PropertyAccessor = self
    @property
    def Body(self):
        self.body_reads += 1
        return 'original body'
    def GetProperty(self, tag):
        if tag.endswith('0x65E20102'):
            if self.revision is None:
                raise RuntimeError('PST has no change key')
            return self.revision
        return '<'+self.EntryID+'>'


class Items:
    def __init__(self, messages):
        self.messages = messages
        self.Count = len(messages)
    def Item(self, index):return self.messages[index-1]
    def Sort(self, *args):pass
    def Restrict(self, *args):return self


def folder(messages):return type('Folder', (), {'Items':Items(messages)})()


def test_same_complete_message_skips_body_and_attachment_download(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    message = Message('one')
    first = scan_folder(folder([message]), 'store', START, 100)
    store.save(first['rows'])
    before = store.get(first['rows'][0]['id'])
    second = scan_folder(folder([message]), 'store', START, 100, known_unchanged=store.mail_unchanged)
    assert second['rows'] == []
    assert second['read'] == 1 and second['unchanged'] == 1
    assert message.body_reads == 1 and message.attachment.saves == 1
    assert store.get(before['id']) == before


def test_changed_missing_or_unstable_change_key_forces_reread(tmp_path):
    for label, revision in [('changed', b'second'), ('unavailable', None)]:
        store = MailStore(tmp_path/(label+'.sqlite3'))
        message = Message(label)
        first = scan_folder(folder([message]), 'store', START, 100)
        store.save(first['rows'])
        message.revision = revision
        second = scan_folder(folder([message]), 'store', START, 100, known_unchanged=store.mail_unchanged)
        assert len(second['rows']) == 1 and second['unchanged'] == 0
        assert message.body_reads == 2 and message.attachment.saves == 2


def test_incomplete_attachment_capture_is_downloaded_again(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    message = Message('bad')
    message.attachment.broken = True
    first = scan_folder(folder([message]), 'store', START, 100)
    store.save(first['rows'])
    message.attachment.broken = False
    second = scan_folder(folder([message]), 'store', START, 100, known_unchanged=store.mail_unchanged)
    assert len(second['rows']) == 1 and second['unchanged'] == 0
    assert second['rows'][0]['_attachments'][0]['raw'] == b'receipt!'
    assert message.attachment.saves == 2


def test_all_unchanged_pages_advance_cursor_and_count_scanned_messages(tmp_path):
    store = MailStore(tmp_path/'mail.sqlite3')
    messages = [Message(str(i)) for i in range(3)]
    selected = folder(messages)
    initial = scan_folder(selected, 'store', START, 100)
    store.save(initial['rows'])
    cursor = None
    scanned = unchanged = 0
    for _ in range(5):
        result = scan_folder_page(selected, 'store', START, END, 1, cursor, known_unchanged=store.mail_unchanged)
        assert result['rows'] == []
        scanned += result['read'];unchanged += result['unchanged']
        if not result['partial']:break
        assert result['cursor'] != cursor
        cursor = result['cursor']
    else:raise AssertionError('all-skipped pages failed to finish')
    assert scanned == unchanged == 3
    assert all(m.body_reads == 1 and m.attachment.saves == 1 for m in messages)


def test_revision_changing_during_capture_is_not_reused(tmp_path):
    class Changing(Message):
        revisions=0
        def GetProperty(self, tag):
            if tag.endswith('0x65E20102'):
                self.revisions+=1
                return str(self.revisions).encode()
            return super().GetProperty(tag)
    store=MailStore(tmp_path/'mail.sqlite3')
    message=Changing('changing')
    first=scan_folder(folder([message]),'store',START,100)
    assert first['rows'][0]['outlook_change_key'] is None
    store.save(first['rows'])
    second=scan_folder(folder([message]),'store',START,100,known_unchanged=store.mail_unchanged)
    assert len(second['rows'])==1 and second['unchanged']==0
    assert message.attachment.saves==2


def test_metadata_only_revision_change_refreshes_marker_and_preserves_completed_result(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    message=Message('one')
    first=scan_folder(folder([message]),'store',START,100)
    store.save(first['rows'])
    identity=first['rows'][0]['id']
    asset=store.get(identity)['attachment_details'][0]
    store.update_attachment(identity,asset['id'],status='done',result={'legacy':'keep'})
    before=store.get(identity)['attachment_details'][0]
    message.revision=b'metadata-only-change'
    second=scan_folder(folder([message]),'store',START,100,known_unchanged=store.mail_unchanged)
    store.save(second['rows'])
    third=scan_folder(folder([message]),'store',START,100,known_unchanged=store.mail_unchanged)
    assert third['unchanged']==1 and third['rows']==[]
    assert message.attachment.saves==2 and message.body_reads==2
    assert store.get(identity)['attachment_details'][0]==before
