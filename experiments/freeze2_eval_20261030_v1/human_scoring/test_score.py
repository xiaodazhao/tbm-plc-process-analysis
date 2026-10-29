import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('human_score',Path(__file__).with_name('score.py'))
score=importlib.util.module_from_spec(spec);spec.loader.exec_module(score)


def test_blank_human_data_never_becomes_zero_or_perfect_accuracy():
    r=score.support_summary([{'support_label':''}])
    assert r['status']=='NOT_EVALUATED' and r['supported_among_determinable'] is None


def test_insufficient_evidence_is_separate_from_supported_and_error():
    r=score.support_summary([{'support_label':s} for s in ['SUPPORTED','CONTRADICTED','UNSUPPORTED','INSUFFICIENT_EVIDENCE','NON_FACTUAL']])
    assert r['supported_among_determinable']==pytest.approx(1/3)
    assert r['conservative_supported']==.25 and r['insufficient_evidence_ratio']==.25


def test_unknown_label_is_not_guessed():
    with pytest.raises(ValueError):score.support_summary([{'support_label':'自动通过'}])


def test_missing_two_reviewers_does_not_create_agreement():
    assert score.agreement([],[])['cohen_kappa'] is None
