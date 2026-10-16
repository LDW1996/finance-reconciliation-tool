"""Expiry cleanup never removes submission guards or unresolved evidence."""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

from .ilog_tasks import worker_lock


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError):
        return None


def references(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in ('screenshot','report') and isinstance(item,str): yield Path(item).resolve()
            else: yield from references(item)
    elif isinstance(value,list):
        for item in value: yield from references(item)


class Retention:
    def __init__(self,manager):
        self.manager=manager

    def settings(self):
        with self.manager.db() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='retention'").fetchone()
        return json.loads(row[0]) if row else {'screenshot_days':90,'record_days':365,'auto_enabled':True}

    def configure(self,screenshot_days,record_days,auto_enabled):
        if any(type(n) is not int or not 1<=n<=36500 for n in (screenshot_days,record_days)) or type(auto_enabled) is not bool:
            raise ValueError('保留期限需为 1–36500 天')
        value={'screenshot_days':screenshot_days,'record_days':record_days,'auto_enabled':auto_enabled}
        with self.manager.db() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('retention',?)",(json.dumps(value),))
            db.execute("DELETE FROM metadata WHERE key='last_cleanup'")
        return value

    def maybe_cleanup(self):
        if not self.settings()['auto_enabled']: return
        with self.manager.db() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='last_cleanup'").fetchone()
        last=timestamp(row[0]) if row else None
        if not last or datetime.now(timezone.utc)-last>=timedelta(days=1): self.cleanup()

    def cleanup(self,dry_run=False):
        result={'tasks':0,'screenshots':0,'reports':0,'protected_tasks':0,'busy':False,'errors':0}
        with worker_lock(self.manager.directory) as acquired:
            if not acquired:
                result['busy']=True;return result
            settings=self.settings();now=datetime.now(timezone.utc)
            record_cutoff=now-timedelta(days=settings['record_days'])
            image_cutoff=(now-timedelta(days=settings['screenshot_days'])).timestamp()
            with self.manager.db() as db:
                db.execute('BEGIN IMMEDIATE')
                protected=set();expired=[]
                for row in db.execute('SELECT * FROM tasks').fetchall():
                    data=json.loads(row['data'])
                    safe=data['state']=='completed' and all(r['status'] in ('success','cancelled') for r in data['rows'])
                    completed=timestamp(data.get('completedAt'))
                    if safe and completed and completed<record_cutoff:
                        expired.append(row)
                    else:
                        if not safe: result['protected_tasks']+=1
                        if not safe: protected.update(references(data))
                roots=[self.manager.directory.parent/'unlock-results',self.manager.directory.parent/'query-results']
                reports=[];images=[]
                safe_reports={'inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked'}
                for root in roots:
                    if not root.exists() or root.is_symlink(): continue
                    for path in root.glob('*.json'):
                        if path.is_symlink() or not path.is_file(): continue
                        try:
                            report=json.loads(path.read_text())
                        except (OSError,ValueError):
                            # Unknown reports are retained, not guessed to be disposable.
                            protected.add(path.resolve());protected.add(path.with_suffix('.png').resolve());continue
                        if not isinstance(report,dict) or report.get('status') not in safe_reports:
                            protected.add(path.resolve());protected.update(references(report))
                        reports.append(path)
                    images.extend(p for p in root.glob('*.png') if p.is_file() and not p.is_symlink())
                result['tasks']=len(expired)
                for path,label,cutoff in [(p,'screenshots',image_cutoff) for p in images]+[(p,'reports',record_cutoff.timestamp()) for p in reports]:
                    if path.resolve() in protected: continue
                    try:
                        if path.stat().st_mtime>=cutoff: continue
                        if not dry_run: path.unlink()
                        result[label]+=1
                    except OSError:
                        result['errors']+=1
                if not dry_run:
                    for row in expired:
                        # Preserve the confirmation key so expiry cannot replay an old request.
                        db.execute('INSERT OR IGNORE INTO expired_requests VALUES (?,?)',(row['request_key'],row['id']))
                        db.execute('DELETE FROM tasks WHERE id=?',(row['id'],))
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_cleanup',?)",(now.isoformat(),))
        return result
