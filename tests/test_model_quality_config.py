from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
from pydantic import SecretStr

import pytest

from chatbi.application.evaluation_config import config_fingerprint, evaluation_config
from chatbi.application.observability import evaluation_dashboard
from chatbi.application.analysis_context import ambiguity_questions
from chatbi.domain.security import QuerySecurityManager
from chatbi.settings import Settings
from evaluator import Evaluator
from llm_client import LLMClient
from scripts.evaluate_release import load_cases, performance


def test_fingerprint_tracks_effective_roles_and_parameters_without_credentials():
    settings = Settings(_env_file=None, openai_base_url="https://user:private@example.com/v1/?token=hidden",
                        openai_api_key="private-key", db_password="private-password", llm_model="base")
    baseline = evaluation_config(settings, "revision1")
    assert baseline["chat_endpoint"] == "https://example.com/v1"
    assert "private" not in json.dumps(baseline) and "hidden" not in json.dumps(baseline)
    for updates in ({"llm_validator_model": "reviewer"}, {"llm_thinking": "disabled"}, {"llm_temperature": .2},
                    {"llm_max_tokens": 4000}, {"openai_base_url": "https://other.example/v1"}):
        assert config_fingerprint(evaluation_config(settings.model_copy(update=updates), "revision1")) != config_fingerprint(baseline)
    assert config_fingerprint(evaluation_config(settings, "revision2")) != config_fingerprint(baseline)
    assert config_fingerprint(evaluation_config(settings.model_copy(update={"openai_api_key": SecretStr("different")}), "revision1")) == config_fingerprint(baseline)
    assert settings.model_for("planner") == "base"
    with pytest.raises(ValueError):
        settings.model_for("unknown")


def test_dashboard_invalidates_same_source_after_model_or_catalog_change(tmp_path):
    path = tmp_path / "report.json"
    config = evaluation_config(Settings(_env_file=None), "v1")
    path.write_text(json.dumps({"source_version": "source1", "config_fingerprint": config_fingerprint(config), "passed": True}), encoding="utf-8")
    assert not evaluation_dashboard(path, "source1", config)["stale"]
    changed = deepcopy(config)
    changed["models"]["sql"] = "new-model"
    assert evaluation_dashboard(path, "source1", changed)["stale"]
    path.write_text('{"source_version":"source1","passed":true}', encoding="utf-8")
    assert evaluation_dashboard(path, "source1", config)["stale"]


def test_role_clients_share_totals_but_send_separate_models_and_opt_in_thinking():
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    settings = Settings(_env_file=None, llm_model="fallback", llm_sql_model="fast", llm_validator_model="careful", llm_thinking="disabled")
    sql = LLMClient(settings, client=client)
    reviewer = LLMClient(settings, client=client, role="validator", usage=sql.usage, usage_by_role=sql.usage_by_role)
    sql.generate_sql("", "")
    reviewer.generate_text("", "")
    assert [request["model"] for request in requests] == ["fast", "careful"]
    assert all(request["extra_body"] == {"thinking": {"type": "disabled"}} for request in requests)
    assert sql.usage["calls"] == 2 and sql.usage["prompt_tokens"] == 20
    assert sql.usage_by_role["sql"]["calls"] == sql.usage_by_role["validator"]["calls"] == 1
    assert LLMClient(Settings(_env_file=None), client=client).request_options() == {}


def test_extended_cases_are_unique_and_safe_and_cover_behavior_and_empty_results():
    simple, complex_cases, behavior = load_cases("full")
    assert len(simple) + len(complex_cases) + len(behavior) >= 100
    assert len({case["question"] for case in simple}) == len(simple)
    assert len({case["expected_sql"] for case in simple}) >= 90
    assert {case["kind"] for case in behavior} == {"non_query", "clarification", "followup"}
    assert {"empty_result", "zero_result"} <= {case["category"] for case in simple}
    security = QuerySecurityManager()
    for case in simple + complex_cases + [row for row in behavior if "expected_sql" in row]:
        security.secure(case["expected_sql"])


def test_evaluation_numeric_equivalence_tolerates_driver_types_not_wrong_scale():
    evaluator = Evaluator(db_client=object())
    assert evaluator._results_equivalent(["x"], [(1,)], ["y"], [(Decimal("1.0000"),)])
    assert evaluator._results_equivalent(["x"], [(Decimal("0.3333333333"),)], ["y"], [(1 / 3,)])
    assert not evaluator._results_equivalent(["x"], [(75,)], ["y"], [(Decimal(".75"),)])
    assert not evaluator._results_equivalent(["x"], [(None,)], ["y"], [(0,)])


def test_latency_percentiles_and_incomplete_token_usage_are_explicit():
    records = [{"latency_ms": value, "token_usage": {"calls": 1, "reported_calls": 1, "completion_tokens": 10}}
               for value in range(1, 21)]
    stats = performance(records)
    assert stats["p50_latency_ms"] == 10 and stats["p95_latency_ms"] == 19
    records[0]["token_usage"]["reported_calls"] = 0
    assert performance(records)["mean_tokens_per_question"] is None


@pytest.mark.parametrize("predicate", ["s.country IS NOT NULL", "NOT s.country IS NULL", "NOT (s.country IS NULL)"])
def test_review_makes_null_predicate_precedence_explicit_without_weakening_rejection(predicate):
    import sqlglot
    from sqlglot.optimizer.simplify import simplify
    from chatbi.infrastructure.agents.validator import ResultValidationAgent
    captured = []
    class Reviewer:
        def generate_text(self, system, prompt):
            captured.append(json.loads(prompt))
            return '{"issues":["缺少 active 过滤"],"checks":{}}'
    sql = f"SELECT COUNT(*) FROM dim_suppliers s WHERE {predicate} AND s.country <> ''"
    review = ResultValidationAgent(Reviewer()).validate(question="活跃供应商数", sql=sql,
        columns=["count"], rows=[[4]], knowledge="必须限定 active")
    displayed = captured[0]["sql"]
    assert "NOT (s.country IS NULL)" in displayed
    assert simplify(sqlglot.parse_one(displayed, read="mysql")) == simplify(sqlglot.parse_one(sql, read="mysql"))
    assert review["status"] == "rejected" and review["issues"] == ["缺少 active 过滤"]


@pytest.mark.parametrize("intent_source", ["qwen", "fallback"])
def test_evaluation_does_not_mislabel_downstream_failure_as_intent_fallback(tmp_path, monkeypatch, intent_source):
    from chatbi.domain.errors import ChatBIError, ErrorCode
    from scripts import evaluate_release as runner
    class Service:
        database = SimpleNamespace(close=lambda: None)
        security = QuerySecurityManager()
        llm = SimpleNamespace(usage=LLMClient.empty_usage(), usage_by_role={})
        intent_classifier = SimpleNamespace(last_error=None)
        def query(self, question):
            raise ChatBIError(ErrorCode.MODEL_OUTPUT_INVALID, details={"intent_source": intent_source})
    monkeypatch.setattr(runner, "build_query_service", lambda _: Service())
    monkeypatch.setattr(runner, "load_cases", lambda _: ([{"id": "failed", "question": "活跃供应商数"}], [], []))
    report = runner.evaluate(Settings(_env_file=None, metric_registry_path=tmp_path / "metrics.sqlite3"))
    record = report["simple"]["results"][0]
    assert not record["execution_match"] and record["error_code"] == "model_output_invalid"
    assert record["intent_model_verified"] == (intent_source == "qwen")
    assert ("routing_error" in record) == (intent_source == "fallback")
    assert not report["passed"]


def test_planner_repairs_previous_invalid_draft_without_dropping_bindings():
    from chatbi.infrastructure.agents.planner import PlannerAgent
    from indicator_knowledge import IndicatorKnowledge
    from tests.test_complex_agents import plan, step
    valid = plan(step(), step("step_2", depends_on=["step_1"])).model_dump()
    valid["steps"][1]["input_bindings"] = [{"source_step_id": "step_1", "name": "input_1", "columns": ["收入"]}]
    invalid = deepcopy(valid)
    invalid["steps"][0]["step_id_note"] = "保留前序汇总"
    captured = []
    class Model:
        def generate_text(self, system, prompt):
            captured.append(prompt)
            return json.dumps(invalid if len(captured) == 1 else valid, ensure_ascii=False)
    result = PlannerAgent(Model(), Settings(_env_file=None, complex_plan_repair_attempts=1), IndicatorKnowledge()).plan(
        "先汇总收入，再从上游结果筛选", "", "sales_orders", [])
    assert result.source == "repaired" and len(result.steps) == 2
    assert result.steps[1].input_bindings[0].source_step_id == "step_1"
    assert json.dumps({"previous_output": json.dumps(invalid, ensure_ascii=False)}, ensure_ascii=False) in captured[1]
    assert "extra_forbidden" in captured[1]


def test_planner_rejects_single_step_when_user_explicitly_requires_upstream_result():
    from chatbi.domain.errors import ChatBIError
    from chatbi.infrastructure.agents.planner import PlannerAgent
    from indicator_knowledge import IndicatorKnowledge
    from tests.test_complex_agents import plan, QueueLLM
    agent = PlannerAgent(QueueLLM(texts=[plan().model_dump_json()]),
        Settings(_env_file=None, complex_plan_repair_attempts=0), IndicatorKnowledge())
    with pytest.raises(ChatBIError):
        agent.plan("从上游结果筛选", "", "sales_orders", [])


@pytest.mark.parametrize("question", ["最近三个月的收入", "最近两周订单数", "最近30天收入"])
def test_explicit_relative_period_does_not_require_clarification(question):
    assert not ambiguity_questions(question)


def test_resume_reuses_completed_cases_and_rejects_model_change(tmp_path, monkeypatch):
    from chatbi.domain.models import QueryResult
    from scripts import evaluate_release as runner
    calls = []
    class Database:
        def execute(self, query):
            return ["count"], [(1,)]
        def close(self):
            pass
    class Service:
        database = Database()
        security = QuerySecurityManager()
        llm = SimpleNamespace(usage=LLMClient.empty_usage(), usage_by_role={})
        def query(self, question):
            calls.append(question)
            return QueryResult(request_id="test", question=question, sql="SELECT COUNT(*) FROM sales_orders",
                               columns=("count",), rows=((1,),), row_count=1, truncated=False, schema_mode="static")
    monkeypatch.setattr(runner, "build_query_service", lambda _: Service())
    monkeypatch.setattr(runner, "load_cases", lambda _: ([{"id": "count", "question": "订单数", "expected_sql": "SELECT COUNT(*) FROM sales_orders"}], [], []))
    settings = Settings(_env_file=None, metric_registry_path=tmp_path / "metrics.sqlite3")
    output = tmp_path / "report.json"
    first = runner.evaluate(settings, output=output)
    assert first["progress"] == {"completed": 1, "total": 1}
    assert not first["intent_model_verified"]
    assert first["simple"]["results"][0]["routing_error"] == "intent_model_unavailable_or_fallback"
    assert not first["passed"]  # Missing complex/behavior coverage cannot pass.
    runner.evaluate(settings, output=output, resume=True)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="Cannot resume"):
        runner.evaluate(settings.model_copy(update={"llm_model": "changed"}), output=output, resume=True)


def test_observability_api_exposes_effective_models_without_keys(tmp_path):
    from fastapi.testclient import TestClient
    from chatbi.api.app import create_app
    from tests.test_api import FakeDatabase, FakeService
    settings = Settings(_env_file=None, app_env="test", metric_registry_path=tmp_path / "metrics.sqlite3",
                        openai_api_key="private-chat-key", intent_api_key="private-intent-key",
                        llm_sql_model="fast-sql", llm_validator_model="careful-review", llm_thinking="disabled")
    with TestClient(create_app(settings, service_factory=lambda _: FakeService(FakeDatabase()))) as client:
        response = client.get("/api/v1/observability")
        assert response.status_code == 200
        snapshot = response.json()["evaluation"]["current_configuration"]
        assert snapshot["models"]["sql"] == "fast-sql" and snapshot["models"]["validator"] == "careful-review"
        assert "private-chat-key" not in response.text and "private-intent-key" not in response.text


def test_summary_repairs_actual_binding_and_never_accepts_forged_target():
    from chatbi.domain.complex_analysis import StepExecutionResult, SummaryAgentOutput
    from chatbi.infrastructure.agents.summarizer import SummaryAgent
    from tests.test_complex_agents import plan, QueueLLM
    target = {"step_id": "step_1", "row_index": 0, "column": "产品线"}
    metric = {"step_id": "step_1", "row_index": 0, "column": "收入"}
    recommendation = {"target": "甲产品", "problem": "当前收入需复核", "action": "核对明细", "rationale": "依据当前收入",
                      "owner_role": "业务负责人", "priority": "medium", "expected_impact": "确认口径", "precondition": "核对期间",
                      "risk": "不构成因果证明", "confidence": "low", "target_evidence": target, "evidence": [metric]}
    draft = {"title": "收入报告", "executive_summary": "查询已完成", "findings": [], "caveats": [],
             "recommendation_status": "actionable", "recommendation_reason": "按数据复核", "recommendations": [recommendation]}
    fixed = deepcopy(draft)
    fixed["recommendations"][0]["evidence"].append(target)
    result = StepExecutionResult(step_id="step_1", step_name="收入", status="succeeded", columns=["产品线", "收入"],
                                 rows=[["甲产品", 100]], row_count=1, result="成功", result_reference="rows")
    prompts = []
    class Model(QueueLLM):
        def generate_text(self, system_msg, prompt):
            prompts.append(prompt)
            return super().generate_text(system_msg, prompt)
    model = Model(texts=[json.dumps(draft, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)])
    agent = SummaryAgent(model, Settings(_env_file=None))
    report = agent.summarize("复核收入", plan(), [result])
    assert report.source == "summary_agent" and report.recommendations[0].target == "甲产品"
    assert "untrusted-previous-draft" in prompts[1] and "step_id、row_index、column" in prompts[1]
    forged = deepcopy(fixed)
    forged["recommendations"][0]["target"] = "不存在的产品"
    with pytest.raises(ValueError, match="真实维度值不同"):
        agent._resolve(SummaryAgentOutput.model_validate(forged), [result])


def test_independent_golden_sql_covers_every_published_seed_metric():
    from indicator_knowledge import IndicatorKnowledge
    simple, _, _ = load_cases("full")
    covered = {case.get("metric") for case in simple}
    assert set(IndicatorKnowledge().indicators) <= covered
