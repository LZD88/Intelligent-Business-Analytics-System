import pytest
from chatbi.application.narrative_verification import verify_text
from chatbi.domain.complex_analysis import ResolvedEvidence


def refs():
    return [ResolvedEvidence(step_id="step_1",step_name="比较",row_index=i,column="金额",value=v) for i,v in enumerate([120,100])]


def test_evidenced_difference_and_comparison():
    verify_text("金额120高于100，差额20。", refs())
    verify_text("金额高出20%。", refs())
    verify_text("这些结果不能证明维护导致成本变化。", refs())


def test_explicit_money_units_use_display_precision_only():
    evidence = [refs()[0].model_copy(update={"value": 1234567})]
    verify_text("金额为123.46万元。", evidence)
    with pytest.raises(ValueError):
        verify_text("金额为123.47万元。", evidence)


@pytest.mark.parametrize("text",["金额为999。","金额120低于100。","维护导致成本下降。","保证提升20%。"])
def test_fabricated_number_false_direction_and_causality(text):
    with pytest.raises(ValueError):
        verify_text(text,refs())


def test_counts_and_user_thresholds_are_scoped_not_general_evidence():
    verify_text("共4个中心，其余3个中心。", [], counts={4, 3})
    verify_text("毛利率0.48低于0.8。", [refs()[0].model_copy(update={"value": .48})], thresholds={.8})
    for text in ("金额4元。", "共9个中心。", "金额0.8元。"):
        with pytest.raises(ValueError):
            verify_text(text, [], counts={4, 3}, thresholds={.8})


def test_rates_differences_with_negative_denominator():
    evidence = [refs()[0].model_copy(update={"value": 150}), refs()[1].model_copy(update={"value": -100})]
    verify_text("差额250，变化250%。", evidence)


def test_chinese_manufacturing_cost_is_not_a_causal_assertion():
    verify_text("电池制造成本中心实际低于预算。", [])
    verify_text("2026年9月与8月对比。", [])
    verify_text("环比下降约20%。", [refs()[0].model_copy(update={"value": -.2})])


def test_null_warning_is_not_a_zero_value_claim():
    evidence = [refs()[0].model_copy(update={"value": None})]
    verify_text("同比为NULL，不能填0。", evidence)
    verify_text("同期为NULL，未填0。", evidence)
    with pytest.raises(ValueError):
        verify_text("同比为0。", evidence)
    verify_text("用户阈值0.8。", [], thresholds={.8})
