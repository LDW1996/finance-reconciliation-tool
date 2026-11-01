"""Local, process-scoped authentication; account hashes are supplied at build time."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import time
from .platform_roles import ROLE_CAPABILITIES

COOKIE_NAME = "finance_platform_session"

def hash_password(password, *, iterations=600_000):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"

def verify_password(password, encoded):
    try:
        scheme, rounds, salt, expected = encoded.split("$")
        if scheme != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False

class PlatformAuth:
    def __init__(self, credentials_path=None, transition_guard=None):
        self.credentials_path = credentials_path
        self.transition_guard = transition_guard
        self.sessions = {}
        self.attempts = {}

    def accounts(self):
        frozen = getattr(sys, "frozen", False)
        if frozen:
            path = Path(sys._MEIPASS) / "platform_accounts.json"
        else:
            path = Path(self.credentials_path or os.environ.get("FINANCE_PLATFORM_ACCOUNTS", Path(__file__).with_name("platform_accounts.json")))
        try:
            accounts = json.loads(path.read_text(encoding="utf-8"))
            if set(accounts) != set(ROLE_CAPABILITIES) or not all(isinstance(v, str) and v.startswith("pbkdf2_sha256$") for v in accounts.values()):
                raise ValueError("Invalid fixed account configuration")
            return accounts
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Platform account configuration missing or invalid") from exc

    def identity(self, token):
        # Explicit revocation or process restart only: unattended mail reads must not expire.
        item = self.sessions.get(token)
        if item:
            name = item
            return {"authenticated": True, "username": name, "role": name, "capabilities": sorted(ROLE_CAPABILITIES[name])}
        self.sessions.pop(token, None)
        return None

    def allow_attempt(self, key):
        now = time.monotonic()
        entries = [t for t in self.attempts.get(key, []) if now-t < 60]
        self.attempts[key] = entries
        if len(entries) >= 10:
            return False
        entries.append(now)
        return True

    def allowed(self, capability):
        return any(identity and capability in identity["capabilities"] for identity in [self.identity(token) for token in list(self.sessions)])
