"""Real read-only MySQL discovery and isolated source execution."""
import os

import pytest
from pydantic import SecretStr

from chatbi.api.app import build_query_service
from chatbi.application.product_runtime import ProductRuntime
from chatbi.infrastructure.control_store import principal_context, source_context
from chatbi.infrastructure.data_sources import discover_mysql, validate_allowlist
from chatbi.settings import Settings


@pytest.mark.integration
def test_real_mysql_source_discovery_scope_and_safe_execution(tmp_path):
    if os.getenv("CHATBI_P0_INTEGRATION") != "1":
        pytest.skip("requires seeded read-only MySQL")
    config = {"host": os.getenv("CHATBI_P0_DB_HOST", "127.0.0.1"), "port": int(os.getenv("CHATBI_P0_DB_PORT", "3306")),
              "user": os.getenv("CHATBI_P0_DB_USER", "chatbi_reader"), "database": "chatbi_mvp"}
    password = os.environ["CHATBI_P0_DB_PASSWORD"]
    schema = discover_mysql(config, password)
    assert len(schema) == 30
    selected = validate_allowlist({"sales_orders": ["order_id", "order_status"]}, schema)
    runtime = ProductRuntime(Settings(control_store_path=tmp_path/"control.db", metric_registry_path=tmp_path/"metrics.db"))
    actor = runtime.store.create_user("owner", "integration-password1", bootstrap=True)
    source = runtime.store.save_source(actor, name="只读接入", config=config, secret=runtime.vault.encrypt(password), schema=selected)
    a, s = principal_context.set(actor), source_context.set(source)
    try:
        settings, whitelist, user, db_config, _ = runtime.snapshot()
        settings = settings.model_copy(update={"openai_api_key": SecretStr("integration-placeholder"), "intent_api_key": SecretStr("integration-placeholder")})
        assert not runtime.catalog().snapshot()[1].indicators
        service = build_query_service(settings, schema=whitelist, user=user, db_config=db_config)
        try:
            sql = service.security.secure("SELECT COUNT(*) AS `订单数` FROM sales_orders WHERE order_status='completed'", user)
            columns, rows = service.database.execute(sql)
            assert columns == ["订单数"] and rows[0][0] > 0
            with pytest.raises(Exception):
                service.security.secure("SELECT net_amount FROM sales_orders", user)
            with pytest.raises(Exception):
                service.security.secure("SELECT * FROM customer_payments", user)
        finally:
            service.database.close()
    finally:
        source_context.reset(s)
        principal_context.reset(a)
