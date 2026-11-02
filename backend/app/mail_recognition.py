"""Versioned lookup of encrypted attachment results; never merges mail evidence."""
from hashlib import sha256
from threading import Lock

PREPARE_BATCH_SIZE = 100

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
        with self.store.db(write=False) as db:
            marker_row = db.execute('SELECT payload FROM mail_state WHERE key=?',
                                    ('recognition_lookup_version',)).fetchone()
        if marker_row and self.store.decode(marker_row[0]) == VERSION:
            return
        last_id = None
        while True:
            # Keyset pagination bounds memory without holding a cursor during decrypt.
            with self.store.db(write=False) as db:
                if last_id is None:
                    rows = db.execute('SELECT id,metadata FROM mail_attachment ORDER BY id LIMIT ?',
                                      (PREPARE_BATCH_SIZE,)).fetchall()
                else:
                    rows = db.execute('SELECT id,metadata FROM mail_attachment WHERE id>? ORDER BY id LIMIT ?',
                                      (last_id, PREPARE_BATCH_SIZE)).fetchall()
            if not rows:
                break
            entries = []
            for identity, payload in rows:
                asset = self.store.decode(payload)
                if (asset.get('status') == 'done' and asset.get('sha256')
                        and asset.get('recognition_version', LEGACY_VERSION) == VERSION
                        and reusable(asset.get('result'))):
                    entries.append((asset['sha256'], parser_kind(asset['name']), VERSION, identity))
            if entries:
                with self.store.db() as db:
                    db.executemany('INSERT OR IGNORE INTO mail_recognition_lookup VALUES(?,?,?,?)', entries)
            last_id = rows[-1][0]
        # A failure anywhere above leaves the marker untouched; batches are idempotent.
        marker = self.store.encode(VERSION)
        with self.store.db() as db:
            db.execute('INSERT INTO mail_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                       ('recognition_lookup_version', marker))

    def key(self, raw, name):
        return sha256(raw).hexdigest(), parser_kind(name)

    def lock(self, key):
        return self.locks[int(key[0][:8], 16) % len(self.locks)]

    def get(self, key):
        with self.store.db(write=False) as db:
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
