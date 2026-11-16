"""Reviewed, revision-bound corrections; never train on unreviewed feedback."""
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from chatbi.infrastructure.control_store import AccessDenied, principal_context, source_context


class ExpectedResult(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=100)
    rows: list[list] = Field(max_length=200)


class Correction(BaseModel):
    query_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    note: str = Field(min_length=1, max_length=1000)
    sql: str = Field(min_length=1, max_length=8000)
    expected: ExpectedResult


def install_feedback(app, runtime, service_builder):
    store = runtime.store

    def cases(published=False):
        actor = principal_context.get()
        revision = runtime.catalog().snapshot()[0]
        with store.connect() as db:
            rows = db.execute("SELECT * FROM feedback_cases WHERE tenant=? AND source_id=? ORDER BY created DESC LIMIT 500",
                              (actor.tenant_id, source_context.get())).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if published and (item["status"] != "published" or item["catalog_revision"] != revision):
                continue
            if not published and actor.role == "analyst" and item["author"] != actor.user_id:
                continue
            if item["catalog_revision"] != revision:
                item["status"] = "stale"
            item["expected"] = json.loads(item["expected"])
            result.append(item)
        return result

    def verify(sql, expected):
        service = service_builder()
        try:
            secured = service.security.secure(sql, service.user_context)
            columns, rows = service.database.execute(secured)
            columns, rows = service.security.mask_result(columns, rows, service.user_context)
            if secured.limited and len(rows) >= secured.max_rows:
                raise ValueError("结果达到上限，请缩小标准查询范围")
            actual = json.loads(json.dumps({"columns": list(columns), "rows": [list(row) for row in rows]}, default=str))
            # Normalize numeric JSON strings without treating arbitrary text as numbers.
            from decimal import Decimal, InvalidOperation
            def normalized(value):
                if isinstance(value, list):
                    return [normalized(v) for v in value]
                if isinstance(value, dict):
                    return {k: normalized(v) for k, v in value.items()}
                if isinstance(value, (str, float, int)) and not isinstance(value, bool):
                    try:
                        return Decimal(str(value))
                    except InvalidOperation:
                        pass
                return value
            if normalized(actual) != normalized(expected):
                raise ValueError("标准 SQL 结果与独立预期不一致，禁止发布")
            return secured.sql
        finally:
            service.database.close()

    @app.get("/api/v1/feedback-cases")
    def list_cases():
        return {"items": cases()}

    @app.get("/api/v1/feedback-cases/export")
    def export():
        if principal_context.get().role not in {"admin", "reviewer"}:
            raise AccessDenied("需要审核员权限")
        return {"schema_version": 1, "version": "reviewed-feedback-1", "database_snapshot": "reviewed-at-publication", "source_id": source_context.get(), "catalog_revision": runtime.catalog().snapshot()[0],
                "cases": [{"id": item["id"], "question": item["question"], "expected_sql": item["sql"],
                           "expected_result": item["expected"], "category": "reviewed_feedback"} for item in cases(True)]}

    @app.post("/api/v1/feedback-cases", status_code=201)
    def create(payload: Correction):
        actor = principal_context.get()
        item = app.state.history.get(payload.query_id)
        if not item or not runtime.owns(item):
            raise HTTPException(404, "个人查询记录不存在")
        revision = runtime.catalog().snapshot()[0]
        expected = payload.expected.model_dump()
        if any(len(row) != len(expected["columns"]) for row in expected["rows"]):
            raise HTTPException(422, "预期结果列数不一致")
        try:
            sql = verify(payload.sql, expected)
        except Exception:
            raise HTTPException(422, "标准查询验证失败；请核对只读 SQL、权限和独立预期结果") from None
        identity = uuid4().hex
        with store.connect() as db:
            db.execute("""INSERT INTO feedback_cases(id,tenant,source_id,query_id,question,note,sql,expected,
                catalog_revision,status,author,created,updated) VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?,?)""",
                (identity, actor.tenant_id, source_context.get(), payload.query_id, item["question"], payload.note,
                 sql, json.dumps(expected, ensure_ascii=False), revision, actor.user_id, time.time(), time.time()))
        store.audit(actor, "feedback_drafted", identity)
        return {"id": identity, "status": "draft"}

    @app.post("/api/v1/feedback-cases/{case_id}/publish")
    def publish(case_id: str):
        actor = principal_context.get()
        if actor.role not in {"admin", "reviewer"}:
            raise AccessDenied("需要审核员权限")
        item = next((item for item in cases() if item["id"] == case_id), None)
        if not item:
            raise HTTPException(404, "案例不存在")
        if item["author"] == actor.user_id:
            raise HTTPException(409, "需由另一位审核员复核，不能自行发布")
        revision = runtime.catalog().snapshot()[0]
        if item["status"] != "draft" or item["catalog_revision"] != revision:
            raise HTTPException(409, "案例已发布或口径已变化，请重新修正")
        try:
            verify(item["sql"], item["expected"])
        except Exception:
            raise HTTPException(409, "当前数据库结果或权限已变化，需重新校验") from None
        with store.connect() as db:
            changed = db.execute("UPDATE feedback_cases SET status='published',reviewer=?,updated=? WHERE id=? AND status='draft'",
                       (actor.user_id, time.time(), case_id)).rowcount
        if changed != 1:
            raise HTTPException(409, "案例状态已变化")
        store.audit(actor, "feedback_published", case_id)
        return {"status": "published"}


def feedback_examples(runtime, question):
    """Exact normalized matches are intentionally conservative, bounded and tenant scoped."""
    import re
    normalize = lambda value: re.sub(r"[\s，。？！?！]", "", value).lower()
    actor = principal_context.get()
    revision = runtime.catalog().snapshot()[0]
    with runtime.store.connect() as db:
        rows = db.execute("""SELECT question,sql FROM feedback_cases WHERE tenant=? AND source_id=?
            AND status='published' AND catalog_revision=? ORDER BY updated DESC LIMIT 500""",
            (actor.tenant_id, source_context.get(), revision)).fetchall()
    return [dict(row) for row in rows if normalize(row["question"]) == normalize(question)][:3]
