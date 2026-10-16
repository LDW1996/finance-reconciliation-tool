from datetime import datetime, timezone, timedelta
import json
import os

import pytest

from app.ilog_tasks import TaskManager
from app.ilog_retention import Retention


def setup_store(tmp_path):
    return TaskManager(tmp_path / 'ilog-tasks', auto_start=False)


def test_defaults_and_settings_validation(tmp_path):
    retention=Retention(setup_store(tmp_path))
    assert retention.settings()['screenshot_days']==90
    assert retention.settings()['record_days']==365
    with pytest.raises(ValueError): retention.configure(0,365,True)
    retention.configure(30,180,False)
    assert Retention(setup_store(tmp_path)).settings()['record_days']==180


def test_cleanup_protects_uncertain_and_submission_guards(tmp_path):
    manager=setup_store(tmp_path)
    retention=Retention(manager)
    old=(datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
    job=manager.create('west','West',['TRBTSTZH2606S'],[],'request-one',True)
    job.update(state='completed',completedAt=old)
    job['rows'][0].update(status='success',time=old)
    root=tmp_path/'unlock-results';root.mkdir()
    image=root/'old.png';image.write_bytes(b'test')
    os.utime(image,(1,1))
    job['rows'][0]['detail']={'screenshot':str(image)}
    with manager.db() as db: manager.save(db,job)
    uncertain=manager.create('west','West',['TRBTSTHP2607S'],[],'request-two',True)
    uncertain.update(state='completed',completedAt=old)
    protected=root/'protected.png';protected.write_bytes(b'test');os.utime(protected,(1,1))
    uncertain['rows'][0].update(status='attention',time=old,detail={'screenshot':str(protected)})
    with manager.db() as db: manager.save(db,uncertain)
    guard=root/'submitted';guard.mkdir();(guard/'do-not-delete.json').write_text('{}')
    preview=retention.cleanup(dry_run=True)
    assert preview['tasks']==1 and preview['screenshots']==1
    assert image.exists()
    result=retention.cleanup()
    assert result['tasks']==1 and not image.exists() and protected.exists()
    assert (guard/'do-not-delete.json').exists()
    assert manager.get(uncertain['id'])['rows'][0]['status']=='attention'
    with pytest.raises(RuntimeError):
        manager.create('west','West',['TRBTSTZH2606S'],[],'request-one',True)


def test_standalone_uncertain_report_keeps_evidence(tmp_path):
    retention=Retention(setup_store(tmp_path))
    root=tmp_path/'unlock-results';root.mkdir()
    image=root/'pending.png';image.write_bytes(b'test');os.utime(image,(1,1))
    report=root/'pending.json';report.write_text(json.dumps({'status':'needs_verification','screenshot':str(image)}));os.utime(report,(1,1))
    assert retention.cleanup()['screenshots']==0
    assert image.exists() and report.exists()


def test_expired_screenshot_is_labeled_and_history_is_paginated(tmp_path):
    manager=setup_store(tmp_path)
    job=manager.create('west','West',['TRBTSTZH2606S'],[],'key',True)
    job['rows'][0]['detail']={'screenshot':str(tmp_path/'missing.png')}
    with manager.db() as db: manager.save(db,job)
    data=manager.get(job['id'])
    assert data['rows'][0]['detail']['screenshotExpired'] is True
    assert 'screenshot' not in data['rows'][0]['detail']
    assert manager.history(1,0)['total']==1
    assert manager.history(1,1)['items']==[]


def test_cleanup_api_requires_confirmation_and_local_header(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import ilog_tasks
    monkeypatch.setattr(ilog_tasks,'_manager',setup_store(tmp_path))
    client=TestClient(app)
    assert client.get('/api/ilog/retention').json()['screenshot_days']==90
    assert client.post('/api/ilog/retention/cleanup',json={'confirmed':True}).status_code==403
    headers={'X-Finance-Local':'1'}
    assert client.post('/api/ilog/retention/cleanup',json={'confirmed':False},headers=headers).status_code==400
    assert client.put('/api/ilog/retention',json={'screenshot_days':0,'record_days':365,'auto_enabled':True},headers=headers).status_code==422
    assert client.get('/api/ilog/retention/preview').json()['tasks']==0
    assert client.post('/api/ilog/retention/cleanup',json={'confirmed':True},headers=headers).status_code==200


def test_legacy_completed_timestamp_is_backfilled(tmp_path):
    manager=setup_store(tmp_path)
    job=manager.create('west','West',['TRBTSTZH2606S'],[],'old-key',True)
    old=(datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
    job.update(state='completed',createdAt=old)
    job['rows'][0].update(status='success',time=old)
    with manager.db() as db:
        db.execute('UPDATE tasks SET state=?,data=? WHERE id=?',('completed',json.dumps(job),job['id']))
    reopened=setup_store(tmp_path)
    assert reopened.get(job['id'])['completedAt']==old
    assert Retention(reopened).cleanup(dry_run=True)['tasks']==1
