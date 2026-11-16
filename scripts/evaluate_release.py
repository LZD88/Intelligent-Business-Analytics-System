"""Evaluate live queries against versioned SQL, routing and dialogue cases."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from threading import Event
from time import monotonic, perf_counter
from typing import Any

from chatbi.api.app import build_query_service, refresh_query_catalog
from chatbi.application.analysis_context import effective_question
from chatbi.application.evaluation_config import config_fingerprint, evaluation_config
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.application.progress import QueryControl, QueryStopped, analysis_options, query_control
from chatbi.application.quality_evaluation import aggregate_quality, assess_complex
from chatbi.domain.errors import ChatBIError
from chatbi.infrastructure.metric_registry import MetricRegistry
from chatbi.infrastructure.runtime_version import source_version
from chatbi.settings import Settings
from evaluator import Evaluator

BENCHMARKS = (Path("test_cases.json"), Path("benchmarks/extended_cases.json"), Path("benchmarks/complex_cases.json"))


def benchmark_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in BENCHMARKS:
        digest.update(path.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def performance(records: list[dict]) -> dict:
    values = sorted(record["latency_ms"] for record in records)
    complete = all(row.get("token_usage", {}).get("calls", 0) == row.get("token_usage", {}).get("reported_calls", 0) for row in records)
    tokens = sum(row.get("token_usage", {}).get("prompt_tokens", 0) + row.get("token_usage", {}).get("completion_tokens", 0) for row in records)
    return {"samples": len(records), "mean_latency_ms": round(sum(values) / len(values), 2) if values else None,
            "p50_latency_ms": values[math.ceil(len(values) * .5) - 1] if values else None,
            "p95_latency_ms": values[math.ceil(len(values) * .95) - 1] if values else None,
            "tokens": tokens, "token_usage_complete": complete,
            "mean_tokens_per_question": round(tokens / len(records), 2) if records and complete else None}


def delta(after: dict, before: dict) -> dict:
    return {key: value - before.get(key, 0) for key, value in after.items()}


def provider_usage(service: Any) -> dict:
    primary = getattr(getattr(service, "intent_classifier", None), "primary", None)
    encoder = getattr(getattr(service, "simple_semantic", None), "encoder", None)
    return {name: dict(getattr(provider, "usage", {}))
            for name, provider in (("chat", service.llm), ("intent", primary), ("embedding", encoder))}


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def load_cases(profile: str) -> tuple[list[dict], list[dict], list[dict]]:
    original, extended, complex_document = [json.loads(path.read_text(encoding="utf-8")) for path in BENCHMARKS]
    return original["cases"] + (extended["cases"] if profile == "full" else []), complex_document["cases"], extended["behaviors"]


def evaluate(settings: Settings, *, profile: str = "full", output: Path | None = None, resume: bool = False) -> dict[str, Any]:
    version = source_version()
    revision, knowledge = MetricRegistry(settings.metric_registry_path).snapshot()
    configuration = evaluation_config(settings, revision)
    fingerprint, benchmark_hash = config_fingerprint(configuration), benchmark_fingerprint()
    simple_cases, complex_cases, behaviors = load_cases(profile)
    saved: dict = {}
    if resume and output and output.exists():
        saved = json.loads(output.read_text(encoding="utf-8"))
        identity = {"source_version": version, "config_fingerprint": fingerprint, "benchmark_fingerprint": benchmark_hash, "profile": profile}
        if any(saved.get(key) != value for key, value in identity.items()):
            raise ValueError("Cannot resume after source, model, catalog or benchmark changes")
    records = {section: list(saved.get(section, {}).get("results", [])) for section in ("simple", "complex", "behavior")}
    service = build_query_service(settings)
    refresh_query_catalog(service, knowledge)
    evaluator = Evaluator(service.database, service.security, model_config=configuration)

    def project(complete: bool) -> dict:
        simple = evaluator.build_report(records["simple"], benchmark_version="2.0.0" if profile == "full" else "1.0.1", database_snapshot="chatbi-mvp-seed-2026-11-09")
        complex_report = {"benchmark_version": "1.0.0", **aggregate_quality(records["complex"]), "results": records["complex"]}
        correct = sum(bool(row.get("execution_match")) for row in records["behavior"])
        behavioral = {"total": len(records["behavior"]), "correct": correct, "results": records["behavior"], "passed": bool(records["behavior"]) and correct == len(records["behavior"])}
        all_records = sum(records.values(), [])
        intent_verified = bool(all_records) and all(row.get("intent_model_verified", False) for row in all_records)
        changed = version != source_version() or benchmark_hash != benchmark_fingerprint()
        current_revision, _ = MetricRegistry(settings.metric_registry_path).snapshot()
        config_changed = fingerprint != config_fingerprint(evaluation_config(settings, current_revision))
        role_totals: dict = {}
        for row in all_records:
            for role, counts in row.get("usage_by_role", {}).items():
                target = role_totals.setdefault(role, {key: 0 for key in counts})
                for key, value in counts.items():
                    target[key] += value
        provider_totals = {}
        for name in ("chat", "intent", "embedding"):
            counts = {key: sum(row.get("provider_usage", {}).get(name, {}).get(key, 0) for row in all_records)
                      for key in ("calls", "reported_calls", "prompt_tokens", "completion_tokens")}
            available = all(row.get("provider_usage", {}).get(name) for row in all_records)
            complete_usage = bool(available and counts["calls"] == counts["reported_calls"])
            provider_totals[name] = {**counts, "complete": complete_usage,
                                     "mean_tokens_per_question": (counts["prompt_tokens"] + counts["completion_tokens"]) / len(all_records)
                                     if complete_usage and all_records else None}
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "live_model", "profile": profile,
                "status": "completed" if complete else "running", "source_version": version,
                "source_changed_during_evaluation": changed, "config_changed_during_evaluation": config_changed,
                "catalog_revision": revision, "configuration": configuration, "config_fingerprint": fingerprint,
                "benchmark_fingerprint": benchmark_hash, "simple": simple, "complex": complex_report, "behavior": behavioral,
                "intent_model_verified": intent_verified,
                "progress": {"completed": len(all_records), "total": len(simple_cases) + len(complex_cases) + len(behaviors)},
                "performance": {section: performance(rows) for section, rows in records.items()},
                "token_usage": provider_totals,
                "usage_by_role": role_totals,
                "passed": complete and simple["passed_mvp_threshold"] and complex_report["passed"] and behavioral["passed"] and intent_verified and not changed and not config_changed}

    try:
        # Exercise expensive planning/report contracts early; still run every case.
        for section, cases in (("complex", complex_cases), ("behavior", behaviors), ("simple", simple_cases)):
            finished = {row["id"] for row in records[section]}
            for case in cases:
                if case["id"] in finished:
                    continue
                started = perf_counter()
                before, role_before = dict(service.llm.usage), deepcopy(service.llm.usage_by_role)
                provider_before = provider_usage(service)
                record: dict = {"id": case["id"], "question": case["question"], "domain": case.get("domain", case.get("category", section))}
                context = {"turns": [{"question": text} for text in [*case.get("context", []), case["question"]]]}
                question = effective_question(case["question"], context)
                try:
                    with query_control(QueryControl(Event(), monotonic() + settings.query_total_timeout_seconds, lambda *args: None)):
                        with analysis_options({"analysis_context": context, "clarify_before_query": True}):
                            result = service.query(question)
                    if section == "complex":
                        record.update(assess_complex(result, case))
                        record.update(plan=result.plan, report=result.analysis_report, step_results=list(result.step_results))
                    if case.get("kind") == "non_query":
                        record["execution_match"] = result.query_type == "non_query" and not result.sql and not result.step_results
                    elif case.get("kind") == "clarification":
                        record.update(execution_match=False, error_code="clarification_missing")
                    else:
                        expected_columns, expected_rows = service.database.execute(service.security.secure(case["expected_sql"]))
                        record.update(execution_match=evaluator._results_equivalent(list(result.columns), [tuple(row) for row in result.rows], expected_columns, expected_rows),
                                      expected_row_count=len(expected_rows), row_count=result.row_count, generated_sql=result.sql,
                                      expected_sql=case["expected_sql"], query_type=result.query_type, validation=result.validation)
                    record["intent_source"] = result.intent_source
                    record["exact_match"] = bool(case.get("expected_sql") and evaluator._normalize_sql(result.sql) == evaluator._normalize_sql(case["expected_sql"]))
                    record["timings_ms"] = result.timings_ms
                    record.setdefault("execution_failed", False)
                    if not record["execution_match"]:
                        record.setdefault("failure_class", "result_mismatch")
                except AnalysisSuspended as exc:
                    ids = {item["id"] for item in exc.public.get("questions", [])}
                    correct = case.get("kind") == "clarification" and set(case.get("question_ids", [])) <= ids
                    record.update(execution_match=correct, execution_failed=not correct, exact_match=False,
                                  error_code=None if correct else "analysis_suspended", clarification_ids=sorted(ids),
                                  intent_source=exc.details.get("intent_source", ""))
                    if not correct:
                        record["suspension"] = exc.public
                except (ChatBIError, QueryStopped) as exc:
                    record.update(execution_match=False, execution_failed=True, exact_match=False,
                                  error_code=getattr(exc.code, "value", exc.code),
                                  intent_source=getattr(exc, "details", {}).get("intent_source", ""))
                except Exception as exc:
                    record.update(execution_match=False, execution_failed=True, exact_match=False, error_code="evaluation_failed", error_type=type(exc).__name__)
                record.setdefault("failure_class", None if record["execution_match"] else record.get("error_code", "result_mismatch"))
                classifier = getattr(service, "intent_classifier", None)
                record["intent_model_verified"] = bool(not settings.feature_intent_router or case.get("kind") == "non_query"
                    or (classifier is not None and getattr(classifier, "last_error", None) is None
                        and record.get("intent_source", "").startswith("qwen")))
                if not record["intent_model_verified"]:
                    record["routing_error"] = "intent_model_unavailable_or_fallback"
                record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
                record["token_usage"] = delta(service.llm.usage, before)
                record["provider_usage"] = {name: delta(counts, provider_before[name]) for name, counts in provider_usage(service).items()}
                record["usage_by_role"] = {role: delta(counts, role_before.get(role, {})) for role, counts in service.llm.usage_by_role.items()}
                records[section].append(record)
                report = project(False)
                if output:
                    save_report(output, report)
                print(json.dumps({"case": case["id"], "correct": record["execution_match"], "ms": record["latency_ms"], **report["progress"]}), flush=True)
        report = project(True)
        if output:
            save_report(output, report)
        return report
    finally:
        service.database.close()


def compare(settings: Settings, models: list[str], output: Path, resume: bool = False) -> dict:
    reports = []
    for model in models:
        updates = {f"llm_{role}_model": None for role in ("sql", "planner", "executor", "validator", "summary")}
        candidate = settings.model_copy(update={**updates, "llm_model": model, "llm_thinking": "disabled"})
        name = hashlib.sha256(model.encode()).hexdigest()[:8]
        report = evaluate(candidate, profile="smoke", output=output.with_name(f"{output.stem}-{name}.json"), resume=resume)
        reports.append({"model": model, "passed": report["passed"], "config_fingerprint": report["config_fingerprint"],
                        "simple_accuracy": report["simple"]["metrics"]["execution_accuracy"], "complex_accuracy": report["complex"]["metrics"]["result_accuracy"],
                        "behavior_correct": report["behavior"]["correct"], "performance": report["performance"]})
        save_report(output, {"status": "running", "candidates": reports})
    eligible = [row for row in reports if row["passed"]]
    quality = max(((row["simple_accuracy"], row["complex_accuracy"]) for row in eligible), default=None)
    peers = [row for row in eligible if (row["simple_accuracy"], row["complex_accuracy"]) == quality]
    selected = min(peers, key=lambda row: row["performance"]["simple"]["p95_latency_ms"]) if peers else None
    result = {"status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(), "candidates": reports,
              "recommended_model": selected["model"] if selected else None,
              "note": "候选均关闭推理；先比较正确率，通过门槛后按简单查询P95排序。小样本选型后仍须完整评测。"}
    save_report(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/release-quality.json"))
    parser.add_argument("--profile", choices=("smoke", "full"), default="full")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--compare-models", nargs="+")
    args = parser.parse_args()
    try:
        report = compare(Settings(), args.compare_models, args.output, args.resume) if args.compare_models else evaluate(Settings(), profile=args.profile, output=args.output, resume=args.resume)
    except Exception as exc:
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "live_model", "source_version": source_version(),
                  "passed": False, "status": "preflight_failed", "error_type": type(exc).__name__,
                  "message": "验收未完成，请检查配置与依赖。原有逐题检查点保留，可使用 --resume 继续。"}
        save_report(args.output.with_name(args.output.stem + "-preflight.json"), report)
    print(json.dumps({key: report.get(key) for key in ("status", "passed", "recommended_model", "progress")}), flush=True)
    raise SystemExit(0 if report.get("passed") or report.get("recommended_model") else 1)
