"""Permission-aware task lifecycle; never rewrites business checkpoints on logout."""
from fastapi import HTTPException
from . import mail_tasks, ilog_tasks


def install_runtime(auth):
    mail_tasks._platform_permission=lambda:auth.allowed('mail')
    ilog_tasks._platform_permission=lambda:auth.allowed('ilog')


def transition_guard(request,next_username):
    auth=request.app.state.platform_auth
    if getattr(auth,'business_requests',0):
        raise HTTPException(409,'A request is still running. Wait for it to finish before signing out or switching accounts.')
    from contextlib import ExitStack
    import json
    current=mail_tasks._manager
    tasks=ilog_tasks._manager
    with ExitStack() as stack:
        stack.enter_context(ilog_tasks.platform_transition_lock)
        if current is not None:
            stack.enter_context(current.lock)
            if current.state['busy'] or current.maintenance_active or current.analysis_busy:
                raise HTTPException(409,'Mail is processing; retry after current work finishes.')
        rows=[]
        if tasks is not None and hasattr(tasks,'db'):
            with tasks.db() as db:
                rows=db.execute("SELECT id,data FROM tasks WHERE state='running'").fetchall()
            for row in rows:
                if any(item.get('status')=='processing' for item in json.loads(row['data'])['rows']):
                    raise HTTPException(409,'An iLog operation is in progress. Wait for the current item before signing out.')
        # Both workers are held at their acquisition boundary before any mutation.
        for row in rows:tasks.control(row['id'],'pause')
        if current is not None and hasattr(current,'suspend_platform'):
            if not current.suspend_platform():
                raise HTTPException(409,'Mail is processing; retry after current work finishes.')
    return True


def on_login(request,identity):
    if 'mail' in identity['capabilities']:
        existed=mail_tasks._manager is not None
        current=mail_tasks.manager()
        if existed and hasattr(current,'resume_platform'):current.resume_platform()
