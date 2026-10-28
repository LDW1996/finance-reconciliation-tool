"""Frozen runtime probe for the standalone Excel edition."""
import json
from pathlib import Path

def write_report(path):
    try:
        import uvicorn
        config=uvicorn.Config('app.excel_main:app',log_config=None)
        config.load();config.setup_event_loop()
        # Inspect the original app explicitly; middleware wrappers vary by uvicorn version.
        from .excel_main import app
        assert not any(route.path.startswith(('/api/ilog','/api/mail')) for route in app.routes)
        result={'ok':True,'checks':{'excel_server':{'ok':True}}}
    except Exception as error:
        result={'ok':False,'checks':{'excel_server':{'ok':False,'error':f'{type(error).__name__}: {error}'}}}
    Path(path).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result['ok']
