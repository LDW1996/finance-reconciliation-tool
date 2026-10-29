"""Snapshot optional files that may disappear during normal database/cache work."""
import stat


def optional_file_stats(paths):
    for path in paths:
        try:
            info=path.stat()
        except FileNotFoundError:
            # SQLite removes sidecars on commit; preview eviction removes caches.
            # Other errors, particularly permission and I/O failures, must surface.
            continue
        if stat.S_ISREG(info.st_mode):
            yield path,info
