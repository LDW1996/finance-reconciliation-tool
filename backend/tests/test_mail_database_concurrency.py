from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sqlite3
import pytest
from app.outlook_probe import MailStore


def test_wal_and_independent_read_during_write(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');store.set_state('old',1)
    entered=Event();release=Event()
    def write():
        with store.db() as db:
            db.execute("INSERT INTO mail_state VALUES('new','value')")
            entered.set();assert release.wait(5)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(write);assert entered.wait(3)
        try:
            with store.db(write=False) as db:
                assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
                assert db.execute("SELECT COUNT(*) FROM mail_state WHERE key='new'").fetchone()[0]==0
        finally:release.set()
        future.result()


def test_two_store_instances_serialize_read_modify_write(tmp_path):
    first=MailStore(tmp_path/'mail.sqlite3');second=MailStore(tmp_path/'mail.sqlite3')
    first.set_state('counter',0)
    def increment(i):
        store=first if i%2 else second
        with store.db() as db:
            row=db.execute("SELECT payload FROM mail_state WHERE key='counter'").fetchone()
            value=store.decode(row[0])+1
            db.execute("UPDATE mail_state SET payload=? WHERE key='counter'",(store.encode(value),))
    with ThreadPoolExecutor(8) as pool:list(pool.map(increment,range(64)))
    assert first.get_state('counter')==64


def test_readonly_connection_cannot_write(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    with pytest.raises(sqlite3.OperationalError):
        with store.db(write=False) as db:db.execute('DELETE FROM mail')


def test_failed_write_rolls_back_and_releases_gate(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    with pytest.raises(RuntimeError):
        with store.db() as db:
            db.execute("INSERT INTO mail_state VALUES('rollback','payload')")
            raise RuntimeError('failure')
    assert store.get_state('rollback') is None
    store.set_state('after',1)
    assert store.get_state('after')==1
