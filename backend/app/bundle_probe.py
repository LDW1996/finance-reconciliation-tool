"""Offline frozen-runtime verification used only by the packaging smoke test."""
import json
import sys
from pathlib import Path


def check_browser_driver():
    from playwright.sync_api import sync_playwright
    # Starts the bundled Node bridge, not a browser, and never visits a website.
    with sync_playwright() as playwright:
        if playwright.chromium.name!='chromium':raise RuntimeError('Chromium driver unavailable')


def check_msg_parser():
    import extract_msg
    if not callable(extract_msg.openMsg):raise RuntimeError('MSG parser unavailable')


def check_outlook_components():
    if sys.platform=='win32':
        import pythoncom
        import pywintypes
        import win32com.client
        # No Outlook connection, permission prompt or mailbox read.


def write_report(path, checks=None):
    checks=checks if checks is not None else {
        'playwright_driver':check_browser_driver,
        'msg_parser':check_msg_parser,
        'outlook_components':check_outlook_components,
    }
    results={}
    for name,check in checks.items():
        try:check();results[name]={'ok':True}
        except Exception as error:results[name]={'ok':False,'error':f'{type(error).__name__}: {error}'}
    ok=all(row['ok'] for row in results.values())
    Path(path).write_text(json.dumps(dict(ok=ok,checks=results),ensure_ascii=False,indent=2),encoding='utf-8')
    return ok
