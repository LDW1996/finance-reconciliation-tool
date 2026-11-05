"""Generate a private build input from the existing GitHub account secret."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.platform_auth import hash_password
from app.platform_roles import ROLE_CAPABILITIES


def prepare(passwords, destination):
    if not isinstance(passwords, dict) or set(passwords) != set(ROLE_CAPABILITIES):
        raise ValueError('Provide exactly the four platform roles')
    if not all(isinstance(value, str) and value and len(value) <= 1024 for value in passwords.values()):
        raise ValueError('Platform role passwords must be nonempty strings of at most 1024 characters')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never print passwords or the generated hashes, including validation failures.
    data = {role: hash_password(passwords[role]) for role in ROLE_CAPABILITIES}
    with open(destination, 'w', encoding='utf-8', opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
        json.dump(data, output)
    return destination


if __name__ == '__main__':
    try:
        values = json.loads(os.environ.get('PLATFORM_SMOKE_ACCOUNTS', ''))
        prepare(values, ROOT / 'build' / 'platform_accounts.json')
    except (ValueError, TypeError, OSError):
        raise SystemExit('Could not prepare platform accounts. Check PLATFORM_SMOKE_ACCOUNTS; no default accounts are bundled.') from None
    print('Private platform account build input prepared.')
