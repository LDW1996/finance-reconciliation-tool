"""Standalone Excel edition. Other product services are not registered or imported."""
from pathlib import Path
import sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .excel_routes import router

app=FastAPI(title='Excel对账工具')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],
                   allow_methods=['*'],allow_headers=['*'])
app.include_router(router)

@app.get('/api/health')
def health():return {'status':'ok','edition':'excel'}

# Prevent API typos from falling through to the frontend HTML.
@app.api_route('/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'])
def unavailable(path:str):raise HTTPException(404,'该版本仅提供 Excel 对账功能')

frontend_dir=(Path(sys._MEIPASS)/'frontend_dist') if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]/'frontend'/'dist'
if frontend_dir.exists():
    if (frontend_dir/'assets').exists():app.mount('/assets',StaticFiles(directory=frontend_dir/'assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def frontend(path:str):
        return FileResponse(frontend_dir/'index.html')
