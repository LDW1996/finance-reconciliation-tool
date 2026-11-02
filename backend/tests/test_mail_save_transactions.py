from contextlib import contextmanager
import sqlite3
import pytest
from app.outlook_probe import MailStore


def test_first_mail_committed_before_second_preparation(tmp_path):
    store=MailStore(tmp_path/'mail.db')
    def rows():
        yield dict(id='first',body='one')
        with sqlite3.connect(store.path) as db:
            assert db.execute('SELECT id FROM mail').fetchall()==[('first',)]
        yield dict(id='second',body='two')
    assert store.save(rows())==2


def test_all_encryption_outside_write_context(tmp_path,monkeypatch):
    store=MailStore(tmp_path/'mail.db');original_db=store.db;encrypt=store.cipher.encrypt
    writing=False
    @contextmanager
    def tracked_db(*args,**kwargs):
        nonlocal writing
        with original_db(*args,**kwargs) as db:
            writing=kwargs.get('write',True)
            try: yield db
            finally: writing=False
    def checked_encrypt(raw):
        assert not writing,'encryption held write gate'
        return encrypt(raw)
    monkeypatch.setattr(store,'db',tracked_db)
    monkeypatch.setattr(store.cipher,'encrypt',checked_encrypt)
    row=dict(id='a',source='outlook',internet_message_id='msg',attachments=['a.pdf'],_attachments=[dict(name='a.pdf',raw=b'original')])
    store.save([row])
    store.save([dict(row,id='b',folder='other')])
    store.save([dict(row,_attachments=[dict(name='a.pdf',raw=b'changed')])])


def test_second_mail_failure_rolls_back_only_second(tmp_path,monkeypatch):
    store=MailStore(tmp_path/'mail.db')
    from app import mail_assets
    original=mail_assets.save_blob
    def fail(store,db,identity,raw,**kwargs):
        original(store,db,identity,raw,**kwargs)
        raise RuntimeError('after blob insertion')
    monkeypatch.setattr(mail_assets,'save_blob',fail)
    with pytest.raises(RuntimeError):
        store.save([dict(id='first'),dict(id='second',_attachments=[dict(name='a',raw=b'raw')])])
    with store.db() as db:
        assert db.execute('SELECT id FROM mail').fetchall()==[('first',)]
        assert db.execute('SELECT COUNT(*) FROM mail_blob').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM mail_attachment').fetchone()[0]==0


def test_corrupt_original_rolls_back_mail_and_metadata(tmp_path):
    import hashlib
    store=MailStore(tmp_path/'mail.db');raw=b'evidence'
    with store.db() as db:
        db.execute('INSERT INTO mail_blob VALUES(?,?)',(hashlib.sha256(raw).hexdigest(),store.cipher.encrypt(b'corrupt')))
    with pytest.raises(ValueError,match='校验失败'):
        store.save([dict(id='a',_attachments=[dict(name='a',raw=raw)])])
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM mail_attachment_blob_ref').fetchone()[0]==0


def test_same_mail_duplicate_originals_share_blob(tmp_path):
    store=MailStore(tmp_path/'mail.db')
    store.save([dict(id='a',_attachments=[dict(name=name,raw=b'evidence') for name in ('a','b')])])
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_blob').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM mail_attachment_blob_ref').fetchone()[0]==2


def test_concurrent_metadata_change_reprepares_instead_of_overwriting(tmp_path,monkeypatch):
    store=MailStore(tmp_path/'mail.db')
    row=dict(id='a',source='outlook',internet_message_id='msg',attachments=['a'],_attachments=[dict(name='a',raw=b'raw')])
    store.save([row]);original_encode=store.encode;changed=False
    def encode(value):
        nonlocal changed
        if value.get('id')=='a' and len(value.get('sources',[]))==2 and not changed:
            changed=True
            with store.db() as db:
                saved=store.decode(db.execute('SELECT payload FROM mail WHERE id=?',('a',)).fetchone()[0])
                saved['outlook_read_version']='new-version'
                db.execute('UPDATE mail SET payload=? WHERE id=?',(original_encode(saved),'a'))
        return original_encode(value)
    monkeypatch.setattr(store,'encode',encode)
    assert store.save([dict(row,id='b',folder='second')])==0
    saved=store.get('a')
    assert saved['outlook_read_version']=='new-version'
    assert len(saved['sources'])==2
