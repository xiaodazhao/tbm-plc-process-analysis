from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tbm_process.reporting_v2.geology import availability, attributes, build_geology, compare_attributes, forward_bounds, observation_clauses, partition_intervals, prefix_compatible
from tbm_process.reporting_v2.performance import calculate_metrics, compare_statistics, performance_windows, summarize_performance
from tbm_process.reporting_v2.facts import progress_comparison
from tbm_process.reporting_v2.narrative import check_draft, check_plan, offline_draft, offline_plan
from tbm_process.reporting_v2.rendering import extract_sketch_images


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return json.loads((ROOT/"tbm_process/reporting_v2/defaults.json").read_text())


def signals(count=35):
    # Synthetic algebra/interface fixtures only; never used as research outputs.
    return pd.DataFrame({"time": pd.date_range("2024-04-26", periods=count, freq="20s"),
                         "advance_speed": 40., "thrust": 20000., "torque": 4000., "rpm_canonical": 5.,
                         "phase_label": "stable", "experimental_phase_label": "stable", "machine_state": "advancing",
                         "rpm_quality": "usable_provisional_mapping", "advance_speed_quality": "usable",
                         "sample_dt_seconds": 20., "source_file": "unit_test_only.csv", "source_row": np.arange(count)+2,
                         "experimental_platform_id": "X", "experimental_phase_id": "XP", "phase_id": "XP", "chainage": 1015800.})


@pytest.mark.parametrize("basis,expected", [("2024-04-25", "eligible"), ("2024-04-26", "review"), ("2024-04-27", "excluded"), (None, "review")])
def test_time_admission(basis, expected):
    assert availability({"submission_date": basis}, "2024-04-26", "tsp")["status"] == expected


def test_test_date_not_treated_as_delivery():
    assert availability({"test_date": "2024-04-20"}, "2024-04-26", "hsp")["status"] == "review"


def test_verified_same_day_time():
    m = {"observation_date": "2024-04-26", "available_at": "2024-04-26T16:00:00+08:00", "actual_distribution_time_verified": True}
    assert availability(m, "2024-04-26", "sketch")["role"] == "same_day_new"
    m["actual_distribution_time_verified"] = False
    assert availability(m, "2024-04-26", "sketch")["status"] == "review"


def test_conflicting_dates():
    assert availability({"submission_date": "2024-04-20", "date_conflicts": ["cover_vs_text"]}, "2024-04-26", "hsp")["status"] == "review"


@pytest.mark.parametrize("direction,expected", [(1,(100.,120.)),(-1,(80.,100.)),(None,None)])
def test_direction(direction, expected):
    assert forward_bounds(100, direction, 20) == expected


def record(identity, lower, upper, text="岩体破碎，见线状出水", grade="IV"):
    return {"record_id": identity, "start_m": lower, "end_m": upper,
            "source": {"report_id": identity}, "attributes": attributes(text, grade)}


def test_real_boundaries_not_fixed_grid():
    parts = partition_intervals([record("T",0,7), record("H",3,10)], 0, 10)
    assert [(p["start_m"],p["end_m"]) for p in parts] == [(0,3),(3,7),(7,10)]
    assert sum(p["end_m"]-p["start_m"] for p in parts) == 10


def test_uncovered_range_preserved():
    parts = partition_intervals([record("T",3,5)], 0, 10)
    assert [p["covered"] for p in parts] == [False, True, False]


def test_dk_not_equated_to_dyk():
    assert prefix_compatible("DyK", "DyK1015+819")
    assert not prefix_compatible("DK")
    assert not prefix_compatible("DyK", "DK1015+819")


def test_two_grades_retained():
    c = compare_attributes(record("T",0,10,grade="V"), record("H",0,10,grade="IV"))
    assert c["status"] == "explicit_reported_grade_difference"
    assert c["attributes"]["grade"]["left"] == ["V"]
    assert c["attributes"]["grade"]["right"] == ["IV"]


def test_unmentioned_water_is_not_no_water():
    c = compare_attributes(record("T",0,10,"岩体破碎"),record("H",0,10,"岩体破碎，见线状出水"))
    assert c["attributes"]["water"]["status"] == "source_only_or_unknown"


def test_negative_fault_statement_not_positive():
    assert attributes("未见断层")["special"]["values"] == ["未见/否定：断层"]


def test_observation_does_not_absorb_forecast_or_recommendation():
    text, omitted = observation_clauses("当前开挖岩体破碎。推测前方岩体完整。建议：及时支护。")
    assert "推测" not in text and "建议" not in text
    assert len(omitted) == 2


def test_forecast_context_does_not_leak_into_observation():
    text, omitted = observation_clauses("推测前方岩体破碎。发育3组节理。当前开挖段落见滴水。")
    assert "节理" not in text
    assert "当前开挖段落见滴水" in text


def test_metrics_and_SI_conversion(config):
    result, check = calculate_metrics(signals(), config)
    area = math.pi*10.23**2/4
    assert result.pr.iloc[0] == 8
    assert result.fpi.iloc[0] == pytest.approx(20000/(70*8))
    expected = 20000/(1000*area)+2*math.pi*4000/(area*8)
    assert result.se.iloc[0] == pytest.approx(expected)
    assert check["formula_equivalence_checked"]


@pytest.mark.parametrize("field,value", [("rpm_canonical",0),("rpm_canonical",np.nan),("thrust",-1168883),("torque",np.inf)])
def test_invalid_inputs_never_enter_performance(config,field,value):
    d = signals()
    d.loc[0,field] = value
    out,_ = calculate_metrics(d,config)
    assert pd.isna(out.pr.iloc[0]) and not out.performance_eligible.iloc[0]


def test_stable_label_alone_is_not_enough(config):
    d=signals()
    d["machine_state"]="stopped"
    out,_=calculate_metrics(d,config)
    assert not out.performance_eligible.any()


def test_raw_penetration_not_assumed_or_used(config):
    d=signals()
    d["penetration_raw"]=99999
    out,_=calculate_metrics(d,config)
    assert out.pr.eq(8).all()


def test_compute_pointwise_before_aggregation(config):
    d=signals(10)
    d["advance_speed"]=[10]*6+[100]*4
    d["rpm_canonical"]=[1]*6+[5]*4
    out,_=calculate_metrics(d,config)
    assert out.pr.median() == 10
    assert out.pr.mean() != d.advance_speed.mean()/d.rpm_canonical.mean()


def test_near_zero_guard(config):
    d=signals()
    d.advance_speed=.51
    d.rpm_canonical=6.65
    out,_=calculate_metrics(d,config)
    assert not out.performance_eligible.any()


def test_short_window_does_not_invent_cv(config):
    d,_=calculate_metrics(signals(5),config)
    phase=json.loads((ROOT/"config.json").read_text())
    assert performance_windows(d,config,phase) == []


def test_window_constant_torque_and_real_time(config):
    d,_=calculate_metrics(signals(35),config)
    phase=json.loads((ROOT/"config.json").read_text())
    windows=performance_windows(d,config,phase)
    assert windows and all(w["torque_cv"] == 0 for w in windows)
    assert all(w["parameter_changes"]["torque"]["slope_per_minute"] == 0 for w in windows)


def test_invalid_sample_breaks_continuity(config):
    d=signals(25)
    d.loc[12,"rpm_canonical"]=np.nan
    out,_=calculate_metrics(d,config)
    phase=json.loads((ROOT/"config.json").read_text())
    windows=performance_windows(out,config,phase)
    assert all(not(w["start_time"]<d.time.iloc[12].isoformat()<w["end_time"]) for w in windows)


def test_reference_not_comparable_is_unknown(config):
    d,_=calculate_metrics(signals(),config)
    s=summarize_performance(d,config)
    assert compare_statistics(s,None,config)["status"] == "insufficient_data"
    changed=deepcopy(s)
    changed["statistics"]["rpm_canonical"]["median"]=10
    assert compare_statistics(changed,s,config)["status"] == "insufficient_data"
    assert compare_statistics(s,s,config)["status"] == "criterion_not_met"


def test_efficiency_is_arithmetic_not_speed_integration():
    a={"machine_state_duration_seconds":{"advancing":3600},"daily_advance":{"net_change_m":10}}
    b={"machine_state_duration_seconds":{"advancing":7200},"daily_advance":{"net_change_m":12},"date":"2024-01-01"}
    c=progress_comparison(a,b)
    assert c["time_component_m"]+c["rate_component_m"] == pytest.approx(-2)
    assert c["identity_error_m"] == pytest.approx(0)


def test_no_sketch_or_missing_original(config,tmp_path):
    info=extract_sketch_images({"geology":{"selected_observations":[]}},tmp_path)
    assert info["images"] == []
    info=extract_sketch_images({"geology":{"selected_observations":[{"record_id":"x","source":{"path":None}}]}},tmp_path)
    assert info["unavailable"][0]["reason"] == "original_pdf_missing"


def test_six_chapters_and_wrong_ids():
    facts=[{"fact_id":f"F{i}","chapter":i,"canonical_text":"这是原事实。","required":True} for i in range(1,7)]
    plan=offline_plan(facts)
    assert check_plan(plan,facts)["passed"]
    plan["chapters"][0]["fact_ids"]=["made_up"]
    assert not check_plan(plan,facts)["passed"]


def test_offline_text_cannot_be_replaced_or_called_llm():
    facts=[{"fact_id":f"F{i}","chapter":i,"canonical_text":"进尺9米。","required":True} for i in range(1,7)]
    plan=offline_plan(facts)
    draft=offline_draft(plan,facts)
    assert check_draft(draft,plan,facts,True)["passed"]
    assert draft["provenance"] == "deterministic_offline_not_llm"
    draft["chapters"][0]["claims"][0]["text"]="进尺90米。"
    assert not check_draft(draft,plan,facts,True)["passed"]


def test_missing_rpm_column_skips_metrics(config):
    d=signals().drop(columns="rpm_canonical")
    out,quality=calculate_metrics(d,config)
    assert not out.performance_eligible.any()
    assert "rpm_canonical" in quality["missing_required_input_columns"]


def test_roman_grade_normalization_not_false_difference():
    c=compare_attributes(record("T",0,10,grade="Ⅴ"),record("H",0,10,grade="V"))
    assert c["attributes"]["grade"]["status"] == "same_literal_attributes"


def test_no_synthetic_design_when_data_missing(config,tmp_path):
    config["design_records"]=[{"verified":False,"record_id":"D"}]
    g=build_geology({"reports":[]},{"source_registry":[]},tmp_path,"2024-04-26",10,20,1,config)
    assert not g["design_records"] and g["design_rejections"]
    assert g["coverage"]["ahead_covered_m"] == 0


def test_multiple_sketches_date_policy(config,tmp_path):
    def sketch(i,day,point):
        return {"report_metadata":{"source_type":"sketch","source_report_id":i,"source_sha256":i,"source_file":i+".pdf", "chainage_prefix":"DyK","alignment":"right","observation_date":day},
                "observation":{"observation_id":"O1","chainage_m":point,"geological_description":"岩体破碎。建议支护。","table_suggested_grade":"IV"}}
    g=build_geology({"reports":[sketch("past1","2024-04-24",15),sketch("past2","2024-04-25",18),sketch("today","2024-04-26",19)]},
                    {"source_registry":[]},tmp_path,"2024-04-26",10,20,1,config)
    assert len(g["selected_observations"]) == 2
    assert len(g["same_day_pending_observations"]) == 1
    assert all(x["observation_date"]<"2024-04-26" for x in g["selected_observations"])


def test_risk_interval_is_not_split_into_points(config,tmp_path):
    m={"source_type":"tsp","source_report_id":"T","source_sha256":"test","source_file":"test.pdf", "chainage_prefix":"DyK","alignment":"right","submission_date":"2024-04-20"}
    r={"report_metadata":m,"forecast_intervals":[],"risk_records":[
       {"risk_id":"R1","geometry":"interval","start_m":18,"end_m":23,"risk_type":"出水风险"},
       {"risk_id":"R2","geometry":"point","chainage_m":20,"risk_type":"掉块风险"}]}
    g=build_geology({"reports":[r]},{"source_registry":[]},tmp_path,"2024-04-26",10,20,1,config)
    assert len(g["ahead_risks"]) == 1
    assert g["ahead_risks"][0]["geometry"] == "interval"
    assert g["ahead_risks"][0]["display_range_m"] == [20,23]
    assert len(g["day_risks"]) == 2


def test_offline_does_not_call_API(monkeypatch,tmp_path):
    from tbm_process.reporting_v2 import narrative
    facts=[{"fact_id":f"F{i}","chapter":i,"canonical_text":"原事实。","required":True} for i in range(1,7)]
    monkeypatch.setattr(narrative,"build_writing_facts",lambda _:facts)
    def forbidden(*args,**kwargs):
        raise AssertionError("Offline mode must never call API")
    monkeypatch.setattr(narrative,"call_json_model",forbidden)
    _,meta=narrative.generate_narrative({},tmp_path)
    assert meta["api_calls"] == 0 and meta["mode"] == "offline_template_no_llm_called"


def test_model_failure_fallback_is_explicit(monkeypatch,tmp_path):
    from tbm_process.reporting_v2 import narrative
    facts=[{"fact_id":f"F{i}","chapter":i,"canonical_text":"原事实。","required":True} for i in range(1,7)]
    monkeypatch.setattr(narrative,"build_writing_facts",lambda _:facts)
    def failed(*args,**kwargs):
        raise RuntimeError("deliberate_unit_test_stub_not_real_API")
    monkeypatch.setattr(narrative,"call_json_model",failed)
    _,meta=narrative.generate_narrative({},tmp_path,mode="llm",api_key="unit_test_placeholder")
    assert meta["mode"] == "offline_fallback_after_real_llm_failure"
    assert meta["api_calls"] == 1
    assert (tmp_path/"llm_failure.json").is_file()
