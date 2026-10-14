from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from tbm_process.config import LAYER_CONFIG_KEYS, layer_config, load_config
from tbm_process.contracts.samples import DataContractError, validate_canonical_samples
from tbm_process.dynamics.registry import load_dynamic_parameter_columns
from tbm_process.ingestion.profiles import load_field_aliases


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_layer_config_exposes_only_owned_keys() -> None:
    config = load_config()
    quality = layer_config(config, "quality")
    process = layer_config(config, "process")
    assert set(quality) <= LAYER_CONFIG_KEYS["quality"]
    assert set(process) <= LAYER_CONFIG_KEYS["process"]
    assert "stable_window_seconds" not in quality
    assert "rpm_switch_timestamp" not in process


def test_sample_contract_reports_missing_columns() -> None:
    with pytest.raises(DataContractError, match="ingestion layer output"):
        validate_canonical_samples(pd.DataFrame({"time": []}))


def test_external_registries_preserve_current_inputs() -> None:
    aliases = load_field_aliases()
    parameters = load_dynamic_parameter_columns()
    assert "掘进状态" in aliases["raw_state"]
    assert parameters == {
        "advance_speed": "advance_speed",
        "thrust": "thrust",
        "torque": "torque",
        "rpm": "rpm_canonical",
    }


def test_layer_dependencies_remain_one_directional() -> None:
    allowed = {
        "ingestion": {"ingestion"},
        "quality": {"quality", "ingestion"},
        "process": {"process", "ingestion"},
        "dynamics": {"dynamics", "ingestion"},
        "facts": {"facts", "ingestion", "process"},
        "reporting": {"reporting", "contracts", "ingestion", "config"},
        "orchestration": {
            "orchestration",
            "contracts",
            "config",
            "dynamics",
            "facts",
            "ingestion",
            "process",
            "quality",
        },
        "outputs": {"outputs", "config", "ingestion"},
    }
    package_root = PROJECT_ROOT / "tbm_process"
    for layer, permitted in allowed.items():
        for path in (package_root / layer).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level == 0:
                    continue
                if node.level == 1:
                    target = layer
                else:
                    target = (node.module or "").split(".", maxsplit=1)[0]
                assert target in permitted, f"{path} imports disallowed layer {target}"


def test_removed_flat_implementation_modules_do_not_return() -> None:
    for name in (
        "io.py",
        "preprocessing.py",
        "segmentation.py",
        "dynamics.py",
        "semantics.py",
        "pipeline.py",
        "output.py",
        "report_semantics.py",
    ):
        assert not (PROJECT_ROOT / "tbm_process" / name).exists()
