import pytest
from chatbi.application.narrative_verification import verify_text
from chatbi.domain.complex_analysis import ResolvedEvidence


def refs():
    return [ResolvedEvidence(step_id="step_1",step_name="比较",row_index=i,column="金额",value=v) for i,v in enumerate([120,100])]


def test_evidenced_difference_and_comparison():
    verify_text("金额120高于100，差额20。", refs())
    verify_text("金额高出20%。", refs())
    verify_text("这些结果不能证明维护导致成本变化。", refs())


@pytest.mark.parametrize("text",["金额为999。","金额120低于100。","维护导致成本下降。","保证提升20%。"])
def test_fabricated_number_false_direction_and_causality(text):
    with pytest.raises(ValueError):
        verify_text(text,refs())
