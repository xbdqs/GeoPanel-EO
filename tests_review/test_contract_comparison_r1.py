"""Focused assertions for the reviewer R1 comparison (supplements, not replaces, core tests)."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from review_tools.contract_vs_unchecked_workflow import evaluate


def test_complete_months_positive_control():
    x={r['scenario']:r for r in evaluate()}['complete_months_temperature']
    assert x['unchecked_pandas']==pytest.approx(5.5)
    assert x['geopanel_v010']==pytest.approx(5.5)
    assert x['geopanel_qc_flag']=='OK'


def test_semantic_guard_rejects_temperature_sum():
    x={r['scenario']:r for r in evaluate()}['temperature_sum_configuration']
    assert x['unchecked_pandas']==pytest.approx(66)
    assert x['geopanel_v010'] is None
    assert x['geopanel_qc_flag']=='SEMANTIC_RULE'


def test_incomplete_month_and_missing_year_preserved():
    x={r['scenario']:r for r in evaluate()}
    assert x['missing_december']['unchecked_pandas']==pytest.approx(5.0)
    assert x['missing_december']['geopanel_v010'] is None
    assert 'PARTIAL_TIME_WINDOW' in x['missing_december']['geopanel_qc_flag']
    assert x['missing_internal_year']['unchecked_pandas'] is None
    assert 'PARTIAL_TIME_WINDOW' in x['missing_internal_year']['geopanel_qc_flag']


def test_upstream_low_coverage_is_not_silently_averaged():
    x={r['scenario']:r for r in evaluate()}['upstream_low_coverage_month']
    assert x['unchecked_pandas']==pytest.approx((sum(range(12))-3)/11)
    assert x['geopanel_v010'] is None
    assert 'PARTIAL_TIME_WINDOW' in x['geopanel_qc_flag']
