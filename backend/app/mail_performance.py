"""Process-local mail work totals; no message content or disk persistence."""
from collections import deque
from contextlib import contextmanager
from threading import Lock
import time


class MailPerformance:
    """Thread-safe totals and 60 one-second buckets (up to 1s boundary error)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._started = clock()
        self._lock = Lock()
        self._counters = dict.fromkeys(('read_batches', 'read_mail', 'unchanged_mail',
            'completed_attachments', 'analysis_runs', 'cache_hits', 'analysis_failures',
            'ocr_pages', 'pdf_text_pages'), 0)
        self._seconds = dict.fromkeys(('read_seconds', 'attachment_read_seconds',
            'analysis_seconds', 'save_seconds', 'cache_wait_seconds'), 0.0)
        self._events = deque(maxlen=61)
        self._last_batch = None
        self._analysis_active = 0

    @contextmanager
    def analyzing(self):
        with self._lock:
            self._analysis_active += 1
        try:
            yield
        finally:
            with self._lock:
                self._analysis_active -= 1

    def _expire(self, now):
        while self._events and self._events[0][0] <= int(now) - 60:
            self._events.popleft()

    def record_read(self, seconds, scanned, unchanged=0, attachment_seconds=0):
        with self._lock:
            self._counters['read_batches'] += 1
            self._counters['read_mail'] += scanned
            self._counters['unchanged_mail'] += unchanged
            self._seconds['read_seconds'] += seconds
            self._seconds['attachment_read_seconds'] += attachment_seconds
            self._last_batch = dict(read_seconds=seconds, scanned=scanned,
                unchanged_mail=unchanged, attachment_read_seconds=attachment_seconds)

    def record_attachment(self, result=None, reused=False, analysis_seconds=0,
                          save_seconds=0, wait_seconds=0, failed=False):
        pages = result.get('pages', []) if isinstance(result, dict) else []
        ocr = sum(isinstance(page, dict) and page.get('method') == 'ocr' for page in pages) if not reused else 0
        pdf = sum(isinstance(page, dict) and page.get('method') == 'pdf_text' for page in pages) if not reused else 0
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._counters['completed_attachments'] += int(not failed)
            self._counters['analysis_runs'] += int(not reused)
            self._counters['cache_hits'] += int(reused)
            self._counters['analysis_failures'] += int(failed)
            self._counters['ocr_pages'] += ocr
            self._counters['pdf_text_pages'] += pdf
            self._seconds['analysis_seconds'] += analysis_seconds
            self._seconds['save_seconds'] += save_seconds
            self._seconds['cache_wait_seconds'] += wait_seconds
            bucket = int(now)
            event = (bucket, int(not failed), ocr, pdf)
            if self._events and self._events[-1][0] == bucket:
                old = self._events.pop()
                event = (bucket, old[1] + event[1], old[2] + ocr, old[3] + pdf)
            self._events.append(event)

    def add_save(self, seconds):
        with self._lock:
            self._seconds['save_seconds'] += seconds

    def snapshot(self):
        with self._lock:
            now = self._clock()
            self._expire(now)
            return dict(elapsed_seconds=max(0, now - self._started),active_analysis=self._analysis_active,
                counters=dict(self._counters), seconds=dict(self._seconds),
                recent=dict(completed_attachments=sum(e[1] for e in self._events),
                    ocr_pages=sum(e[2] for e in self._events), pdf_text_pages=sum(e[3] for e in self._events)),
                last_batch=dict(self._last_batch) if self._last_batch is not None else None)
