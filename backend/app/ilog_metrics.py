"""Compact monotonic stage totals; no credentials or page contents."""
from time import monotonic


class Timings:
    def __init__(self, result, enabled=True):
        self.result, self.enabled = result, enabled
        self.started = self.last = monotonic()
        self.current = None
        self.totals = {}

    def stage(self, stage):
        now = monotonic()
        if self.current:
            self.totals[self.current] = self.totals.get(self.current, 0) + now - self.last
        self.last = now
        self.current = ('login' if stage == 'login' else
                        'query' if stage.startswith('query') else
                        'verification' if stage.startswith('verify') else
                        'screenshot' if stage == 'screenshot' else
                        'save' if stage == 'save' else 'submission')

    def finish(self):
        now = monotonic()
        if self.current:
            self.totals[self.current] = self.totals.get(self.current, 0) + now - self.last
        if self.enabled:
            timings = [
                {'stage': stage, 'seconds': round(seconds, 6)} for stage, seconds in self.totals.items()]
            total_seconds = round(now - self.started, 6)
            # Task rows expose the result directly; retain the older nested path too.
            for record in [self.result, *self.result.get('records', [])]:
                record['timings'] = [dict(entry) for entry in timings]
                record.setdefault('detail', {})['timings'] = [dict(entry) for entry in timings]
                record['total_seconds'] = total_seconds
