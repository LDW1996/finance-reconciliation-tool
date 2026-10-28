from concurrent.futures import ThreadPoolExecutor

from app.mail_performance import MailPerformance


def test_totals_pages_reuse_and_restart():
    now = [10.0]
    metrics = MailPerformance(clock=lambda: now[0])
    metrics.record_read(3, 12, unchanged=8, attachment_seconds=1)
    result = {'pages': [{'method': 'ocr'}, {'method': 'pdf_text'}, {'method': 'ocr'}]}
    metrics.record_attachment(result, analysis_seconds=2, save_seconds=.5, wait_seconds=.1)
    metrics.record_attachment(result, reused=True, wait_seconds=.2)
    metrics.record_attachment(failed=True, analysis_seconds=1)
    metrics.add_save(.25)
    now[0] = 15
    snapshot = metrics.snapshot()
    assert snapshot['elapsed_seconds'] == 5
    assert snapshot['counters'] == dict(read_batches=1, read_mail=12, unchanged_mail=8,
        completed_attachments=2, analysis_runs=2, cache_hits=1, analysis_failures=1,
        ocr_pages=2, pdf_text_pages=1)
    assert snapshot['seconds'] == dict(read_seconds=3, attachment_read_seconds=1,
        analysis_seconds=3, save_seconds=.75, cache_wait_seconds=.1+.2)
    assert snapshot['last_batch'] == dict(read_seconds=3, scanned=12, unchanged_mail=8, attachment_read_seconds=1)
    assert snapshot['recent'] == dict(completed_attachments=2, ocr_pages=2, pdf_text_pages=1)
    assert MailPerformance().snapshot()['counters']['read_mail'] == 0
    snapshot['counters']['read_mail'] = 999
    assert metrics.snapshot()['counters']['read_mail'] == 12


def test_recent_is_actual_window_and_idle_expiry():
    now = [0.0]
    metrics = MailPerformance(clock=lambda: now[0])
    metrics.record_attachment({'pages': [{'method': 'ocr'}]})
    now[0] = 59.9
    metrics.record_attachment({'pages': [{'method': 'pdf_text'}]})
    assert metrics.snapshot()['recent']['completed_attachments'] == 2
    now[0] = 60.01
    assert metrics.snapshot()['recent'] == dict(completed_attachments=1, ocr_pages=0, pdf_text_pages=1)
    now[0] = 120
    assert metrics.snapshot()['recent'] == dict(completed_attachments=0, ocr_pages=0, pdf_text_pages=0)


def test_concurrent_updates_and_bounded_events():
    metrics = MailPerformance(clock=lambda: 0)
    def record(_):
        metrics.record_read(.1, 1)
        metrics.record_attachment({'pages': [{'method': 'ocr'}]}, analysis_seconds=.2)
        metrics.add_save(.1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(1000)))
    snapshot = metrics.snapshot()
    assert snapshot['counters']['read_mail'] == 1000
    assert snapshot['counters']['ocr_pages'] == 1000
    assert snapshot['recent']['completed_attachments'] == 1000
    assert metrics._events.maxlen == 61
    assert len(metrics._events) == 1


def test_second_buckets_bound_memory_without_losing_same_second_counts():
    now = [0.0]
    metrics = MailPerformance(clock=lambda: now[0])
    for second in range(180):
        now[0] = second + .5
        for _ in range(3):
            metrics.record_attachment()
    assert len(metrics._events) == 60
    assert metrics.snapshot()['recent']['completed_attachments'] == 180
    assert metrics.snapshot()['counters']['completed_attachments'] == 540
    now[0] = 239.5
    assert metrics.snapshot()['recent']['completed_attachments'] == 0


def test_active_analysis_gauge_excludes_cache_wait_and_clears_on_error():
    import pytest
    recorder=MailPerformance()
    assert recorder.snapshot()['active_analysis']==0
    with pytest.raises(RuntimeError):
        with recorder.analyzing():
            assert recorder.snapshot()['active_analysis']==1
            raise RuntimeError('analysis interrupted')
    assert recorder.snapshot()['active_analysis']==0
