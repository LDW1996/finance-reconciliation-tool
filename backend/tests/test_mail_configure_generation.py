from threading import Event, Thread

from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def test_pause_invalidates_configure_start_waiting_outside_lock(tmp_path, monkeypatch):
    reached_start=Event()
    release_start=Event()
    reader_called=Event()
    errors=[]
    def reader(*args):
        reader_called.set()
        return dict(rows=[],errors=[],partial=False)
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=reader)
    original_start=manager.start
    def delayed_start(*args,**kwargs):
        reached_start.set()
        assert release_start.wait(5),'test did not release configure start'
        return original_start(*args,**kwargs)
    monkeypatch.setattr(manager,'start',delayed_start)
    def enable_old_selection():
        try: manager.configure(True,'old-store','old-folder',7,100,60)
        except BaseException as error: errors.append(error)
    caller=Thread(target=enable_old_selection)
    caller.start()
    try:
        assert reached_start.wait(5),'configure did not reach unlocked start'
        manager.configure(False,'new-store','new-folder',3,20,120)
        paused_generation=manager.generation
    finally:
        release_start.set()
        caller.join(5)
    assert not caller.is_alive()
    assert not errors
    assert not reader_called.is_set()
    assert manager.generation==paused_generation
    assert manager.selection==('new-store','new-folder',3,20)
    assert manager.state['enabled'] is False
    assert manager.state['busy'] is False
    assert manager.state['folder']=='new-folder'
    assert manager.timer is None
    saved=manager.poll_settings.load()
    assert saved['enabled'] is False
    assert (saved['store'],saved['folder'],saved['days'],saved['limit'])==('new-store','new-folder',3,20)
