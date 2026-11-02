from contextlib import contextmanager
import json
import sqlite3
import pytest
from app import mail_recognition as recognition


class Store:
    def __init__(self, path):
        self.path = path
        self.active = False
        self.modes = []
        self.fail = None
        with self.db() as db:
            db.execute('CREATE TABLE mail_attachment(id TEXT PRIMARY KEY, metadata TEXT)')
            db.execute('CREATE TABLE mail_state(key TEXT PRIMARY KEY, payload TEXT)')

    @contextmanager
    def db(self, write=True):
        assert not self.active
        self.active = True
        self.modes.append(write)
        try:
            with sqlite3.connect(self.path) as db:
                yield db
        finally:
            db.close()
            self.active = False

    def decode(self, payload):
        assert not self.active, 'decryption held database connection'
        asset = json.loads(payload)
        if isinstance(asset, dict) and asset.get('id') == self.fail:
            raise ValueError('injected decode failure')
        return asset

    def encode(self, value):
        assert not self.active, 'encryption held database connection'
        return json.dumps(value)

    def get_state(self, key):
        with self.db(write=False) as db:
            row = db.execute('SELECT payload FROM mail_state WHERE key=?', (key,)).fetchone()
        return self.decode(row[0]) if row else None


def seed(store, count):
    with store.db() as db:
        for i in range(count):
            asset = dict(id=str(i), name='receipt.png', status='done', sha256=str(i),
                         result={'pages': [{}], 'fields': {}, 'warnings': []})
            db.execute('INSERT INTO mail_attachment VALUES(?,?)', (str(i), json.dumps(asset)))


def test_prepare_paged_and_get_decrypt_outside_read_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(recognition, 'PREPARE_BATCH_SIZE', 2, raising=False)
    store = Store(tmp_path/'cache.db')
    seed(store, 5)
    cache = recognition.RecognitionCache(store)
    store.modes.clear()
    cache.prepare()
    assert store.get_state('recognition_lookup_version') == recognition.VERSION
    assert store.modes.count(False) >= 4
    store.modes.clear()
    assert cache.get(('3', 'image')) == {'pages': [{}], 'fields': {}, 'warnings': []}
    assert store.modes == [False]


def test_failed_prepare_has_no_marker_and_reruns_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(recognition, 'PREPARE_BATCH_SIZE', 2, raising=False)
    store = Store(tmp_path/'cache.db')
    seed(store, 5)
    cache = recognition.RecognitionCache(store)
    store.fail = '3'
    with pytest.raises(ValueError, match='injected'):
        cache.prepare()
    assert store.get_state('recognition_lookup_version') is None
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0] == 2
    store.fail = None
    cache.prepare()
    cache.prepare()
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0] == 5


def test_write_failure_does_not_mark_complete_and_can_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(recognition, 'PREPARE_BATCH_SIZE', 2)
    store = Store(tmp_path/'cache.db')
    seed(store, 5)
    cache = recognition.RecognitionCache(store)
    original = store.db
    writes = 0

    @contextmanager
    def fail_second_write(write=True):
        nonlocal writes
        with original(write=write) as db:
            yield db
            if write:
                writes += 1
                if writes == 2:
                    raise sqlite3.OperationalError('injected write failure')

    monkeypatch.setattr(store, 'db', fail_second_write)
    with pytest.raises(sqlite3.OperationalError, match='injected'):
        cache.prepare()
    assert store.get_state('recognition_lookup_version') is None
    monkeypatch.setattr(store, 'db', original)
    cache.prepare()
    assert store.get_state('recognition_lookup_version') == recognition.VERSION
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0] == 5
