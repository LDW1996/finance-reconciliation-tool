"""Install before serving: middleware rejects requests before business initialization."""
import inspect
import secrets
import sys
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from .platform_auth import COOKIE_NAME, PlatformAuth, hash_password, verify_password
from .platform_roles import required_capability, ROLE_CAPABILITIES

def accounts_capabilities(username):
    return ROLE_CAPABILITIES[username]


def install_platform_auth(app, *, credentials_path=None, transition_guard=None, on_login=None):
    auth = PlatformAuth(credentials_path, transition_guard)
    app.state.platform_auth = auth
    dummy = hash_password(secrets.token_urlsafe(32))

    async def guard(request, next_identity):
        if auth.transition_guard:
            result = auth.transition_guard(request, next_identity)
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                raise HTTPException(409, "Background operation must reach a safe stopping point")

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or (parsed.netloc != request.headers.get("host") and (getattr(sys,"frozen",False) or origin not in {"http://localhost:5173","http://127.0.0.1:5173","http://localhost:8765","http://127.0.0.1:8765"})):
                    return JSONResponse({"detail": "Local origin required"}, status_code=403)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-requested-with") != "XMLHttpRequest" and request.headers.get("x-finance-local") != "1":
                return JSONResponse({"detail": "Local mutation header required"}, status_code=403)
            identity = auth.identity(request.cookies.get(COOKIE_NAME))
            request.state.platform_identity = identity
            if path not in {"/api/health", "/api/platform/login", "/api/platform/session"}:
                if not identity:
                    return JSONResponse({"detail": "Authentication required"}, status_code=401)
                capability = required_capability(path)
                if path != "/api/platform/logout" and (not capability or capability not in identity["capabilities"]):
                    return JSONResponse({"detail": "Permission denied"}, status_code=403)
        business=path.startswith('/api/') and not path.startswith('/api/platform/') and path!='/api/health'
        if business:auth.business_requests=getattr(auth,'business_requests',0)+1
        try:return await call_next(request)
        finally:
            if business:auth.business_requests-=1

    @app.get("/api/platform/session")
    async def session(request: Request):
        identity = auth.identity(request.cookies.get(COOKIE_NAME))
        if not identity:
            raise HTTPException(401, "Authentication required")
        return identity

    @app.post("/api/platform/login")
    async def login(request: Request):
        if not auth.allow_attempt(request.client.host if request.client else "local"):
            raise HTTPException(429, "Too many login attempts")
        try:
            body = await request.json()
            username, password = body.get("username"), body.get("password")
        except (ValueError, AttributeError):
            raise HTTPException(400, "Invalid login request")
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
            raise HTTPException(401, "Invalid username or password")
        try:
            accounts = auth.accounts()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        valid = verify_password(password, accounts.get(username, dummy))
        if username not in accounts or not valid:
            raise HTTPException(401, "Invalid username or password")
        await guard(request, username)
        previous = dict(auth.sessions)
        token = secrets.token_urlsafe(48)
        auth.sessions.clear()
        auth.sessions[token] = username
        if on_login:
            try:
                result = on_login(request, auth.identity(token))
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                auth.sessions.clear()
                auth.sessions.update(previous)
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(503, "Authorized runtime could not start")
        response = JSONResponse(auth.identity(token))
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=False, path="/")
        return response

    @app.post("/api/platform/logout")
    async def logout(request: Request):
        await guard(request, None)
        auth.sessions.clear()
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response
    return auth
