"""Display an existing Outlook item; never send, move or modify it."""
import sys
from .outlook_probe import outlook_session
from .diagnostics import record


def open_original(store,identity):
    mail=store.get(identity)
    if not mail:raise ValueError('邮件记录不存在，请刷新列表。')
    search={k:mail.get(k,'') for k in ('subject','sender','received')}
    def failed(reason):return dict(opened=False,reason=reason,search=search)
    if sys.platform!='win32':return failed('直接打开原件需要 Windows 经典版 Outlook；当前电脑可查看程序内邮件详情。')
    sources=mail.get('sources',[])+[mail]
    locators=list(dict.fromkeys((s.get('outlook_entry_id'),s.get('outlook_store_id')) for s in sources if s.get('outlook_entry_id') and s.get('outlook_store_id')))
    if not locators:return failed('这封旧记录没有保存 Outlook 定位信息，请按下方主题、发件人和时间搜索原件；后续重新读取可补充定位信息。')
    try:
        with outlook_session() as session:
            for entry,source in locators:
                try:
                    item=session.GetItemFromID(entry,source)
                    if item.Class!=43:continue
                    item.Display(False)
                    return dict(opened=True)
                except Exception as error:record('outlook_open_original',error,mail_id=identity)
    except ValueError as error:return failed(str(error))
    return failed('无法定位原件。请确认对应邮箱或 PST 已在 Outlook 打开；邮件可能已移动、删除或存档位置发生变化。请按下方信息搜索。')
