"""Publish reviewed join-grain clarifications through the normal version workflow."""
from chatbi.infrastructure.metric_registry import CatalogDecision, MetricDraft, MetricRegistry
from chatbi.settings import Settings
from indicator_knowledge import IndicatorKnowledge


def update(settings=None):
    registry=MetricRegistry((settings or Settings()).metric_registry_path)
    seed=IndicatorKnowledge().indicators
    changed=[]
    for item in registry.list_metrics():
        if item["name"] not in {"客户回款率","采购付款率","生产完成率"}:
            continue
        definition=seed[item["name"]]
        if item["payload"]==definition:
            continue
        if item["status"]!="published":
            raise ValueError("存在未完成版本，需先处理指标草稿")
        draft=registry.create_draft(MetricDraft(**definition,owner="口径维护",change_note="明确关联前预聚合、订单分母去重及币种匹配"),item["id"])
        for action in ("submit","publish"):
            registry.transition(item["id"],draft["version"],action,CatalogDecision(actor="本轮授权口径核查",note="通过独立标准SQL和关联粒度检查"))
        changed.append(item["name"])
    return changed


if __name__=="__main__":
    print("Updated catalog metrics:", ", ".join(update()) or "already current")
