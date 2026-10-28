from app.mail_tasks import MailManager
from app.outlook_probe import MailStore
from test_mail_tasks import wait


class FakeTimer:
    instances = []
    def __init__(self, seconds, callback):
        self.seconds=seconds;self.callback=callback;self.cancelled=False
        self.instances.append(self)
    def start(self):pass
    def cancel(self):self.cancelled=True


def paused_manager(tmp_path):
    FakeTimer.instances=[]
    calls=[]
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),
                        reader=lambda *a:calls.append(a) or dict(rows=[],errors=[],partial=False),
                        timer_factory=FakeTimer)
    manager.storage.configure(dict(queue_limit=100))
    manager.selection=('store','folder',90,100);manager.selection_options={}
    manager.state.update(enabled=True,seconds=300)
    manager.store.set_state('sync_progress',dict(cursor={'position':17}))
    for i in range(100):manager.analysis_queue.append((str(i),False))
    manager._run('store','folder',90,100)
    return manager,calls


def drain(manager, count):
    for _ in range(count):
        identity,_=manager.analysis_queue.popleft()
        manager.analysis_queue.done(identity)


def test_low_water_schedules_quick_resume_and_retains_cursor(tmp_path):
    manager,calls=paused_manager(tmp_path)
    assert manager.status()['queue_paused']
    slow=FakeTimer.instances[-1]
    assert slow.seconds==300
    drain(manager,49)
    manager._resume_after_backpressure()
    assert len(FakeTimer.instances)==1  # still above low water
    drain(manager,1)
    manager._resume_after_backpressure()
    quick=FakeTimer.instances[-1]
    assert quick.seconds==1 and slow.cancelled
    manager._resume_after_backpressure()
    assert len(FakeTimer.instances)==2  # do not keep resetting the quick timer
    assert not calls
    assert manager.store.get_state('sync_progress')['cursor']=={'position':17}
    quick.callback();wait(manager)
    assert len(calls)==1
    assert not manager.status()['queue_paused']


def test_stop_invalidates_a_scheduled_low_water_resume(tmp_path):
    manager,calls=paused_manager(tmp_path)
    drain(manager,60)
    manager._resume_after_backpressure()
    quick=FakeTimer.instances[-1]
    manager.configure(False,'store','folder',90,100,300)
    quick.callback()
    assert quick.cancelled and not calls
    assert not manager.status()['queue_paused']
    assert manager.store.get_state('sync_progress')['cursor']=={'position':17}


def test_low_disk_prevents_fast_resume(tmp_path,monkeypatch):
    manager,calls=paused_manager(tmp_path)
    drain(manager,60)
    monkeypatch.setattr(manager.storage,'disk_free',lambda:0)
    manager._resume_after_backpressure()
    assert len(FakeTimer.instances)==1 and not calls


def test_analysis_failure_prevents_fast_resume(tmp_path):
    manager,calls=paused_manager(tmp_path)
    drain(manager,60)
    manager.state['analysis_error']='worker stopped'
    manager._resume_after_backpressure()
    assert len(FakeTimer.instances)==1 and not calls
