"""Authenticated identities and tenant-owned control resources."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from chatbi.infrastructure.control_database import connect as connect_control
import time
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Principal:
    user_id: str = "local-demo"
    tenant_id: str = "default"
    username: str = "本机用户"
    role: str = "admin"
    platform_admin: bool = False


principal_context: ContextVar[Principal] = ContextVar("principal", default=Principal())
source_context: ContextVar[str] = ContextVar("data_source", default="demo")


class AccessDenied(ValueError):
    pass


class ControlStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL,
                platform_admin INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS login_attempts(identity TEXT PRIMARY KEY,count INTEGER,until REAL);
              CREATE TABLE IF NOT EXISTS data_sources(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,
                name TEXT NOT NULL,config TEXT NOT NULL,secret TEXT NOT NULL,schema_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS source_grants(source_id TEXT,user_id TEXT,policy TEXT NOT NULL,
                PRIMARY KEY(source_id,user_id));
              CREATE TABLE IF NOT EXISTS feedback_cases(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,
                source_id TEXT NOT NULL,query_id TEXT NOT NULL,question TEXT NOT NULL,note TEXT NOT NULL,
                sql TEXT NOT NULL DEFAULT '',expected TEXT NOT NULL DEFAULT '',catalog_revision TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',author TEXT NOT NULL,reviewer TEXT,
                created REAL NOT NULL,updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS control_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,
                at REAL NOT NULL,actor TEXT NOT NULL,tenant TEXT NOT NULL,action TEXT NOT NULL,
                resource TEXT NOT NULL,detail TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = connect_control(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password: str) -> str:
        if len(password) < 12 or len(password) > 256:
            raise ValueError("密码长度应为12至256位")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
        return f"pbkdf2_sha256$600000${salt}${digest.hex()}"

    @staticmethod
    def password_matches(password: str, encoded: str) -> bool:
        try:
            _, rounds, salt, expected = encoded.split("$")
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
            return hmac.compare_digest(actual.hex(), expected)
        except (ValueError, TypeError):
            return False

    def audit(self, actor: Principal, action: str, resource: str, detail: dict | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO control_audit(at,actor,tenant,action,resource,detail) VALUES(?,?,?,?,?,?)",
                (time.time(), actor.user_id, actor.tenant_id, action, resource, json.dumps(detail or {}, ensure_ascii=False)))

    def needs_setup(self) -> bool:
        with self.connect() as db:
            return not db.execute("SELECT 1 FROM users LIMIT 1").fetchone()

    def create_user(self, username: str, password: str, *, tenant: str = "default", role: str = "analyst",
                    actor: Principal | None = None, bootstrap: bool = False) -> Principal:
        if role not in {"admin", "reviewer", "analyst"}:
            raise ValueError("未知角色")
        if not username.strip() or len(username) > 80 or not tenant or len(tenant) > 80:
            raise ValueError("用户名或租户无效")
        hashed = self.password_hash(password)
        user_id = uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if bootstrap:
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    raise AccessDenied("管理员已初始化")
                role, tenant = "admin", "default"
            elif actor is None or actor.role != "admin" or (tenant != actor.tenant_id and not actor.platform_admin):
                raise AccessDenied("无权创建用户")
            db.execute("INSERT INTO users(id,tenant,username,password,role,platform_admin) VALUES(?,?,?,?,?,?)",
                (user_id, tenant, username.strip(), hashed, role, int(bootstrap)))
        result = Principal(user_id, tenant, username.strip(), role, bootstrap)
        self.audit(actor or result, "user_created", user_id, {"role": role, "tenant": tenant})
        return result

    def user(self, user_id: str) -> Principal | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        return Principal(row["id"], row["tenant"], row["username"], row["role"], bool(row["platform_admin"])) if row else None

    def login(self, username: str, password: str, ttl: int, *, peer: str = "local") -> tuple[str, Principal]:
        identity = hashlib.sha256((peer + "|" + username).encode()).hexdigest()
        with self.connect() as db:
            attempt = db.execute("SELECT * FROM login_attempts WHERE identity=?", (identity,)).fetchone()
            if attempt and attempt["count"] >= 8 and attempt["until"] > time.time():
                raise AccessDenied("登录尝试过多，请稍后重试")
            row = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            # Constant work even for nonexistent users.
            dummy = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
            if not self.password_matches(password, row["password"] if row else dummy) or not row:
                count = attempt["count"] + 1 if attempt and attempt["until"] > time.time() else 1
                db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (identity, count, time.time() + 300))
                invalid = True
            else:
                db.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))
                invalid = False
        if invalid:
            raise AccessDenied("用户名或密码错误")
        token = secrets.token_urlsafe(40)
        user = self.user(row["id"])
        assert user
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user.user_id, time.time() + ttl))
        self.audit(user, "login", user.user_id)
        return token, user

    def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM sessions WHERE token=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return self.user(row[0]) if row else None

    def logout(self, token: str | None):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def list_users(self, actor: Principal) -> list[dict]:
        if actor.role != "admin":
            raise AccessDenied("需要管理员权限")
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,tenant,username,role,active FROM users WHERE tenant=?", (actor.tenant_id,))]

    def update_user(self, actor: Principal, user_id: str, *, active: bool, role: str):
        with self.connect() as db:
            target = db.execute("SELECT tenant FROM users WHERE id=?", (user_id,)).fetchone()
        if actor.role != "admin" or not target or target[0] != actor.tenant_id or user_id == actor.user_id:
            raise AccessDenied("不能修改该用户或停用自身")
        if role not in {"admin", "reviewer", "analyst"}:
            raise ValueError("未知角色")
        with self.connect() as db:
            db.execute("UPDATE users SET active=?,role=? WHERE id=?", (int(active), role, user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.audit(actor, "user_updated", user_id, {"active": active, "role": role})

    def source(self, actor: Principal, source_id: str) -> dict[str, Any]:
        if source_id == "demo" and actor.tenant_id == "default":
            return {"id": "demo", "tenant": "default", "name": "演示业务库", "config": {}, "schema": {}, "revision": 1}
        with self.connect() as db:
            row = db.execute("SELECT * FROM data_sources WHERE id=? AND tenant=? AND active=1", (source_id, actor.tenant_id)).fetchone()
            grant = db.execute("SELECT policy FROM source_grants WHERE source_id=? AND user_id=?", (source_id, actor.user_id)).fetchone()
        if not row or (actor.role != "admin" and not grant):
            raise AccessDenied("数据源不存在或未授权")
        data = dict(row)
        data["config"] = json.loads(data["config"])
        data["schema"] = json.loads(data.pop("schema_json"))
        data["policy"] = json.loads(grant[0]) if grant else {}
        return data

    @staticmethod
    def public_source(source: dict) -> dict:
        return {key: value for key, value in source.items() if key not in {"secret"}}

    def list_sources(self, actor: Principal) -> list[dict]:
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM data_sources WHERE tenant=? AND active=1", (actor.tenant_id,))]
        sources = [self.source(actor, "demo")] if actor.tenant_id == "default" else []
        for source_id in ids:
            try:
                sources.append(self.public_source(self.source(actor, source_id)))
            except AccessDenied:
                continue
        return sources

    def save_source(self, actor: Principal, *, name: str, config: dict, secret: str,
                    schema: dict, source_id: str | None = None) -> str:
        if actor.role != "admin":
            raise AccessDenied("需要管理员权限")
        source_id = source_id or uuid4().hex
        with self.connect() as db:
            existing = db.execute("SELECT tenant FROM data_sources WHERE id=?", (source_id,)).fetchone()
            if existing and existing[0] != actor.tenant_id:
                raise AccessDenied("无权修改该数据源")
            db.execute("""INSERT INTO data_sources(id,tenant,name,config,secret,schema_json) VALUES(?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,secret=excluded.secret,
              schema_json=excluded.schema_json,revision=data_sources.revision+1""",
                (source_id, actor.tenant_id, name, json.dumps(config), secret, json.dumps(schema)))
        self.audit(actor, "source_saved", source_id, {"tables": sorted(schema)})
        return source_id

    def delete_source(self, actor: Principal, source_id: str):
        self.source(actor, source_id)
        if actor.role != "admin" or source_id == "demo":
            raise AccessDenied("无权停用该数据源")
        with self.connect() as db:
            db.execute("UPDATE data_sources SET active=0,revision=revision+1 WHERE id=?", (source_id,))
        self.audit(actor, "source_disabled", source_id)

    def grant_source(self, actor: Principal, source_id: str, user_id: str, policy: dict):
        source = self.source(actor, source_id)
        user = self.user(user_id)
        if actor.role != "admin" or not user or user.tenant_id != actor.tenant_id or source_id == "demo":
            raise AccessDenied("无权授权该用户")
        tables = set(policy.get("tables", source["schema"]))
        if not tables or tables - set(source["schema"]):
            raise ValueError("授权表不在数据源白名单中")
        for rule in policy.get("row_filters", []):
            if rule["table"] not in tables or rule["column"] not in source["schema"][rule["table"]]:
                raise ValueError("行权限字段无效")
        for field in policy.get("sensitive_fields", []):
            table, _, column = field.partition(".")
            if table not in tables or column not in source["schema"][table]:
                raise ValueError("脱敏字段无效")
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO source_grants VALUES(?,?,?)", (source_id, user_id, json.dumps(policy)))
            db.execute("UPDATE data_sources SET revision=revision+1 WHERE id=?", (source_id,))
        self.audit(actor, "source_granted", source_id, {"user_id": user_id})

    def revoke_source(self, actor, source_id, user_id):
        self.source(actor, source_id)
        if actor.role != "admin" or source_id == "demo":
            raise AccessDenied("需要数据源管理员权限")
        with self.connect() as db:
            db.execute("DELETE FROM source_grants WHERE source_id=? AND user_id=?", (source_id,user_id))
            db.execute("UPDATE data_sources SET revision=revision+1 WHERE id=?", (source_id,))
        self.audit(actor,"source_revoked",source_id,{"user_id":user_id})
