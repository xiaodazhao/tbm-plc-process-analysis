from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_VERSION = "tbm_process_layered_v1_2026-10-13"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_PLC_DIR = (
    Path.home()
    / "Library/CloudStorage/GoogleDrive-xiaodazhao0608@gmail.com/我的云端硬盘/TBM9/TBM9_2023"
)


LAYER_CONFIG_KEYS = {
    "quality": frozenset(
        {
            "large_gap_seconds",
            "maximum_sample_duration_seconds",
            "minimum_speed_upper_bound",
            "penetration_reference_relative_error_warning",
            "quality_distance_ratio_warning",
            "rpm_analysis_upper_bound",
            "rpm_mapping_evidence",
            "rpm_mapping_version",
            "rpm_motor_ratio_reference",
            "rpm_relative_error_warning",
            "rpm_switch_timestamp",
            "speed_mad_multiplier",
            "speed_setpoint_multiplier",
        }
    ),
    "process": frozenset(
        {
            "advancing_rpm_min",
            "advancing_speed_min",
            "advancing_thrust_min",
            "advancing_torque_min",
            "bridge_advance_gap_seconds",
            "cycle_split_min_loaded_samples",
            "cycle_split_min_loaded_seconds",
            "cycle_split_relative_level_fraction",
            "cycle_split_relative_signal_count",
            "cycle_split_relative_unloaded_seconds",
            "cycle_split_reload_ratio",
            "cycle_split_unloaded_seconds",
            "directional_bridge_gap_seconds",
            "knee_minimum_distance_seconds",
            "knee_snap_seconds",
            "kneedle_minimum_prominence",
            "maximum_sample_duration_seconds",
            "minimum_advance_interval_samples",
            "minimum_advance_interval_seconds",
            "phase_segmentation_method_version",
            "phase_signal_interpolation_max_seconds",
            "phase_smoothing_max_seconds",
            "phase_smoothing_min_seconds",
            "plateau_boundary_smoothing_seconds",
            "plateau_confirmation_samples",
            "plateau_entry_level_fraction",
            "plateau_maximum_future_gain",
            "rising_minimum_relative_gain",
            "rising_reference_shoulder_samples",
            "rising_start_level_fraction",
            "rising_start_minimum_samples",
            "score_kneedle_smoothing_fraction",
            "stable_bridge_gap_seconds",
            "stable_minimum_activity_level",
            "stable_minimum_occupancy",
            "stable_minimum_samples",
            "stable_minimum_seconds",
            "stable_normalized_slope_per_minute_max",
            "stable_normalized_std_max",
            "stable_occupancy_window_seconds",
            "stable_required_signal_count",
            "stable_window_minimum_samples",
            "stable_window_seconds",
            "terminal_decline_max_gap_seconds",
            "terminal_stop_max_gap_seconds",
            "transition_minimum_seconds",
            "transition_minimum_signals",
            "transition_window_seconds",
        }
    ),
    "dynamics": frozenset(
        {
            "coupled_event_minimum_overlap_seconds",
            "daily_construction_event_limit",
            "daily_coupled_event_limit",
            "daily_semantic_segment_limit",
            "dynamic_window_seconds",
            "event_min_changing_parameters",
            "event_min_relative_change",
            "fluctuation_moderate_ratio",
            "fluctuation_residual_ratio",
            "local_outlier_robust_z",
            "maximum_sample_duration_seconds",
            "minimum_dynamic_samples",
            "minimum_event_seconds",
            "semantic_selection_min_seconds",
            "trend_min_relative_change",
            "trend_min_spearman_abs",
        }
    ),
    "orchestration": frozenset(
        {
            "cumulative_step_jump_m",
            "daily_construction_event_limit",
            "daily_semantic_segment_limit",
            "maximum_sample_duration_seconds",
            "quality_distance_ratio_warning",
            "schema_version",
            "terminal_stop_max_gap_seconds",
            "top_process_count",
            "transition_context_max_seconds",
            "transition_context_rpm_min",
            "transition_context_torque_min",
        }
    ),
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    return json.loads(config_path.read_text(encoding="utf-8"))


def layer_config(config: dict[str, Any], layer: str) -> dict[str, Any]:
    """Return only the configuration keys owned by one processing layer."""
    try:
        allowed = LAYER_CONFIG_KEYS[layer]
    except KeyError as exc:
        raise ValueError(f"Unknown configuration layer: {layer}") from exc
    return {key: config[key] for key in allowed if key in config}

