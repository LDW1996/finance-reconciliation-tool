"""Small encrypted polling settings, independent of the mail database writer."""
from contextlib import suppress
import os
from pathlib import Path
import tempfile
from threading import RLock

_registry_lock=RLock()
_locks={}


class PollSettings:
    def __init__(self,store):
        self.store=store
        self.path=store.path.with_suffix(store.path.suffix+'.poll')
        with _registry_lock:self.lock=_locks.setdefault(str(self.path.resolve()),RLock())
        with self.lock:
            if not self.path.exists():self.save(store.get_state('poll_settings',{}))

    def load(self):
        with self.lock:
            return self.store.decode(self.path.read_text(encoding='utf-8'))

    def save(self,value):
        payload=self.store.encode(value).encode('utf-8')
        with self.lock:
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix=self.path.name+'.',delete=False) as output:
                    temporary=Path(output.name)
                    os.chmod(temporary,0o600)
                    output.write(payload);output.flush();os.fsync(output.fileno())
                os.replace(temporary,self.path)
                if os.name=='posix':
                    descriptor=os.open(self.path.parent,os.O_RDONLY)
                    try:os.fsync(descriptor)
                    finally:os.close(descriptor)
            finally:
                if temporary is not None:
                    with suppress(FileNotFoundError):temporary.unlink()
