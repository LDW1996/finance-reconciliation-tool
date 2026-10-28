"""Read a selected Outlook/PST scope with one durable, bounded page cursor."""
from . import outlook_probe as outlook

MAX_FOLDERS = 5000


def folder_tree(session):
    rows, errors, visited = [], [], set()
    for index in range(1, session.Stores.Count + 1):
        try:
            store = session.Stores.Item(index)
            pending = [(store.GetRootFolder(), None, str(store.DisplayName))]
            while pending:
                folder, parent, path = pending.pop()
                try:
                    key = (str(store.StoreID), str(folder.EntryID))
                    if key in visited: continue
                    if len(rows) >= MAX_FOLDERS:
                        errors.append('目录数量超过 5000，列表已截断；请减少 Outlook 中打开的存档后重新连接。')
                        return dict(folders=rows, errors=errors, note='目录列表不完整，请处理提示后重试。')
                    visited.add(key)
                    rows.append(dict(id=key[1], store=key[0], label=path, name=str(folder.Name),
                                     parent=parent, store_label=str(store.DisplayName),
                                     selectable=getattr(folder, 'DefaultItemType', 0) == 0))
                    children=[]
                    for i in range(1, folder.Folders.Count + 1):
                        try:
                            child=folder.Folders.Item(i)
                            children.append((child, key[1], path+' / '+str(child.Name)))
                        except Exception as error:
                            errors.append(f'{path}：第 {i} 个子目录无法访问（{type(error).__name__}）。')
                    pending.extend(reversed(children))
                except Exception as error:
                    errors.append(f'{path}：目录无法访问（{type(error).__name__}）。')
        except Exception as error:
            errors.append(f'第 {index} 个邮箱或存档无法访问（{type(error).__name__}），请确认已打开且有权限。')
    return dict(folders=rows, errors=errors, note='列出 Outlook 已打开的邮箱和 PST 存档。未打开的存档不在列表中。')


def read_scope_page(selections, include_children, start, end, limit, cursor=None, known_unchanged=None):
    with outlook.outlook_session() as session:
        if cursor:
            targets=cursor['targets']; position=cursor['position']; page_cursor=cursor.get('page')
            completed=list(cursor.get('completed', [])); errors=list(cursor.get('errors', []))
        else:
            targets=[]; errors=[]; completed=[]; position=0; page_cursor=None; seen=set()
            for selection in selections:
                pending=[dict(selection)]
                while pending:
                    target=pending.pop()
                    key=(target['store'],target['folder'])
                    if key in seen: continue
                    if len(targets)>=MAX_FOLDERS: raise ValueError('读取目录超过 5000，请缩小范围。')
                    seen.add(key)
                    try:
                        folder=session.GetFolderFromID(target['folder'],target['store'])
                        target['label']=str(folder.FolderPath)
                        if getattr(folder,'DefaultItemType',0)==0: targets.append(target)
                        if include_children:
                            children=[]
                            for i in range(1,folder.Folders.Count+1):
                                try: children.append(dict(store=target['store'],folder=str(folder.Folders.Item(i).EntryID)))
                                except Exception as error: errors.append(f"{target['label']}：第 {i} 个子目录读取失败（{type(error).__name__}）。")
                            pending.extend(reversed(children))
                    except Exception as error:
                        errors.append(f"{target.get('label', '所选目录')}：无法打开（{type(error).__name__}），请确认存档已挂载，可重新补扫。")
        rows=[]; scanned=unchanged=0; attachment_seconds=0.0
        # At most 50 folders per batch, including empty ones, to bound COM work.
        visited=0
        while position<len(targets) and scanned<limit and visited<50:
            target=targets[position]; visited+=1
            try:
                folder=session.GetFolderFromID(target['folder'],target['store'])
                result=outlook.scan_folder_page(folder,target['store'],start,end,limit-scanned,page_cursor,**({'known_unchanged':known_unchanged} if known_unchanged else {}))
                scanned += result.get('read',len(result['rows']))
                unchanged += result.get('unchanged',0)
                attachment_seconds += result.get('attachment_seconds',0)
                for row in result['rows']:
                    row['folder']=target['label']; row['outlook_store']=target['store']
                rows.extend(result['rows'])
                errors.extend(f"{target['label']}：{error}" for error in result['errors'])
                if result['partial']:
                    if not result.get('cursor') or result['cursor']==page_cursor:
                        raise ValueError('分页进度未推进，请重新补扫。')
                    page_cursor=result['cursor']; break
                completed.append(target['label'])
            except Exception as error:
                detail=str(error).replace('\n',' ')[:500] if isinstance(error,ValueError) else type(error).__name__
                errors.append(f"{target['label']}：读取失败（{type(error).__name__}：{detail}）。该目录已跳过，其余目录继续；处理原因后重新补扫。")
            position+=1; page_cursor=None
        partial=position<len(targets)
        checkpoint=dict(targets=targets,position=position,page=page_cursor,completed=completed,errors=errors) if partial else None
        return dict(rows=rows,errors=errors,partial=partial,cursor=checkpoint,read=scanned,unchanged=unchanged,attachment_seconds=attachment_seconds,
                    folders_total=len(targets),folders_done=position,completed_folders=completed)
