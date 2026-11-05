from datetime import datetime, timezone
from pathlib import Path
from app import outlook_probe as probe


def test_failed_attachment_keeps_reason_and_other_mail_continues(monkeypatch):
    events=[]
    monkeypatch.setattr(probe,'diagnostic_record',lambda stage,error=None,**context:events.append((stage,type(error).__name__,context)))
    class Attachment:
        FileName='receipt.png';Size=8
        def __init__(self,fail):self.fail=fail
        def SaveAsFile(self,path):
            if self.fail:raise PermissionError('private account and server details')
            Path(path).write_bytes(b'receipt!')
    class Attachments:
        Count=2
        def Item(self,index):return Attachment(index==1)
    class Mail:
        Class=43;ReceivedTime=datetime.now(timezone.utc);Subject='private';Body='body';SenderEmailAddress='x@example.invalid'
        def __init__(self,id):self.EntryID=id
    Mail.Attachments=Attachments()
    class Items:
        Count=2
        def Sort(self,*args):pass
        def Item(self,index):return Mail(str(index))
    result=probe.scan_folder(type('Folder',(),{'Items':Items()})(),'store',datetime(2020,1,1,tzinfo=timezone.utc),10)
    assert len(result['rows'])==2
    failed,good=result['rows'][0]['_attachments']
    assert failed['status']=='failed'
    assert failed['error_code']=='attachment_permission_denied'
    assert failed['error_type']=='PermissionError'
    assert 'private' not in failed['error']
    assert good['raw']==b'receipt!'
    assert events[0][0]=='attachment_read_failed'
