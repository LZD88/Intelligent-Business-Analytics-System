"""Conservative numeric and claim checks, with explicit limits on prose proof."""
from decimal import Decimal, InvalidOperation
import re

NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|亿|万|%|％|元|个百分点)?")
CAUSAL = re.compile(r"(?:导致|造成|归因于|根本原因是|证明了|必然带来|必将|保证(?:提升|降低))")
NEGATIVE = re.compile(r"(?:不能|无法|尚不能|不足以|不代表|不意味着|未能|缺乏|尚无|没有|待验证|需验证)")


def number(token):
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", token)
    if match is None:
        raise ValueError("无效数值")
    value = Decimal(match[0].replace(",", ""))
    if "亿" in token:
        value *= 100000000
    elif "万" in token:
        value *= 10000
    return value


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def verify_text(text, evidence, *, extras=()):
    values = [v for ref in evidence if (v := numeric(ref.value)) is not None]
    allowed = set(values) | {Decimal(str(v)) for v in extras}
    # Only direct differences and rates of referenced cells are permitted.
    # This does not establish that a comparison is appropriate in business terms.
    for a in values:
        for b in values:
            allowed.add(a - b)
            if b:
                allowed.update({a / b * 100, (a - b) / abs(b) * 100})
    cleaned = text
    for ref in evidence:
        if numeric(ref.value) is None and ref.value is not None:
            cleaned = cleaned.replace(str(ref.value), "对象")
    cleaned = re.sub(r"\d{4}年(?:\d{1,2}月)?(?:\d{1,2}日)?|\d{4}-\d{2}(?:-\d{2})?", "期间", cleaned)
    for match in NUMBER.finditer(cleaned):
        # Counts and schedule proposals are not source-cell claims.
        suffix = cleaned[match.end():match.end()+3]
        if re.match(r"(?:条建议|个步骤|个节点|项发现)", suffix):
            continue
        actual = number(match[0])
        # Match explicit display precision, including rounded 万/亿 units.
        # This is half a display unit, not an amount-dependent relative tolerance.
        tolerance = Decimal("0.01")
        if "万" in match[0] or "亿" in match[0]:
            digits = re.search(r"\d[\d,]*(?:\.(\d+))?", match[0])
            scale = Decimal(100000000 if "亿" in match[0] else 10000)
            tolerance = scale * Decimal(10) ** (-len((digits[1] if digits else "") or "")) / 2
        candidates = allowed | ({v * 100 for v in values} if "%" in match[0] or "％" in match[0] else set())
        if not any(abs(actual - candidate) <= tolerance for candidate in candidates):
            raise ValueError("正文含无对应证据的数值，须删除或引用真实单元格及可核算差额")
    for clause in re.split(r"[。；;\n]", text):
        for match in CAUSAL.finditer(clause):
            if not NEGATIVE.search(clause[max(0, match.start()-18):match.end()]):
                raise ValueError("正文声称因果或确定收益，现有查询结果只能支持观察和待验证假设")
    # Explicit numeric comparison direction is mechanically checkable.
    comparison = re.compile(r"(\d+(?:\.\d+)?)\s*(高于|大于|超过|低于|小于)\s*(\d+(?:\.\d+)?)")
    for a, op, b in comparison.findall(cleaned):
        correct = Decimal(a) > Decimal(b) if op in {"高于", "大于", "超过"} else Decimal(a) < Decimal(b)
        if not correct:
            raise ValueError("正文比较方向与数值矛盾")


def verify_report(report, successful):
    all_evidence = [ref for item in report.findings for ref in item.evidence]
    all_evidence += [ref for item in report.recommendations for ref in item.evidence]
    # Summary may cite any successful cell; findings/actions must use their own evidence.
    from chatbi.domain.complex_analysis import ResolvedEvidence
    all_cells = [ResolvedEvidence(step_id=step.step_id, step_name=step.step_name, row_index=i,
                column=column, value=row[j]) for step in successful for i,row in enumerate(step.rows)
                for j,column in enumerate(step.columns) if j < len(row)]
    verify_text(report.title + "。" + report.executive_summary, all_cells)
    for item in report.findings:
        verify_text(item.title + "。" + item.statement, item.evidence)
    for item in report.recommendations:
        verify_text("。".join([item.problem,item.action,item.rationale,item.expected_impact,item.precondition,item.risk]), item.evidence)
    for caveat in [*report.caveats, report.recommendation_reason]:
        verify_text(caveat, all_cells)
    return {"status": "checked", "checks": ["evidence_binding", "numeric_consistency", "explicit_comparison", "causal_language"],
        "boundary": "已核验引用、正文数值和显式比较；业务归因、隐含语义与建议效果仍需人工复核。"}
