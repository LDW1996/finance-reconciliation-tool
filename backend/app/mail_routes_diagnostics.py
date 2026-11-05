"""Mail diagnostics endpoints; manager state is obtained at request time."""
from fastapi import Query, Response
from . import mail_tasks as tasks


def register(router):

    @router.get('/diagnostics/status')
    def diagnostics_status():
        from .diagnostics import health
        current=tasks.manager()
        # User-triggered check records actual write success, not an assumption.
        tasks.diagnostic_record('diagnostic_health_check')
        root=current.store.path.parent
        log=root/'mail-errors.log'
        return {**health(),'data_directory':str(root),'database_path':str(current.store.path),
                'queue_log_path':str(log),'queue_log_exists':log.is_file(),
                'queue_log_write_error':current.state.get('queue_log_write_error','')}

    @router.get('/diagnostics/export')
    def diagnostics_export(days: int=Query(3,ge=1,le=14)):
        return Response(tasks.export_diagnostics(days),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="mail-diagnostics.zip"'})
