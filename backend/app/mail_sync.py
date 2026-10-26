"""Persistent, fixed-window Outlook history and incremental checkpoints."""

from datetime import datetime, timedelta, timezone


class MailSync:
    def __init__(self, store): self.store=store

    def plan(self, account, folder, days, now=None):
        now=now or datetime.now(timezone.utc)
        key=[account,folder,days]
        progress=self.store.get_state('sync_progress',{})
        if progress.get('source')!=key:
            progress={'source':key,'phase':'history','start':(now-timedelta(days=days)).isoformat(),
                      'end':now.isoformat(),'cursor':None,'scanned':0,'watermark':None,'complete':False}
            self.store.set_state('sync_progress',progress)
        elif progress.get('complete'):
            progress={**progress,'phase':'incremental','start':(datetime.fromisoformat(progress['watermark'])-timedelta(days=1)).isoformat(),
                      'end':now.isoformat(),'cursor':None,'complete':False}
            self.store.set_state('sync_progress',progress)
        return progress

    def commit(self, plan, result):
        if result['partial'] and (not result.get('cursor') or result['cursor']==plan.get('cursor')):
            raise ValueError('邮箱读取进度未推进，请检查 Outlook 后重试。')
        current=self.store.get_state('sync_progress',{})
        if current.get('source')!=plan['source'] or current.get('end')!=plan['end']:
            raise ValueError('邮箱读取设置已变更，请重新读取。')
        current.update(cursor=result.get('cursor'),complete=not result['partial'],
                       scanned=current.get('scanned',0)+result.get('read',len(result.get('rows',[]))))
        if current['complete']: current['watermark']=plan['end']
        self.store.set_state('sync_progress',current)
        return current

    def reset(self): self.store.set_state('sync_progress',{})
