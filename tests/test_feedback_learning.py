from fastapi.testclient import TestClient
import pytest

from chatbi.api.app import create_app
from chatbi.api.feedback import feedback_examples
from chatbi.infrastructure.control_store import principal_context
from chatbi.domain.security import QuerySecurityManager, UserContext
from tests.test_api import FakeService, FakeDatabase
from tests.test_product_access import product_settings, bootstrap


def test_independent_expected_result_review_separation_and_revision_expiry(tmp_path, monkeypatch):
    class ResultDB(FakeDatabase):
        def execute(self, query):
            return ["订单数"], [(7,)]
        def close(self):
            pass
    def service(*args, **kwargs):
        fake = FakeService(ResultDB())
        fake.security = QuerySecurityManager()
        fake.user_context = UserContext.demo_admin()
        return fake
    monkeypatch.setattr("chatbi.api.app.build_query_service", service)
    app = create_app(product_settings(tmp_path), service_factory=lambda _: FakeService(FakeDatabase()))
    with TestClient(app) as client:
        bootstrap(client)
        client.post("/api/v1/users", json={"username":"reviewer", "password":"review-password1", "role":"reviewer"})
        client.post("/api/v1/queries",json={"question":"订单数"})
        query_id=client.get("/api/v1/history").json()["items"][0]["id"]
        payload={"query_id":query_id,"note":"核对完成订单口径", "sql":"SELECT COUNT(*) AS `订单数` FROM sales_orders",
                 "expected":{"columns":["订单数"],"rows":[[8]]}}
        assert client.post("/api/v1/feedback-cases",json=payload).status_code==422
        payload["expected"]["rows"]=[[7]]
        response=client.post("/api/v1/feedback-cases",json=payload)
        assert response.status_code==201,response.text
        identity=response.json()["id"]
        assert client.post(f"/api/v1/feedback-cases/{identity}/publish").status_code==409
        assert not client.get("/api/v1/feedback-cases/export").json()["cases"]
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login",json={"username":"reviewer","password":"review-password1"})
        assert client.post(f"/api/v1/feedback-cases/{identity}/publish").status_code==200
        cases=client.get("/api/v1/feedback-cases/export").json()["cases"]
        assert len(cases)==1 and cases[0]["expected_result"]["rows"]==[[7]]
        runtime=app.state.product
        actor=runtime.store.login("reviewer","review-password1",300)[1]
        token=principal_context.set(actor)
        try:
            assert feedback_examples(runtime,"订单数？")
            assert not feedback_examples(runtime,"总收入")
            with runtime.store.connect() as db:
                db.execute("UPDATE feedback_cases SET catalog_revision='expired' WHERE id=?",(identity,))
            assert not feedback_examples(runtime,"订单数")
        finally:
            principal_context.reset(token)
        assert client.get("/api/v1/feedback-cases").json()["items"][0]["status"]=="stale"
