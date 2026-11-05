"""Mail storage endpoints; manager state is obtained at request time."""
from fastapi import HTTPException, Response
from . import mail_tasks as tasks


def register(router):

    @router.get('/storage')
    def storage():
        try:return tasks.manager().storage.snapshot()
        except (tasks.sqlite3.Error,OSError) as error:
            info=tasks.describe_failure(error,phase='查询邮件存储占用',limit=0)
            raise HTTPException(503,info['message']) from None

    @router.put('/storage')
    def configure_storage(values: dict):
        try:
            if set(values)-{'max_gb','min_free_gb','queue_limit','preview_cache_mb'}:raise ValueError('包含未知的保存设置。')
            with tasks.manager().lock:
                if tasks.manager().maintenance_active:raise ValueError('存储优化进行中，请稍后修改规则。')
                tasks.manager().storage.configure(values)
            return tasks.manager().storage.snapshot()
        except ValueError as error:raise HTTPException(400,str(error)) from None

    @router.get('/storage/maintenance')
    def maintenance_status():return tasks.manager().maintenance_job()

    @router.get('/storage/maintenance/history')
    def maintenance_history():return tasks.manager().store.get_state('mail_maintenance_history',[])

    @router.post('/storage/maintenance/preview',status_code=202)
    def maintenance_preview():
        try:return tasks.manager().maintain_storage('preview')
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/maintenance/optimize',status_code=202)
    def maintenance_optimize(body: tasks.StorageOptimization):
        if not body.confirmed:raise HTTPException(400,'请先确认存储优化。')
        try:return tasks.manager().maintain_storage('optimize',body.preview_id,body.compact)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/storage/cleanup/backups')
    def cleanup_backups():
        from .mail_cleanup import MailCleanup
        return MailCleanup(tasks.manager().store).backups()

    @router.post('/storage/cleanup/preview',status_code=202)
    def cleanup_preview():
        try:return tasks.manager().maintain_storage('cleanup_preview')
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/cleanup/execute',status_code=202)
    def cleanup_execute(body: tasks.MailCleanupRequest):
        if not body.confirmed:raise HTTPException(400,'请先逐封核对并确认本地邮件清理。')
        try:return tasks.manager().maintain_storage('cleanup',body.preview_id,selected=body.selected)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/cleanup/restore',status_code=202)
    def cleanup_restore(body: tasks.MailRestoreRequest):
        try:return tasks.manager().maintain_storage('cleanup_restore',backup_id=body.backup_id)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/messages/{identity}/attachments/{attachment_id}/preview/{number}')
    def attachment_preview(identity:str,attachment_id:str,number:int):
        from .mail_assets import PreviewCache
        try:
            with tasks.manager().lock:
                if tasks.manager().maintenance_active:raise ValueError('存储优化进行中，请稍后查看原件预览。')
                if not 1<=number<=10:raise ValueError('页码无效。')
                value=PreviewCache(tasks.manager().store).get(identity,attachment_id,number)
            return Response(value,media_type='image/png',headers={'Cache-Control':'no-store'})
        except LookupError:raise HTTPException(404,'附件不存在。') from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
        except Exception:raise HTTPException(422,'附件预览生成失败，识别文字仍保留，请检查原始附件。') from None
