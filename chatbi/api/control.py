"""Authenticated control-plane endpoints and same-origin enforcement."""
from dataclasses import asdict
from urllib.parse import urlparse
import sqlite3

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from chatbi.infrastructure.control_store import AccessDenied, Principal, principal_context, source_context
from chatbi.infrastructure.data_sources import SourceDraft, discover_mysql, validate_allowlist


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(max_length=256)


class NewUser(Login):
    tenant: str = Field(default="default", max_length=80)
    role: str = "analyst"


class UserUpdate(BaseModel):
    active: bool
    role: str


class SourceGrant(BaseModel):
    user_id: str
    policy: dict = Field(default_factory=dict)


def install_control(app, runtime):
    settings, store = runtime.settings, runtime.store

    @app.exception_handler(AccessDenied)
    async def access_error(request, exc):
        return JSONResponse(status_code=403, content={"message": str(exc)})

    @app.middleware("http")
    async def identity(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        public = path in {"/api/v1/auth/status", "/api/v1/auth/login", "/api/v1/auth/setup", "/api/v1/runtime"}
        actor = store.authenticate(request.cookies.get("chatbi_session")) if settings.auth_enabled else Principal()
        if settings.auth_enabled and not public and actor is None:
            return JSONResponse(status_code=401, content={"message": "请先登录"})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (origin and (urlparse(origin).netloc != request.url.netloc or urlparse(origin).scheme != request.url.scheme)) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(status_code=403, content={"message": "拒绝跨站请求"})
        source_id = request.headers.get("x-data-source") or request.query_params.get("source_id", "demo")
        if actor and not public and not path.startswith(("/api/v1/auth", "/api/v1/users", "/api/v1/sources")):
            try:
                store.source(actor, source_id)
            except AccessDenied as exc:
                return JSONResponse(status_code=403, content={"message": str(exc)})
        a, s = principal_context.set(actor or Principal()), source_context.set(source_id)
        try:
            return await call_next(request)
        finally:
            principal_context.reset(a)
            source_context.reset(s)

    @app.get("/api/v1/auth/status")
    def status(request: Request):
        actor = store.authenticate(request.cookies.get("chatbi_session")) if settings.auth_enabled else Principal()
        return {"enabled": settings.auth_enabled, "needs_setup": settings.auth_enabled and store.needs_setup(),
                "user": asdict(actor) if actor else None}

    @app.post("/api/v1/auth/setup", status_code=201)
    def setup(payload: Login, request: Request):
        if not settings.auth_enabled or not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise AccessDenied("初始化仅允许本机访问")
        try:
            actor = store.create_user(payload.username, payload.password.get_secret_value(), bootstrap=True)
        except AccessDenied:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Adopt legacy local history once, retaining the original audit events.
        with app.state.history.connect() as db:
            import json
            for row in db.execute("SELECT id,request_options FROM queries").fetchall():
                options = json.loads(row[1] or "{}")
                if not options.get("owner"):
                    options.update(owner=actor.user_id, tenant=actor.tenant_id, source_id="demo")
                    db.execute("UPDATE queries SET request_options=? WHERE id=?", (json.dumps(options), row[0]))
        return {"user": asdict(actor)}

    @app.post("/api/v1/auth/login")
    def login(payload: Login, request: Request):
        if not settings.auth_enabled:
            return {"user": asdict(Principal())}
        token, actor = store.login(payload.username, payload.password.get_secret_value(), settings.auth_session_seconds,
                                   peer=request.client.host if request.client else "unknown")
        response = JSONResponse({"user": asdict(actor)})
        response.set_cookie("chatbi_session", token, httponly=True, secure=settings.auth_cookie_secure,
                            samesite="strict", max_age=settings.auth_session_seconds, path="/")
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        store.logout(request.cookies.get("chatbi_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("chatbi_session")
        return response

    @app.get("/api/v1/users")
    def users():
        return {"items": store.list_users(principal_context.get())}

    @app.post("/api/v1/users", status_code=201)
    def create_user(payload: NewUser):
        try:
            return asdict(store.create_user(payload.username, payload.password.get_secret_value(), tenant=payload.tenant,
                          role=payload.role, actor=principal_context.get()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在")
        except ValueError as exc:
            if isinstance(exc, AccessDenied):
                raise
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/v1/users/{user_id}")
    def update_user(user_id: str, payload: UserUpdate):
        store.update_user(principal_context.get(), user_id, **payload.model_dump())
        return {"ok": True}

    @app.get("/api/v1/sources")
    def sources():
        return {"items": store.list_sources(principal_context.get())}

    @app.post("/api/v1/sources/discover")
    def discover(payload: SourceDraft):
        if principal_context.get().role != "admin":
            raise AccessDenied("需要管理员权限")
        try:
            found = discover_mysql(payload.config(), payload.password.get_secret_value())
            return {"schema": found, "column_types": getattr(found, "column_types", {})}
        except Exception as exc:
            # Vendor errors may contain host, username and credentials.
            message = str(exc) if type(exc) is ValueError else "连接失败，请检查地址、TLS 与只读账号"
            raise HTTPException(422, message) from None

    def save_source(payload, source_id=None):
        actor = principal_context.get()
        if actor.role != "admin":
            raise AccessDenied("需要管理员权限")
        try:
            discovered = discover_mysql(payload.config(), payload.password.get_secret_value())
            schema = validate_allowlist(payload.tables, discovered)
            types = {t: {f: k for f, k in fs.items() if f in schema.get(t, ())}
                     for t, fs in getattr(discovered, "column_types", {}).items() if t in schema}
            identity = store.save_source(actor, name=payload.name, config={**payload.config(), "column_types": types},
                secret=runtime.vault.encrypt(payload.password.get_secret_value()), schema=schema, source_id=source_id)
            return store.public_source(store.source(actor, identity))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(422, "连接验证失败，数据源未保存") from None

    @app.post("/api/v1/sources", status_code=201)
    def create_source(payload: SourceDraft):
        return save_source(payload)

    @app.put("/api/v1/sources/{source_id}")
    def update_source(source_id: str, payload: SourceDraft):
        store.source(principal_context.get(), source_id)
        if source_id == "demo":
            raise AccessDenied("演示库使用启动配置管理")
        return save_source(payload, source_id)

    @app.delete("/api/v1/sources/{source_id}")
    def delete_source(source_id: str):
        store.delete_source(principal_context.get(), source_id)
        return {"ok": True}

    @app.post("/api/v1/sources/{source_id}/grants")
    def grant_source(source_id: str, payload: SourceGrant):
        try:
            store.grant_source(principal_context.get(), source_id, payload.user_id, payload.policy)
        except ValueError as exc:
            if isinstance(exc, AccessDenied):
                raise
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}


    @app.delete("/api/v1/sources/{source_id}/grants/{user_id}")
    def revoke_source(source_id: str, user_id: str):
        store.revoke_source(principal_context.get(),source_id,user_id)
        return {"ok":True}
