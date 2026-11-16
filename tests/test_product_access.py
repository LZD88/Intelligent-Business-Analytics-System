from dataclasses import asdict
import json

from fastapi.testclient import TestClient
import pytest

from chatbi.api.app import create_app
from chatbi.infrastructure.control_store import AccessDenied, ControlStore
from chatbi.infrastructure.data_sources import CredentialVault, validate_allowlist
from chatbi.infrastructure.metric_registry import MetricRegistry
from tests.test_api import FakeService, FakeDatabase, settings


def product_settings(tmp_path):
    return settings(metric_registry_path=tmp_path / "metrics.db").model_copy(update={"auth_enabled": True, "control_store_path": tmp_path / "control.db"})


def bootstrap(client):
    credentials = {"username": "owner", "password": "test-password-12345"}
    assert client.post("/api/v1/auth/setup", json=credentials).status_code == 201
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    return credentials


def test_sessions_revocation_reactivation_roles_and_tenants(tmp_path):
    store = ControlStore(tmp_path / "access.db")
    admin = store.create_user("admin", "administrator123", bootstrap=True)
    analyst = store.create_user("analyst", "analyst-password", actor=admin)
    foreign = store.create_user("other", "other-password1", tenant="other", role="admin", actor=admin)
    token, _ = store.login("analyst", "analyst-password", 300)
    assert store.authenticate(token) == analyst
    with pytest.raises(AccessDenied):
        store.create_user("forged", "forged-password1", actor=analyst, role="admin")
    with pytest.raises(AccessDenied):
        store.source(foreign, "demo")
    with pytest.raises(AccessDenied):
        store.update_user(admin, foreign.user_id, role="analyst", active=False)
    store.update_user(admin, analyst.user_id, role="reviewer", active=False)
    assert store.authenticate(token) is None
    store.update_user(admin, analyst.user_id, role="reviewer", active=True)
    assert store.login("analyst", "analyst-password", 300)[1].role == "reviewer"
    with store.connect() as db:
        assert "analyst-password" not in db.execute("SELECT password FROM users WHERE id=?", (analyst.user_id,)).fetchone()[0]


def test_unauthenticated_cross_origin_and_cross_user_history_denied(tmp_path):
    app = create_app(product_settings(tmp_path), service_factory=lambda _: FakeService(FakeDatabase()))
    with TestClient(app) as client:
        assert client.get("/api/v1/history").status_code == 401
        bootstrap(client)
        assert client.post("/api/v1/auth/setup", json={"username": "x", "password": "x"*12}).status_code == 403
        assert client.post("/api/v1/query-jobs", json={"question": "总销售额"}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert client.post("/api/v1/users", json={"username": "analyst", "password": "analyst-password"}).status_code == 201
        assert client.post("/api/v1/queries", json={"question": "总销售额"}).status_code == 200
        query_id = client.get("/api/v1/history").json()["items"][0]["id"]
        client.post("/api/v1/auth/logout")
        assert client.get(f"/api/v1/history/{query_id}").status_code == 401
        client.post("/api/v1/auth/login", json={"username": "analyst", "password": "analyst-password"})
        assert not client.get("/api/v1/history").json()["items"]
        for suffix in ("", "/events"):
            url = f"/api/v1/history/{query_id}" if not suffix else f"/api/v1/query-jobs/{query_id}{suffix}"
            assert client.get(url).status_code == 404
        assert client.post(f"/api/v1/query-jobs/{query_id}/cancel").status_code == 404
        assert client.post("/api/v1/query-jobs", json={"question": "只看华东", "parent_query_id": query_id}).status_code == 404
        assert client.get("/api/v1/users").status_code == 403
        assert client.delete("/api/v1/history").json()["deleted"] == 0
        assert app.state.history.get(query_id)


def test_source_cipher_allowlist_and_isolation(tmp_path):
    store = ControlStore(tmp_path / "access.db")
    admin = store.create_user("admin", "administrator123", bootstrap=True)
    user = store.create_user("analyst", "analyst-password", actor=admin)
    vault = CredentialVault(tmp_path / "vault.key")
    encrypted = vault.encrypt("database-secret")
    assert "database-secret" not in encrypted
    assert CredentialVault(tmp_path / "vault.key").decrypt(encrypted) == "database-secret"
    identity = store.save_source(admin, name="客户库", config={"host": "localhost"}, secret=encrypted, schema={"orders": ["id", "region"]})
    with pytest.raises(AccessDenied):
        store.source(user, identity)
    store.grant_source(admin, identity, user.user_id, {"tables": ["orders"], "row_filters": [{"table": "orders", "column": "region", "value": "华东"}]})
    source = store.source(user, identity)
    assert source["policy"]["row_filters"][0]["value"] == "华东"
    assert "secret" not in json.dumps(store.list_sources(user))
    with pytest.raises(ValueError):
        validate_allowlist({"orders": ["password"]}, source["schema"])
    assert not MetricRegistry(tmp_path / "empty.db", seed=False).snapshot()[1].indicators


def test_source_api_requires_readonly_validation_and_never_returns_secret(tmp_path, monkeypatch):
    monkeypatch.setattr("chatbi.api.control.discover_mysql", lambda *_: {"orders": ["id", "amount"]})
    app = create_app(product_settings(tmp_path), service_factory=lambda _: FakeService(FakeDatabase()))
    with TestClient(app) as client:
        bootstrap(client)
        data = {"name": "真实业务", "host": "localhost", "database": "biz", "user": "reader", "password": "secret123", "tables": {"orders": ["id"]}}
        response = client.post("/api/v1/sources", json=data)
        assert response.status_code == 201, response.text
        assert "secret123" not in response.text and "secret" not in response.json()
        source_id = response.json()["id"]
        assert client.get("/api/v1/metrics", headers={"X-Data-Source": source_id}).json()["active_count"] == 0
        assert client.get("/api/v1/history", headers={"X-Data-Source": "unknown"}).status_code == 403
        assert client.delete(f"/api/v1/sources/{source_id}").status_code == 200
        assert client.get("/api/v1/metrics", headers={"X-Data-Source": source_id}).status_code == 403
