from threading import Event, Thread
import pytest
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def test_configuration_save_does_not_wait_for_mail_writer(tmp_path):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    held=Event();release=Event();done=Event();errors=[]
    def holder():
        with m.store.database.writer:
            held.set();assert release.wait(5)
    def configure():
        try:m.configure(False,'s','new',7,10,60)
        except Exception as e:errors.append(e)
        finally:done.set()
    owner=Thread(target=holder);owner.start();assert held.wait(5)
    caller=Thread(target=configure);caller.start()
    try:assert done.wait(1),'configuration waited for mail writer'
    finally:release.set();owner.join(5);caller.join(5)
    assert not errors


def test_settings_io_does_not_hold_manager_lock(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    entered=Event();release=Event();acquired=Event();errors=[]
    original=m._save_poll_settings
    def delayed(*a,**kw):
        entered.set();assert release.wait(5);return original(*a,**kw)
    monkeypatch.setattr(m,'_save_poll_settings',delayed)
    def configure():
        try:m.configure(False,'s','f',7,10,60)
        except Exception as e:errors.append(e)
    caller=Thread(target=configure);caller.start();assert entered.wait(5)
    def lock_probe():
        with m.lock:acquired.set()
    probe=Thread(target=lock_probe);probe.start()
    try:assert acquired.wait(1),'settings IO held manager lock'
    finally:release.set();caller.join(5);probe.join(5)
    assert not errors


def test_legacy_settings_migrate_once_and_new_file_is_encrypted(tmp_path):
    from app.mail_poll_settings import PollSettings
    store=MailStore(tmp_path/'mail.db')
    legacy=dict(enabled=False,store='legacy-store',folder='legacy-folder',seconds=60)
    store.set_state('poll_settings',legacy)
    settings=PollSettings(store)
    assert settings.load()==legacy
    settings.save(dict(legacy,folder='new-folder'))
    assert 'new-folder' not in settings.path.read_text()
    assert PollSettings(MailStore(store.path)).load()['folder']=='new-folder'
    assert store.get_state('poll_settings')==legacy


def test_continuous_saves_finish_with_latest_pause(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    entered=Event();release=Event();pause_requested=Event();done=Event();errors=[]
    original=m._save_poll_settings
    def delayed(value,**kw):
        if value.get('folder')=='first':
            entered.set();assert release.wait(5)
        return original(value,**kw)
    monkeypatch.setattr(m,'_save_poll_settings',delayed)
    def first():
        try:m.configure(False,'s','first',7,10,60)
        except Exception as e:errors.append(e)
    def pause():
        pause_requested.set()
        try:m.configure(False,'s','latest',3,20,120)
        except Exception as e:errors.append(e)
        finally:done.set()
    a=Thread(target=first);a.start();assert entered.wait(5)
    b=Thread(target=pause);b.start();assert pause_requested.wait(5)
    release.set();a.join(5);b.join(5)
    assert done.is_set() and not errors
    assert m.state['folder']=='latest' and not m.state['enabled']
    assert m.poll_settings.load()['folder']=='latest'


def test_batch_save_does_not_hold_manager_lock(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    saving=Event();release=Event();acquired=Event();finished=Event()
    def save(rows):
        saving.set();assert release.wait(5);return 0
    monkeypatch.setattr(m.store,'save',save)
    def run():
        try:m._run('s','f',7,10)
        finally:finished.set()
    worker=Thread(target=run);worker.start();assert saving.wait(5)
    def probe():
        with m.lock:acquired.set()
    caller=Thread(target=probe);caller.start()
    try:assert acquired.wait(1),'mail batch save held manager lock'
    finally:release.set();worker.join(5);caller.join(5)
    assert finished.is_set()


@pytest.mark.parametrize("operation",["popleft","done","release"])
def test_pause_and_status_respond_while_controller_waits_for_writer(tmp_path,monkeypatch,operation):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    held=Event();release=Event();claiming=Event();configured=Event();status_done=Event();errors=[]
    admission_done=Event();reader_called=Event()
    m.reader=lambda *args:reader_called.set() or dict(rows=[],errors=[],partial=False)
    original_start=m.start
    def start(*args,**kwargs):
        try:return original_start(*args,**kwargs)
        finally:admission_done.set()
    monkeypatch.setattr(m,'start',start)
    m.analysis_queue.append(('mail',False));m.analysis_busy=True
    def holder():
        with m.store.database.writer:
            held.set();assert release.wait(5)
    def controller():
        with m.lock:
            claiming.set()
            if operation=='popleft':m.analysis_queue.popleft()
            else:getattr(m.analysis_queue,operation)('mail')
    def pause():
        try:
            m.configure(True,'s','enabled',3,20,60)
            m.configure(False,'s','paused',3,20,60)
        except Exception as e:errors.append(e)
        finally:configured.set()
    owner=Thread(target=holder);owner.start();assert held.wait(5)
    active=Thread(target=controller);active.start();assert claiming.wait(5)
    caller=Thread(target=pause);caller.start()
    probe=Thread(target=lambda:(m.status(),status_done.set()));probe.start()
    try:
        assert configured.wait(1),'pause waited for controller manager lock'
        assert status_done.wait(1),'status waited for controller manager lock'
    finally:
        release.set()
        for thread in (owner,active,caller,probe):thread.join(5)
    assert admission_done.wait(5)
    assert not reader_called.is_set()
    assert not errors and m.poll_settings.load()['folder']=='paused'


def test_ack_rechecks_pending_attachment_atomically(tmp_path):
    import pytest
    from app.outlook_probe import StaleAttachmentError
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False),permission_check=lambda:False)
    m.store.save([dict(id='mail',_attachments=[dict(name='a',raw=b'new',status='pending')])])
    m.analysis_queue.append(('mail',False));m.analysis_queue.popleft()
    with pytest.raises(StaleAttachmentError):m.analysis_queue.done('mail')
    with m.store.db(write=False) as db:
        assert db.execute('SELECT claimed FROM mail_analysis_queue WHERE mail_id=?',('mail',)).fetchone()==(1,)


def test_backpressure_and_pause_share_command_generation(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    checking=Event();release=Event();paused=Event();timers=[];errors=[]
    class Timer:
        def __init__(self,seconds,callback):self.callback=callback;self.cancelled=False;timers.append(self)
        def start(self):pass
        def cancel(self):self.cancelled=True
    m.timer_factory=Timer
    with m.poll_lock:
        m.selection=('s','old',7,10);m.selection_options={}
        m.state.update(enabled=True,queue_paused=True)
    def check(**kwargs):
        checking.set();assert release.wait(5);return ''
    monkeypatch.setattr(m.storage,'block_reason',check)
    def resume():
        try:m._resume_after_backpressure()
        except Exception as e:errors.append(e)
    def pause():
        try:m.configure(False,'s','paused',3,20,60)
        except Exception as e:errors.append(e)
        finally:paused.set()
    controller=Thread(target=resume);controller.start();assert checking.wait(5)
    command=Thread(target=pause);command.start()
    # Old code allows pause to finish, then publishes a quick-resume timer based
    # on its earlier enabled check. Correct code serializes both transitions.
    paused.wait(.1)
    release.set();controller.join(5);command.join(5)
    assert paused.is_set() and not errors
    assert not m.state['enabled'] and m.timer is None
    assert not m.queue_resume_scheduled
    assert all(timer.cancelled for timer in timers)
    generation=m.generation
    for timer in timers:timer.callback()
    assert m.generation==generation and not m.state['busy']
    assert m.poll_settings.load()['folder']=='paused'


def test_pause_between_start_admission_and_persist_preserves_new_busy(tmp_path,monkeypatch):
    from threading import current_thread
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[],errors=[],partial=False))
    gap=Event();release_old=Event();reader_entered=Event();release_reader=Event();finished=Event();errors=[]
    real_lock=m.poll_lock
    class AdmissionGap:
        count=0
        def __enter__(self):
            if current_thread().name=='old-start':
                self.count+=1
                if self.count==2:
                    gap.set();assert release_old.wait(5)
            real_lock.acquire();return self
        def __exit__(self,*args):real_lock.release()
    m.poll_lock=AdmissionGap()
    def reader(*args):
        reader_entered.set();assert release_reader.wait(5)
        return dict(rows=[],errors=[],partial=False)
    m.reader=reader
    original_run=m._run
    def run(*a,**kw):
        try:return original_run(*a,**kw)
        finally:finished.set()
    monkeypatch.setattr(m,'_run',run)
    def old_start():
        try:m.start('s','old',7,10)
        except Exception as e:errors.append(e)
    old=Thread(target=old_start,name='old-start');old.start();assert gap.wait(5)
    try:
        m.configure(False,'s','paused',3,20,60)
        m.configure(True,'s','new',3,20,60)
        assert reader_entered.wait(5)
        new_generation=m.generation
        release_old.set();old.join(5)
        assert not errors and not old.is_alive()
        assert m.generation==new_generation and m.state['busy']
        assert m.state['folder']=='new' and m.poll_settings.load()['folder']=='new'
        m.configure(False,'s','new',3,20,60)
    finally:
        release_old.set();release_reader.set();old.join(5)
    assert finished.wait(5)


def test_pause_returns_during_actual_mail_save_writer_wait(tmp_path,monkeypatch):
    held=Event();release=Event();saving=Event();finished=Event();paused=Event();errors=[]
    row=dict(id='mail',body='new mail')
    m=MailManager(MailStore(tmp_path/'mail.db'),reader=lambda *a:dict(rows=[row],errors=[],partial=False))
    original_save=m.store.save;original_run=m._run
    def save(rows):saving.set();return original_save(rows)
    def run(*a,**kw):
        try:return original_run(*a,**kw)
        finally:finished.set()
    monkeypatch.setattr(m.store,'save',save);monkeypatch.setattr(m,'_run',run)
    def holder():
        with m.store.database.writer:held.set();assert release.wait(5)
    owner=Thread(target=holder);owner.start();assert held.wait(5)
    m.start('s','old',7,10);assert saving.wait(5)
    def pause():
        try:m.configure(False,'s','paused',3,20,60)
        except Exception as e:errors.append(e)
        finally:paused.set()
    command=Thread(target=pause);command.start()
    try:assert paused.wait(1),'pause waited for whole-batch save DB admission'
    finally:release.set();owner.join(5);command.join(5)
    assert finished.wait(5) and not errors
    assert not m.state['enabled'] and m.poll_settings.load()['folder']=='paused'
