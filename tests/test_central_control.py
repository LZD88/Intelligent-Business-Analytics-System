import os
from pathlib import Path
from uuid import uuid4

import pytest

from chatbi.infrastructure.control_store import ControlStore
from chatbi.infrastructure.metric_registry import MetricRegistry, MetricDraft, CatalogDecision
from chatbi.infrastructure.query_history import QueryHistory
from chatbi.application.query_jobs import QueryJobs
from tests.test_query_jobs import wait_until


@pytest.mark.integration
def test_postgresql_shared_identities_catalog_and_leased_queue(tmp_path, monkeypatch):
    dsn=os.getenv("CHATBI_CONTROL_PG_URL")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL control database")
    monkeypatch.setenv("CONTROL_DATABASE_URL",dsn)
    suffix=uuid4().hex
    path=tmp_path/f"control-{suffix}.db"
    store=ControlStore(path)
    admin=store.create_user("admin","administrator123",bootstrap=True)
    token,actor=store.login("admin","administrator123",300)
    # Different host directory, same stable store name -> same central schema.
    assert ControlStore(tmp_path/"other-host"/path.name).authenticate(token)==admin
    history=QueryHistory(tmp_path/f"history-{suffix}.db")
    calls=[]
    def execute(question,identity):
        calls.append(identity)
        return {"question":question}
    one=QueryJobs(history,execute,shared=True,workers=2,lease_seconds=5)
    two=QueryJobs(QueryHistory(tmp_path/"other-host"/history.path.name),execute,shared=True,workers=2,lease_seconds=5)
    try:
        identity=one.submit("共享查询",{"owner":admin.user_id,"tenant":"default","source_id":"demo"})
        wait_until(lambda:history.get(identity)["status"]=="succeeded")
        assert calls==[identity]
        assert len(history.list_history(scope={"owner":admin.user_id,"tenant":"default","source_id":"demo"}))==1
    finally:
        one.close();two.close()
    catalog=MetricRegistry(tmp_path/f"catalog-{suffix}.db",seed=False)
    draft=MetricDraft(name="测试订单数",definition="订单计数",formula="COUNT(*)",data_source="sales_orders",time_field="order_date",owner=admin.user_id,change_note="接入")
    created=catalog.create_draft(draft)
    for action in ("submit","publish"):
        catalog.transition(created["id"],1,action,CatalogDecision(actor=admin.user_id,note="验证"))
    assert MetricRegistry(tmp_path/"other-host"/catalog.path.name,seed=False).snapshot()[1].indicators["测试订单数"]
