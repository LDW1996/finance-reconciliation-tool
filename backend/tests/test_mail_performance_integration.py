import time
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore
from test_mail_tasks import wait


def test_fresh_and_reused_attachments_have_distinct_metrics(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    for i in range(3):
        store.save([dict(id=str(i),body='',_attachments=[dict(name=f'{i}.png',raw=b'same',status='pending')])])
    def analyze(*args):
        time.sleep(.01)
        return dict(pages=[dict(number=1,method='ocr',fields={})],fields={},warnings=[])
    manager=MailManager(store,analyzer=analyze)
    deadline=time.monotonic()+5
    while manager.status()['analysis_busy'] and time.monotonic()<deadline:time.sleep(.02)
    status=manager.status()
    assert not status['analysis_busy']
    stats=status['performance']
    assert stats['counters']['completed_attachments']==3
    assert stats['counters']['analysis_runs']==1
    assert stats['counters']['cache_hits']==2
    assert stats['counters']['ocr_pages']==1
    assert stats['seconds']['analysis_seconds']>=.01
    assert stats['seconds']['save_seconds']>0


def test_all_skipped_read_batch_retains_scan_counts_and_checkpoint(tmp_path):
    calls=[]
    def read(*args):
        calls.append(args)
        return dict(rows=[],errors=[],partial=True,cursor={'position':100},read=100,unchanged=100,attachment_seconds=0)
    store=MailStore(tmp_path/'mail.sqlite3')
    manager=MailManager(store,paged_reader=read)
    manager.start('store','folder',90,100);wait(manager)
    stats=manager.status()['performance']
    assert manager.status()['read']==100 and manager.status()['new']==0
    assert stats['counters']['read_mail']==stats['counters']['unchanged_mail']==100
    assert store.get_state('sync_progress')['cursor']=={'position':100}
    assert stats['last_batch']['scanned']==100
