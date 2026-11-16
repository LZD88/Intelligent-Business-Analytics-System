"""Honest dashboard projections of recorded evaluations and runtime queries."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any
from chatbi.application.evaluation_config import config_fingerprint


def evaluation_dashboard(path: Path, current_version: str, current_config: dict | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False, "message": "尚无真实模型评测报告，请运行 scripts/chatbi.ps1 evaluate。", "domains": [],
                "current_configuration": current_config}
    report = json.loads(path.read_text(encoding="utf-8"))
    records = report.get("simple", {}).get("results", []) + report.get("complex", {}).get("results", []) + report.get("behavior", {}).get("results", [])
    reasons = []
    if report.get("source_version") != current_version:
        reasons.append("执行代码已更新")
    if current_config is not None:
        if not report.get("config_fingerprint"):
            reasons.append("旧报告未记录模型配置，需重新验收")
        elif report["config_fingerprint"] != config_fingerprint(current_config):
            reasons.append("接口、模型参数、执行策略或指标口径已变化")
    if report.get("status", "completed") != "completed":
        reasons.append("评测尚未完成")
    groups: dict[str, list] = {}
    failures: Counter[str] = Counter()
    for record in records:
        domain = record.get("domain") or {"revenue": "销售收入", "cost": "销售成本", "gross_profit": "盈利分析", "expenses": "期间费用",
            "profit": "盈利分析"}.get(record.get("category")) or {"low-margin-product-cost": "盈利分析", "over-budget-centers": "预算分析",
                                           "maintenance-cost-ranking": "设备维护"}.get(record.get("id"), "未分类")
        groups.setdefault(domain, []).append(record)
        if not record.get("execution_match"):
            failures[record.get("error_code") or record.get("failure_class") or "result_mismatch"] += 1
        if record.get("routing_error"):
            failures[record["routing_error"]] += 1
    domains = [{"name": name, "samples": len(items), "correct": sum(bool(item.get("execution_match")) for item in items),
                "accuracy": sum(bool(item.get("execution_match")) for item in items) / len(items),
                "mean_latency_ms": sum(float(item.get("latency_ms", item.get("duration_ms", 0))) for item in items) / len(items)} for name, items in groups.items()]
    return {"available": True, "generated_at": report.get("generated_at"), "passed": report.get("passed"),
            "source_version": report.get("source_version"), "current_version": current_version,
            "stale": bool(reasons), "stale_reasons": reasons, "domains": domains,
            "current_configuration": current_config, "configuration": report.get("configuration"),
            "progress": report.get("progress"), "performance": report.get("performance", {}),
            "profile": report.get("profile", "legacy"),
            "behavior_metrics": {key: value for key, value in report.get("behavior", {}).items() if key != "results"},
            "usage_by_role": report.get("usage_by_role", {}),
            "token_usage": report.get("token_usage", {}), "failures": dict(failures),
            "simple_metrics": report.get("simple", {}).get("metrics", {}),
            "complex_metrics": report.get("complex", {}).get("metrics", {}),
            "note": "正确率来自固定基准 SQL 结果比对；小样本不代表真实业务总体准确率。"}
