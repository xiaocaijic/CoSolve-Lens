import logging
import os
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from .adapters import MAX_UPLOAD
from .api import router
from .core import Problem, Settings, database, digest, row_or_404, uid
from .http import COOKIE, TTL, User, create_login, validate_demo_origin
from . import analysis_api, exports, live, reviews, settings_api, showcase  # Registers feature routes before mounting the router.

LOGGER = logging.getLogger(__name__)


class Login(BaseModel):
    account: Literal["student-demo", "student-alt", "teacher-demo", "teacher-alt", "admin-demo"]


def error_response(request, status, code, message, details=None):
    return JSONResponse({"code": code, "message": message, "details": details or {},
                         "request_id": getattr(request.state, "request_id", None),
                         "retryable": status in (429, 503)}, status)


def create_app(settings=None):
    app = FastAPI(title="CoSolve Lens · 大学生 AI 协同问题解决评价系统", version="0.1.0")
    app.state.settings = settings or Settings()
    app.include_router(router)

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request.state.request_id = uid()
        length = request.headers.get("content-length")
        upload_path = request.url.path == "/api/datasets/imports"
        limit = MAX_UPLOAD + 32_768 if upload_path else 5 * 1024 * 1024
        allowed_hosts = set(os.getenv("AI_COLLAB_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(","))
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in allowed_hosts:
            response = error_response(request, 400, "INVALID_HOST", "服务地址不在允许列表内。")
        elif length and (not length.isdecimal() or int(length) > limit):
            response = error_response(request, 413, "FILE_TOO_LARGE", "请求大小超过允许上限。")
        elif request.method in ("POST", "PUT", "PATCH") and not length and request.headers.get("transfer-encoding"):
            response = error_response(request, 411, "LENGTH_REQUIRED", "请求需要提供内容长度。")
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Problem)
    async def problem(request: Request, exc: Problem):
        return error_response(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return error_response(request, 422, "INVALID_INPUT", "请检查输入字段和格式。",
                              {"fields": [list(error["loc"]) for error in exc.errors()]})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, exc.status_code, "HTTP_ERROR", "请求的地址或操作不可用。")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        LOGGER.error("Request %s failed: %s", request.state.request_id, type(exc).__name__)
        return error_response(request, 500, "INTERNAL_ERROR", "服务未能完成请求。")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "demo_login": app.state.settings.demo}

    @app.get("/api/readiness")
    def readiness():
        worker = getattr(app.state, "desktop_worker", None)
        if worker is not None and worker.poll() is not None:
            raise Problem(503, "WORKER_UNAVAILABLE", "评价进程已停止。请关闭本地服务后重新启动。")
        try:
            with database(app.state.settings) as db:
                db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        except Exception:
            raise Problem(503, "DATABASE_UNAVAILABLE", "数据库未就绪，请先执行迁移。") from None
        return {"status": "ready", "database": "ready", "execution": "fixture_simulation", "external_llm": "disabled"}

    @app.post("/api/auth/demo")
    def login(request: Request, response: Response, body: Login):
        settings = request.app.state.settings
        if not settings.demo:
            raise Problem(403, "DEMO_DISABLED", "当前环境关闭了演示登录。")
        validate_demo_origin(request)
        with database(settings, write=True) as db:
            user = row_or_404(db.execute("SELECT * FROM users WHERE id=? AND demo=1", (body.account,)).fetchone())
            old = request.cookies.get(COOKIE)
            if old:
                db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(old),))
            token, data = create_login(db, user)
        response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, samesite="lax", secure=not settings.demo)
        return data

    @app.get("/api/auth/me")
    def me(user: User):
        return user

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, user: User):
        with database(request.app.state.settings, write=True) as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(request.cookies[COOKIE]),))
        response.delete_cookie(COOKIE, secure=not request.app.state.settings.demo, httponly=True, samesite="lax")
        return {"status": "signed_out"}

    return app


app = create_app()
