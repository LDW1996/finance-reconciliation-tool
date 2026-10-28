"""Versioned lookup of encrypted attachment results; never merges mail evidence."""
from hashlib import sha256
from threading import Lock

VERSION = 'rapidocr-1.4.4-fields-v1-pdf-v2'
# Only this version accepts compatible unversioned historical results.
LEGACY_VERSION = 'rapidocr-1.4.4-fields-v1-pdf-v2'


def parser_kind(name):
    return 'pdf' if name.lower().endswith('.pdf') else 'image'


def reusable(result):
    if not isinstance(result, dict) or not result.get('pages') or not isinstance(result.get('fields'), dict):
        return False
    # A partially failed PDF must not prevent another copy from being retried.
    return not any('页识别失败' in str(warning) for warning in result.get('warnings', []))


class RecognitionCache:
    def __init__(self, store):
        self.store = store
        self.locks = [Lock() for _ in range(64)]
        with store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS mail_recognition_lookup (
                digest TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
                attachment_id TEXT NOT NULL, PRIMARY KEY(digest,kind,version))''')

    def prepare(self):
        # One derived index build per version; only decrypt existing metadata.
        # No historical attachment, payment or review is rewritten.
        if self.store.get_state('recognition_lookup_version') == VERSION:
            return
        with self.store.db() as db:
            rows = db.execute('SELECT id,metadata FROM mail_attachment')
            for identity, payload in rows:
                asset = self.store.decode(payload)
                if (asset.get('status') == 'done' and asset.get('sha256')
                        and asset.get('recognition_version', LEGACY_VERSION) == VERSION
                        and reusable(asset.get('result'))):
                    db.execute('INSERT OR IGNORE INTO mail_recognition_lookup VALUES(?,?,?,?)',
                               (asset['sha256'], parser_kind(asset['name']), VERSION, identity))
            db.execute('INSERT INTO mail_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                       ('recognition_lookup_version', self.store.encode(VERSION)))

    def key(self, raw, name):
        return sha256(raw).hexdigest(), parser_kind(name)

    def lock(self, key):
        return self.locks[int(key[0][:8], 16) % len(self.locks)]

    def get(self, key):
        with self.store.db() as db:
            row = db.execute('''SELECT a.metadata FROM mail_recognition_lookup c
                JOIN mail_attachment a ON a.id=c.attachment_id
                WHERE c.digest=? AND c.kind=? AND c.version=?''', (*key, VERSION)).fetchone()
            if row:
                asset = self.store.decode(row[0])
                if (asset.get('status') == 'done' and asset.get('sha256') == key[0]
                        and parser_kind(asset['name']) == key[1]
                        and asset.get('recognition_version', LEGACY_VERSION) == VERSION
                        and reusable(asset.get('result'))):
                    return asset['result']
        return None

    def remember(self, key, identity, result):
        if reusable(result):
            with self.store.db() as db:
                db.execute('''INSERT INTO mail_recognition_lookup VALUES(?,?,?,?)
                    ON CONFLICT(digest,kind,version) DO UPDATE SET attachment_id=excluded.attachment_id''',
                           (*key, VERSION, identity))
