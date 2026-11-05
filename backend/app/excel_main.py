"""Standalone Excel edition. Other product services are not registered or imported."""
from .web_common import bundled_frontend_dir, install_frontend
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

frontend_dir=bundled_frontend_dir()
install_frontend(app, frontend_dir, serve_files=False)
