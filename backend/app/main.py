from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
# Compatibility exports used by integrations and tests; these are the same classes.
from .accounts import AccountStore
from . import ilog_tasks
from .ilog_retention import Retention
from .web_common import bundled_frontend_dir, check_local_request, install_frontend


app = FastAPI(title="财务Excel自动核对工具")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def require_local_account_write(request: Request):
    check_local_request(request, "请通过本机账号管理页面修改")


from .ilog_routes import create_accounts_router as create_ilog_accounts_router
from .ilog_routes import create_router as create_ilog_router
# Legacy order: the account list read was registered before the mail router.
app.include_router(create_ilog_accounts_router())

from .mail_tasks import create_router as create_mail_router
app.include_router(create_mail_router(require_local_account_write))
app.include_router(create_ilog_router(require_local_account_write))


from .excel_routes import router as excel_router
app.include_router(excel_router)

from .profit_center.routes import router as profit_center_router
app.include_router(profit_center_router)


# The public executable has one authenticated entry point. Legacy isolated apps
# remain development fixtures and are not included in the unified release.
from .platform_auth_routes import install_platform_auth
from .platform_runtime import install_runtime, transition_guard, on_login
platform_auth=install_platform_auth(app,transition_guard=transition_guard,on_login=on_login)
install_runtime(platform_auth)

# Outside auth middleware so login/permission diagnostics follow UI language too.
from .platform_locale import install_platform_locale
install_platform_locale(app)


frontend_dir = bundled_frontend_dir()
install_frontend(app, frontend_dir)
