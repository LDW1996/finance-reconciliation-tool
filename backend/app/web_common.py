"""Shared local-request validation and frontend hosting, independent of services."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def check_local_request(request: Request, detail: str):
    origin = request.headers.get('origin')
    if request.headers.get('x-finance-local') != '1' or (origin and urlparse(origin).hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise HTTPException(403, detail)


def bundled_frontend_dir(override_env=None):
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'frontend_dist'
    default = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    return Path(os.environ.get(override_env, default)) if override_env else default


def install_frontend(app, directory, *, serve_files=True, require_exists=True):
    directory = Path(directory)
    if require_exists and not directory.exists():
        return
    if (directory / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=directory / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def frontend_page(path: str):
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404, 'Not found')
        target = (directory / path).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise HTTPException(404, 'Not found')
        if serve_files and path and target.is_file():
            return FileResponse(target)
        if (directory / 'index.html').is_file():
            return FileResponse(directory / 'index.html')
        raise HTTPException(404, 'Frontend not installed')
