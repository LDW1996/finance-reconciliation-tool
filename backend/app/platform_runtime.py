"""Permission-aware task lifecycle; never rewrites business checkpoints on logout."""
from fastapi import HTTPException
from . import mail_tasks, ilog_tasks

# 邮件扫描/识别可能占用 manager 锁数分钟。登录请求最多等这么久，避免整条登录链路静默悬挂。
MAIL_TRANSITION_TIMEOUT = 60.0


def install_runtime(auth):
    mail_tasks._platform_permission=lambda:auth.allowed('mail')
    ilog_tasks._platform_permission=lambda:auth.allowed('ilog')


def transition_guard(request,next_username):
    auth=request.app.state.platform_auth
    if getattr(auth,'business_requests',0):
        raise HTTPException(409,'A request is still running. Wait for it to finish before signing out or switching accounts.')
    import json
    current=mail_tasks._manager
    tasks=ilog_tasks._manager
    if current is not None and (current.state['busy'] or current.maintenance_active or current.analysis_busy):
        raise HTTPException(409,'Mail is processing; retry after current work finishes.')
    paused=[]
    if tasks is not None and hasattr(tasks,'db'):
        # 只在停人这一步短暂持有平台切换锁，随后立刻释放：解锁工作线程不该为登录流程排队几分钟。
        with ilog_tasks.platform_transition_lock:
            with tasks.db() as db:
                rows=db.execute("SELECT id,state,data FROM tasks WHERE state IN ('running','paused','closing')").fetchall()
            for row in rows:
                if any(item.get('status')=='processing' for item in json.loads(row['data'])['rows']):
                    raise HTTPException(409,'An iLog operation is in progress. Wait for the current item before signing out.')
            for row in rows:
                if row['state'] == 'running':
                    tasks.pause_with_notice(row['id'],ilog_tasks.TRANSITION_BUSY_NOTICE)
                    paused.append(row['id'])
    try:
        if current is not None:
            if not current.lock.acquire(timeout=MAIL_TRANSITION_TIMEOUT):
                raise HTTPException(409,'Mail is processing; retry after current work finishes.')
            try:
                if current.state['busy'] or current.maintenance_active or current.analysis_busy:
                    raise HTTPException(409,'Mail is processing; retry after current work finishes.')
                if not current.suspend_platform():
                    raise HTTPException(409,'Mail is processing; retry after current work finishes.')
            finally:
                current.lock.release()
    except Exception:
        # 登录被拒绝时不留下"莫名暂停"的任务。
        for task_id in paused:
            try: tasks.control(task_id,'resume')
            except Exception: pass
        raise
    return True


def on_login(request,identity):
    if 'mail' in identity['capabilities']:
        existed=mail_tasks._manager is not None
        current=mail_tasks.manager()
        if existed and hasattr(current,'resume_platform'):current.resume_platform()
