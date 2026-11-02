"""Per-database write gate, short explicit transactions and independent WAL reads."""
from contextlib import contextmanager, closing
from pathlib import Path
from threading import Lock, RLock
from time import perf_counter
import sqlite3
from .diagnostics import record

_registry_lock=Lock()
_writers={}


class MailDatabase:
    def __init__(self,path):
        self.path=Path(path)
        key=str(self.path.resolve())
        with _registry_lock:self.writer=_writers.setdefault(key,RLock())
        with self.writer:
            with closing(sqlite3.connect(self.path,timeout=10)) as db:
                mode=db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
                if mode.lower()!='wal':raise RuntimeError('邮件数据库无法启用 WAL，请检查数据目录。')

    @contextmanager
    def connection(self,write=True):
        started=perf_counter(); acquired=False; db=None
        try:
            if write:
                acquired=self.writer.acquire(timeout=30)
                if not acquired:raise sqlite3.OperationalError('database writer queue timed out')
                waited=perf_counter()-started
                if waited>=1:record('mail_database_writer_wait',elapsed_seconds=waited)
            db=sqlite3.connect(self.path,timeout=10)
            db.execute('PRAGMA busy_timeout=10000')
            if write:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('PRAGMA wal_autocheckpoint=256')
                db.execute('PRAGMA journal_size_limit=16777216')
                db.execute('BEGIN IMMEDIATE')
            else:db.execute('PRAGMA query_only=ON')
            yield db
            if write:db.commit()
        except Exception as error:
            if db is not None and db.in_transaction:db.rollback()
            record('mail_database_transaction_failed',error,elapsed_seconds=perf_counter()-started,write=write)
            raise
        finally:
            if db is not None:db.close()
            if acquired:self.writer.release()
            elapsed=perf_counter()-started
            if elapsed>=2:record('mail_database_transaction_slow',elapsed_seconds=elapsed,write=write)
