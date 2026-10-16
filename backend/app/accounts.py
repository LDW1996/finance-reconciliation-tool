"""Application-owned account storage shared by Windows, macOS and Linux."""

import os
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
from uuid import uuid4

from cryptography.fernet import Fernet


def account_data_dir() -> Path:
    override = os.environ.get("FINANCE_RECONCILE_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FinanceReconciliation"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FinanceReconciliation"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "FinanceReconciliation"


class AccountStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory if directory is not None else account_data_dir()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = self.directory / "accounts.sqlite3"
        self.key_path = self.directory / "accounts.key"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL, password_cipher TEXT)")
            if not self.key_path.exists() and db.execute("SELECT COUNT(*) FROM accounts WHERE password_cipher IS NOT NULL").fetchone()[0]:
                raise RuntimeError("Account encryption key is missing")
        if not self.key_path.exists():
            try:
                descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(Fernet.generate_key())
                    handle.flush()
                    os.fsync(handle.fileno())
        self.cipher = Fernet(self.key_path.read_bytes())
        if os.name != "nt":
            self.db_path.chmod(0o600)

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _metadata(row):
        return {"id": row["id"], "name": row["name"], "username": row["username"], "hasPassword": bool(row["password_cipher"])}

    def list_accounts(self):
        with closing(self._connect()) as db:
            return [self._metadata(row) for row in db.execute("SELECT * FROM accounts ORDER BY rowid")]

    def create(self, name: str, username: str, password: str | None = None, *, account_id: str | None = None):
        account_id = account_id or str(uuid4())
        encrypted = self.cipher.encrypt(password.encode()).decode() if password else None
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?)", (account_id, name, username, encrypted))
        return {"id": account_id, "name": name, "username": username, "hasPassword": bool(encrypted)}

    def update(self, account_id: str, name: str, username: str, password: str | None = None):
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise LookupError("Account does not exist")
            encrypted = self.cipher.encrypt(password.encode()).decode() if password else row["password_cipher"]
            db.execute("UPDATE accounts SET name = ?, username = ?, password_cipher = ? WHERE id = ?", (name, username, encrypted, account_id))
        return {"id": account_id, "name": name, "username": username, "hasPassword": bool(encrypted)}

    def delete(self, account_id: str):
        with closing(self._connect()) as db, db:
            if not db.execute("DELETE FROM accounts WHERE id = ?", (account_id,)).rowcount:
                raise LookupError("Account does not exist")

    def password_for(self, account_id: str) -> str | None:
        """For backend login integration only; never expose through an API."""
        with closing(self._connect()) as db:
            row = db.execute("SELECT password_cipher FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise LookupError("Account does not exist")
        return self.cipher.decrypt(row[0].encode()).decode() if row[0] else None
