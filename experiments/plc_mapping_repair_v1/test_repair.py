"""Synthetic structural fixtures test the adapter, not engineering accuracy."""
import numpy as np
import pandas as pd

from experiments.plc_mapping_repair_v1.repair import GAS_PREFIX_FIELDS, GASES, MOTORS, repair_frame


def fixture(relocated=False):
    header = [f"unused_{i}" for i in range(330)]
    names = {0: "运行时间-time", 1: "掘进状态", 8: "刀盘扭矩", 9: "刀盘给定转速", 10: "刀盘实际转速",
             11: "刀盘刹车压力", 14: "推力", 15: "推进给定速度", 16: "推进速度", 17: "贯入度",
             188: "刹车释放压力", 189: "刀盘给定频率", 190: "1#变频柜内部水温度",
             300: "导向滚动角", 301: "导向盾首里程", 302: "导向盾中里程", 303: "导向盾尾里程"}
    names.update(dict(enumerate(MOTORS, 89)))
    names.update(dict(enumerate(GAS_PREFIX_FIELDS, 188)))
    names.update(dict(enumerate(GASES, 221)))
    for pos, name in names.items():
        header[pos] = name
    row = np.full(330, 0.123456789)
    row[1] = 1
    row[[8, 11, 14, 15, 16, 17]] = [3000, 90, 20000, 30, 30, 6.66]
    start = 88 if relocated else 89
    row[start:start+14] = np.arange(14) + 100
    row[start+14:start+28] = np.arange(14) + 300
    row[start+28:start+42] = 73.77
    row[start+42:start+56] = np.arange(14) + 40
    row[start+56:start+70] = 1
    row[start+70:start+84] = np.arange(14) + 25
    row[start+84:start+98] = 1
    brake = start + 99
    row[brake] = 90
    if relocated:
        row[9], row[10], row[brake+1], row[brake+2] = 32, 4.6/.061, 4.5, 4.6
    else:
        row[9], row[10], row[brake+1] = 4.6, 4.5, 73.77
    row[brake+33:brake+39] = [0.02, 50/27648, 0, 20/27648, 100/27648, 200/27648]
    row[300:304] = [-2, 1015674, 1015660, 0]
    data = dict(zip(header, row))
    data[header[0]] = "2024-04-07 12:00:00"
    return pd.DataFrame([data])


def test_direct_and_relocated_layouts_without_using_calendar_or_penetration():
    for shifted in [False, True]:
        raw = fixture(shifted)
        before = raw.copy(deep=True)
        raw.loc[0, "贯入度"] = np.nan
        fixed, evidence = repair_frame(raw)
        assert fixed["刀盘实际转速"].iloc[0] == 4.5
        assert fixed["刀盘给定转速"].iloc[0] == 4.6
        assert evidence.rpm_layout.iloc[0] == ("relocated_after_brake" if shifted else "direct_actual")
        assert fixed["M1电机电流"].iloc[0] == 100
        assert fixed["M14电机电流"].iloc[0] == 113
        assert fixed["CO2检测"].iloc[0] == 0.02
        assert not fixed.mapping_units_confirmed.iloc[0]
        assert fixed["推力"].equals(raw["推力"])
        assert before["刀盘实际转速"].equals(raw["刀盘实际转速"])


def test_competing_brake_anchor_is_quarantined_not_guessed():
    raw = fixture()
    raw.loc[0, "unused_187"] = 90
    fixed, evidence = repair_frame(raw)
    assert evidence.duplicate_brake_source_index.iloc[0] == -1
    assert np.isnan(fixed["刀盘实际转速"].iloc[0])
    assert evidence.rpm_layout.iloc[0] == "unresolved"
    assert fixed["raw__刀盘实际转速"].iloc[0] == 4.5


def test_missing_motor_identity_prevents_automatic_per_channel_repair():
    raw = fixture().rename(columns={"M6电机温度": "unknown_temperature"})
    fixed, evidence = repair_frame(raw)
    assert evidence.motor_block_start_index.iloc[0] == -1
    assert fixed["M1电机电流"].isna().all()
    assert fixed["刀盘实际转速"].isna().all()


def test_gas_step_failure_does_not_change_unrelated_core_fields():
    raw = fixture()
    raw.loc[0, "CH4检测"] = 0.123456
    fixed, evidence = repair_frame(raw)
    assert fixed[GASES].isna().all(axis=None)
    assert fixed["推进速度"].equals(raw["推进速度"])
    assert fixed["刀盘实际转速"].iloc[0] == 4.5


def test_unknown_right_shift_core_is_preserved_as_raw_and_not_used():
    raw = fixture()
    raw.loc[0, ["导向盾首里程", "导向盾中里程", "导向盾尾里程"]] = [0, 1015674, 1015660]
    fixed, evidence = repair_frame(raw)
    assert evidence.navigation_mode.iloc[0] == "shifted_right"
    assert fixed["推力"].isna().all()
    assert fixed["raw__推力"].iloc[0] == 20000
    assert fixed["导向盾首里程"].iloc[0] == 1015674


def test_missing_gas_header_cannot_be_replaced_by_matching_quantized_neighbor():
    raw = fixture().drop(columns=["H2S检测"])
    fixed, evidence = repair_frame(raw)
    assert fixed[GASES].isna().all(axis=None)
    assert not evidence.gas_header_complete_and_ordered.iloc[0]


def test_unvalidated_relocated_setpoint_does_not_invalidate_independent_actual_rpm():
    raw = fixture(True)
    raw.iloc[0, 189] = 123456
    fixed, evidence = repair_frame(raw)
    assert fixed["刀盘给定转速"].isna().all()
    assert fixed["刀盘实际转速"].iloc[0] == 4.5
    assert not evidence.relocated_set_role_validated.iloc[0]
