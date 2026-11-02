"""Per-read transient progress; never changes the durable scan cursor."""
from contextvars import ContextVar
sink = ContextVar('mail_read_progress', default=None)


def report(phase, **fields):
    callback = sink.get()
    if callback is not None:
        callback(phase, fields)
