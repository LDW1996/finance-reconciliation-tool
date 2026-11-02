from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def test_stalled_read_is_not_completion_and_preserves_cursor(tmp_path,monkeypatch):
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=lambda *args:None)
    checkpoint={'complete':False,'cursor':{'position':0,'page':{'before':'2026-10-01'}},'folders_done':0,'folders_total':13}
    manager.store.set_state('sync_progress',checkpoint)
    monkeypatch.setattr('app.mail_tasks.perf_counter',lambda:1000)
    manager.state['busy']=True
    manager._read_progress('sorting',{'folder_number':1,'folders_total':13,'current_folder':'Inbox'})
    monkeypatch.setattr('app.mail_tasks.perf_counter',lambda:1121)
    status=manager.status()
    assert status['read_progress']['stalled'] and status['read_progress']['stage_seconds']==121
    assert not status['sync_complete'] and status['folders_done']==0
    assert manager.store.get_state('sync_progress')==checkpoint
    manager._read_progress('metadata')
    assert not manager.status()['read_progress']['stalled']
    assert manager.status()['read_progress']['folder_number']==1


def test_progress_context_does_not_leak_between_reads():
    from app.mail_read_progress import report,sink
    events=[]
    token=sink.set(lambda phase,fields:events.append((phase,fields)))
    try:report('sorting',folder_number=1)
    finally:sink.reset(token)
    report('saving')
    assert events==[('sorting',{'folder_number':1})]
