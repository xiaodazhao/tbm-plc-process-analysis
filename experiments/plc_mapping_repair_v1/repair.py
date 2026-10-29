"""Conservative block-local export adapter. Originals and algorithms are immutable."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from experiments.plc_mapping_repair_v1.audit_all import frame_layout, sha
from tbm_process.ingestion.io import write_json

VERSION = "tbm9_block_local_export_adapter_v1r2_20261023"
GASES = ["CO2检测", "H2S检测", "SO2检测", "NO2检测", "NO检测", "CH4检测"]
GAS_PREFIX_FIELDS = ["刹车释放压力", "刀盘给定频率", "1#变频柜内部水温度", "1#变频柜202水压力", "1#变频柜203水压力",
    "2#变频柜内部水温度", "2#变频柜202水压力", "2#变频柜203水压力", "电机对称选择", "刀盘主控室控制选择",
    "主机皮带机速度达到", "刀盘急停正常", "内循环水泵运行", "EP2系统正常", "齿轮油系统正常", "刀盘电流扭矩正常",
    "刹车液压泵运行", "主驱动加压压力", "小齿轮轴承1#脉冲次数", "小齿轮轴承2#脉冲次数", "齿轮1#脉冲次数",
    "齿轮2#脉冲次数", "主轴承1#脉冲次数", "主轴承2#脉冲次数", "齿轮1#油泵压力", "齿轮2#油泵压力", "齿轮3#油泵压力",
    "回油温度", "回油泵出口压力", "回油流量", "齿轮润滑油箱液位", "齿轮油箱温度", "齿轮油箱加压压力"]
MOTOR_GROUPS = ["电机电流", "电机扭矩", "电机频率", "电机温度", "减速机温度开关", "减速机温度", "电机开关"]
MOTORS = [f"M{i}{suffix}" for suffix in MOTOR_GROUPS for i in range(1, 15)]


def numeric_matrix(raw):
    return raw.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def motor_positions(raw, values):
    """Find 14-channel blocks using two discrete runs plus intervening temperatures."""
    center = raw.columns.get_loc("M1电机电流")
    candidates = []
    for start in range(max(1, center - 3), min(values.shape[1] - 98, center + 3) + 1):
        first = values[:, start+56:start+70]
        second = values[:, start+84:start+98]
        flags = np.isin(first, [0, 1]).all(axis=1) & np.isin(second, [0, 1]).all(axis=1)
        t1, t2 = values[:, start+42:start+56], values[:, start+70:start+84]
        # Type evidence only; these broad bounds are NOT equipment temperature limits.
        temps = ((t1 > 1) & (t1 < 200)).sum(axis=1) >= 8
        temps &= ((t2 > 1) & (t2 < 200)).sum(axis=1) >= 8
        candidates.append((start, flags & temps))
    hits = sum(mask.astype(int) for _, mask in candidates)
    positions = np.full(len(raw), -1, dtype=int)
    for start, mask in candidates:
        positions[mask & (hits == 1)] = start
    return positions, hits


def repair_frame(raw):
    out = raw.copy(deep=True)
    values = numeric_matrix(raw)
    nav = frame_layout(raw)
    starts, motor_hits = motor_positions(raw, values)
    present = all(field in raw for field in MOTORS)
    ordered_complete = present and [raw.columns.get_loc(field) for field in MOTORS] == list(range(raw.columns.get_loc(MOTORS[0]), raw.columns.get_loc(MOTORS[0]) + 98))
    if not ordered_complete:
        starts[:] = -1
    n = len(raw)
    indices = np.arange(n)
    meta_values = {"source_row": indices + 2, "time": raw["运行时间-time"],
                   "navigation_mode": nav.chainage_mapping_mode,
                   "motor_header_complete_ordered": ordered_complete,
                   "motor_block_start_index": starts, "motor_layout_candidate_count": motor_hits}
    shadows = {}
    source_indices = {}

    def assign(field, positions, reason):
        positions = np.asarray(positions, dtype=int)
        result = np.full(n, np.nan)
        valid = (positions >= 0) & (positions < values.shape[1])
        result[valid] = values[indices[valid], positions[valid]]
        if field in raw:
            shadows["raw__" + field] = raw[field]
        out[field] = result
        source_indices[field] = positions
        meta_values["source_index__" + field] = positions
        return result

    for offset, field in enumerate(MOTORS):
        positions = np.where(starts >= 0, starts + offset, -1)
        assign(field, positions, "two_boolean_runs_and_temperature_boundaries")

    # Brake pressure is independently duplicated near the drive block. Match both occurrences.
    brake_ref = pd.to_numeric(raw["刀盘刹车压力"], errors="coerce").to_numpy()
    brake_pos = np.full(n, -1, dtype=int)
    brake_hits = np.zeros(n, dtype=int)
    for offset in (98, 99, 100, 101, 102):
        pos = np.clip(starts + offset, 0, values.shape[1] - 1)
        hit = (starts >= 0) & (brake_ref > 1) & np.isclose(values[indices, pos], brake_ref, rtol=0.00001, atol=0.0001)
        brake_pos[hit] = pos[hit]
        brake_hits += hit
    brake_pos[brake_hits != 1] = -1
    meta_values["duplicate_brake_source_index"] = brake_pos

    # Header indices 9/10 are invariant in every inspected schema, but their payload roles differ.
    freq_pos = np.where(starts >= 0, starts + 28, -1)
    freq = np.full(n, np.nan)
    valid = freq_pos >= 0
    freq[valid] = values[indices[valid], freq_pos[valid]]
    actual = pd.to_numeric(raw["刀盘实际转速"], errors="coerce").to_numpy()
    setting = pd.to_numeric(raw["刀盘给定转速"], errors="coerce").to_numpy()
    after_brake = np.clip(brake_pos + 1, 0, values.shape[1] - 1)
    next_value = values[indices, after_brake]
    running = freq > 1
    ratio_matches = lambda x: np.abs(x - freq * 0.061) <= np.maximum(0.2, np.abs(freq * 0.061) * 0.15)
    direct = valid & (brake_pos >= 0) & (actual >= 0) & (actual <= 10) & (setting >= 0) & (setting <= 10)
    direct &= (~running | ratio_matches(actual))
    direct &= np.isclose(next_value, freq, rtol=0.05, atol=0.2)
    relocated = valid & (brake_pos >= 0) & (next_value >= 0) & (next_value <= 10)
    relocated &= np.isclose(actual, freq, rtol=0.05, atol=0.2)
    relocated &= (~running | ratio_matches(next_value))
    # Water-temperature-like setpoint payload and frequency-like actual payload provide extra role evidence.
    relocated &= (setting > 10) | (actual > 10)
    rpm_pos = np.full(n, -1, dtype=int)
    set_pos = np.full(n, -1, dtype=int)
    rpm_pos[direct & ~relocated] = raw.columns.get_loc("刀盘实际转速")
    set_pos[direct & ~relocated] = raw.columns.get_loc("刀盘给定转速")
    rpm_pos[relocated & ~direct] = after_brake[relocated & ~direct]
    set_pos[relocated & ~direct] = after_brake[relocated & ~direct] + 1
    candidate_set = values[indices, np.clip(set_pos, 0, values.shape[1]-1)]
    set_role_ok = (candidate_set >= 0) & (candidate_set <= 10)
    set_role_ok &= np.abs(candidate_set - actual * 0.061) <= np.maximum(0.02, np.abs(actual * 0.061) * 0.01)
    set_pos[relocated & ~set_role_ok] = -1
    meta_values["relocated_set_role_validated"] = relocated & set_role_ok
    # A stopped row with all competing RPM payloads equal zero does not need a guessed identity.
    zero_agreement = valid & (freq == 0) & (actual == 0) & (brake_pos >= 0) & (next_value == 0)
    rpm_pos[zero_agreement & (rpm_pos < 0)] = raw.columns.get_loc("刀盘实际转速")
    assign("刀盘实际转速", rpm_pos, "duplicated_brake_frequency_and_block_type")
    assign("刀盘给定转速", set_pos, "layout_specific_role")
    meta_values["rpm_layout"] = np.select([direct & ~relocated, relocated & ~direct, zero_agreement],
                                  ["direct_actual", "relocated_after_brake", "zero_value_agreement"], default="unresolved")

    # Gas identity requires both a structural brake-to-gas offset and an independent quantization check.
    gas_start = np.where(brake_pos >= 0, brake_pos + 33, -1)
    gas_good = gas_start >= 0
    gas_header_complete = all(field in raw for field in GASES) and "刹车释放压力" in raw
    if gas_header_complete:
        first = raw.columns.get_loc("CO2检测")
        gas_header_complete = raw.columns[first:first+6].tolist() == GASES
        gas_header_complete &= raw.columns[raw.columns.get_loc("刹车释放压力"):first].tolist() == GAS_PREFIX_FIELDS
    gas_good &= gas_header_complete
    meta_values["gas_header_complete_and_ordered"] = gas_header_complete
    for offset, scale in ((1, 50), (3, 20), (4, 100), (5, 100)):
        pos = np.clip(gas_start + offset, 0, values.shape[1]-1)
        counts = values[indices, pos] * 27648 / scale
        gas_good &= np.isfinite(counts) & (np.abs(counts - np.round(counts)) <= 0.002)
    # A negative/offset SO2 payload is not excluded by a guessed zero offset or sensor unit.
    for offset, gas in enumerate(GASES):
        assign(gas, np.where(gas_good, gas_start + offset, -1), "duplicate_brake_layout_plus_quantization")
    meta_values["gas_block_start_index"] = np.where(gas_good, gas_start, -1)
    meta_values["gas_status"] = np.where(gas_good, "structural_and_step_supported_units_unconfirmed", "unresolved")

    # Navigation is its own block: preserve its existing three-layout recovery without transferring it to motors/gases.
    for field, series in [("导向盾首里程", nav.chainage), ("导向盾中里程", nav.chainage_middle_canonical)]:
        if field in raw:
            shadows["raw__" + field] = raw[field]
        out[field] = series
    # The rare right-shift family has no validated core mapping; do not infer it from navigation alone.
    quarantine = nav.chainage_mapping_mode.eq("shifted_right")
    for field in ["刀盘扭矩", "推力", "推进速度", "贯入度", "推进给定速度"]:
        if field in raw:
            shadows["raw__" + field] = raw[field]
            out.loc[quarantine, field] = np.nan
    meta = pd.DataFrame(meta_values)
    out = pd.concat([out, pd.DataFrame(shadows)], axis=1)
    out["mapping_version"] = VERSION
    out["mapping_source_row"] = indices + 2
    out["mapping_rpm_source_index"] = rpm_pos
    out["mapping_rpm_layout"] = meta.rpm_layout
    out["mapping_gas_status"] = meta.gas_status
    out["mapping_navigation_mode"] = nav.chainage_mapping_mode
    out["mapping_core_positions_confirmed"] = ~quarantine
    # Unresolved RPM does not invalidate independently located speed/thrust/torque, but must remain missing.
    out["mapping_units_confirmed"] = False
    return out, meta


def run(dates=None):
    sources = json.loads((ROOT / "source_inventory.json").read_text())
    (ROOT / "repaired_raw").mkdir(exist_ok=True)
    (ROOT / "repair_provenance").mkdir(exist_ok=True)
    summaries, distributions, unresolved = [], [], []
    for source in sources:
        day = source["date"]
        if dates and day not in dates:
            continue
        raw = pd.read_csv(source["source"], encoding="utf-8-sig", low_memory=False)
        repaired, meta = repair_frame(raw)
        output = ROOT / "repaired_raw" / f"{day}.csv.gz"
        repaired.to_csv(output, index=False, encoding="utf-8-sig", compression={"method": "gzip", "compresslevel": 1})
        meta.to_csv(ROOT / "repair_provenance" / f"{day}.csv.gz", index=False, encoding="utf-8-sig", compression={"method": "gzip", "compresslevel": 1})
        row = {"date": day, "rows": len(raw), "raw_source": source["source"], "raw_sha256": source["sha256"],
               "repaired_sha256": sha(output), "version": VERSION,
               "rpm_resolved": int(meta.rpm_layout.ne("unresolved").sum()),
               "rpm_relocated": int(meta.rpm_layout.eq("relocated_after_brake").sum()),
               "motor_resolved": int(meta.motor_block_start_index.ge(0).sum()),
               "gas_resolved": int(meta.gas_block_start_index.ge(0).sum()),
               "navigation_unavailable": int(meta.navigation_mode.isin(["unavailable", "ambiguous"]).sum()),
               "raw_unchanged": sha(source["source"]) == source["sha256"]}
        summaries.append(row)
        for group, mask in [("rpm", meta.rpm_layout.eq("unresolved")), ("motor", meta.motor_block_start_index.lt(0)),
                            ("gas", meta.gas_block_start_index.lt(0))]:
            for _, item in meta.loc[mask, ["source_row", "time"]].iterrows():
                unresolved.append({"date": day, "block": group, "source_row": int(item.source_row), "time": str(item.time),
                                   "source": source["source"], "reason": "independent_anchor_or_role_checks_not_unique"})
        for field in ["刀盘实际转速", "刀盘给定转速", "推力", "刀盘扭矩", "推进速度", "贯入度", "导向盾首里程", *GASES, *MOTORS]:
            before = pd.to_numeric(raw[field], errors="coerce") if field in raw else pd.Series(np.nan, index=raw.index)
            after = pd.to_numeric(repaired[field], errors="coerce")
            distributions.append({"date": day, "field": field,
                "changed_rows": int((~np.isclose(before, after, rtol=0, atol=0, equal_nan=True)).sum()),
                **{f"{label}_{stat}": value for label, vec in [("before", before), ("after", after)]
                   for stat, value in [("valid", int(vec.notna().sum())), ("median", vec.median()),
                                       ("p10", vec.quantile(.1)), ("p90", vec.quantile(.9))]}})
        pd.DataFrame(summaries).to_csv(ROOT / "repair_daily_summary.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(distributions).to_csv(ROOT / "field_distribution_changes.csv", index=False, encoding="utf-8-sig")
        print(f"Repaired {day}: rpm {row['rpm_resolved']}/{len(raw)}, gas {row['gas_resolved']}/{len(raw)}", flush=True)
    pd.DataFrame(unresolved).to_csv(ROOT / "unresolved_records.csv.gz", index=False, encoding="utf-8-sig", compression="gzip")
    write_json(ROOT / "repair_rules.json", {"version": VERSION, "core_method_changed": False, "date_based_mapping": False,
        "motor_block": "unique two 14-boolean runs separated by 14 temperatures, preserve channel order",
        "rpm": "duplicated brake anchor + motor frequency consistency + distinct payload roles; never speed/penetration alone",
        "gas": "brake index +33, independent 4-channel step compatibility; no gas safety interpretation",
        "navigation": "existing independent head/middle plausibility mapping; no interpolation",
        "peripheral_fields": "raw passthrough only; not certified, not used for equipment conclusions",
        "source_indices": "zero-based original CSV column indices; -1 means unresolved and value masked",
        "raw_values": "raw__ columns for each altered field, originals also retained with SHA256",
        "units": "unconfirmed", "safety_comparison": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="*")
    run(parser.parse_args().dates)
