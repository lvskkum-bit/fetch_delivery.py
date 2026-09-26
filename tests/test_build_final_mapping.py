import copy
import json
from pathlib import Path

import pytest

from build_final_mapping import build_final_mapping, publish_final_artifacts
from phase3_mapping_contract import MappingContractError, canonical_sha256


ROOT = Path(__file__).resolve().parents[1]


def load_inputs():
    return [
        json.loads((ROOT / path).read_text(encoding="utf-8"))
        for path in (
            "nifty50_latest.json",
            "phase3_artifacts/nifty50_classification_latest.json",
            "phase3_artifacts/nifty50_memberships_latest.json",
            "phase3_artifacts/nifty50_primary_sector_weights_latest.json",
            "phase3_artifacts/nifty50_instruments_latest.json",
        )
    ]


def test_combines_all_locked_inputs_into_50_of_50_mapping():
    mapping, audit = build_final_mapping(*load_inputs())
    assert mapping["schema_version"] == "nifty50-mapping-v2"
    assert mapping["status"] == "PASS"
    assert mapping["verification_as_of_date"] == "2026-09-25"
    assert mapping["weight_as_of_date"] == "2026-08-31"
    assert len(mapping["rows"]) == 50
    assert len({row["symbol"] for row in mapping["rows"]}) == 50
    assert len({row["isin"] for row in mapping["rows"]}) == 50
    assert all(row["primary_sector_index"] in row["all_membership_indices"] for row in mapping["rows"])
    assert all(row["instrument_key"] == f'NSE_EQ|{row["isin"]}' for row in mapping["rows"])
    assert audit["final_status"] == "PASS"
    assert audit["decision"] == "READY"
    assert audit["gate_counts"]["classifications"] == "50/50"
    assert audit["gate_counts"]["memberships"] == "50/50"
    assert audit["gate_counts"]["primary_weights"] == "50/50"
    assert audit["gate_counts"]["instruments"] == "50/50"
    assert audit["gate_counts"]["partial_rows"] == 0


def test_monthly_weight_date_is_not_rejected_as_daily_mismatch():
    mapping, _ = build_final_mapping(*load_inputs())
    assert {row["weight_as_of_date"] for row in mapping["rows"]} == {"2026-08-31"}
    assert {row["verification_status"] for row in mapping["rows"]} == {"VERIFIED"}


def test_final_mapping_normalizes_integral_floats_for_cross_runtime_checksum():
    mapping, _ = build_final_mapping(*load_inputs())

    def integral_floats(value):
        if isinstance(value, dict):
            return [item for child in value.values() for item in integral_floats(child)]
        if isinstance(value, list):
            return [item for child in value for item in integral_floats(child)]
        return [value] if isinstance(value, float) and value.is_integer() else []

    assert integral_floats(mapping) == []


@pytest.mark.parametrize("input_index", [1, 2, 3, 4])
def test_non_pass_component_fails_closed(input_index):
    inputs = load_inputs()
    inputs[input_index]["status"] = "WAIT"
    with pytest.raises(MappingContractError) as caught:
        build_final_mapping(*inputs)
    assert caught.value.code == "COMPONENT_NOT_READY"


def test_symbol_or_isin_mismatch_fails_closed():
    inputs = load_inputs()
    inputs[1]["rows"][0]["isin"] = "BROKEN"
    with pytest.raises(MappingContractError) as caught:
        build_final_mapping(*inputs)
    assert caught.value.code == "ROW_IDENTITY_MISMATCH"


def test_primary_index_must_be_an_applicable_index():
    inputs = load_inputs()
    inputs[3]["rows"][0]["primary_sector_index"] = "Not Applicable"
    inputs[3]["source_checksum"] = canonical_sha256(inputs[3])
    with pytest.raises(MappingContractError) as caught:
        build_final_mapping(*inputs)
    assert caught.value.code == "PRIMARY_MEMBERSHIP_MISMATCH"


def test_daily_verification_date_mismatch_fails_closed():
    inputs = load_inputs()
    inputs[4]["source_as_of_date"] = "2026-09-24"
    inputs[4]["source_checksum"] = canonical_sha256(inputs[4])
    with pytest.raises(MappingContractError) as caught:
        build_final_mapping(*inputs)
    assert caught.value.code == "DAILY_VERIFICATION_DATE_MISMATCH"


def test_added_removed_reconciliation_is_reported():
    inputs = load_inputs()
    baseline = {row["symbol"]: row for row in build_final_mapping(*inputs)[0]["rows"]}
    _, audit = build_final_mapping(*inputs, last_known_good=baseline)
    assert audit["transition_status"] == "NO_CHANGE"
    assert audit["added"] == []
    assert audit["removed"] == []


def test_publishes_exact_final_json_artifact_names(tmp_path):
    mapping, audit = publish_final_artifacts(*load_inputs(), output_dir=tmp_path)
    mapping_path = tmp_path / "nifty50_mapping_latest.json"
    audit_path = tmp_path / "phase3_mapping_audit_latest.json"
    assert mapping_path.exists()
    assert audit_path.exists()
    assert json.loads(mapping_path.read_text(encoding="utf-8")) == mapping
    assert json.loads(audit_path.read_text(encoding="utf-8")) == audit
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "nifty50_mapping_latest.json",
        "phase3_mapping_audit_latest.json",
    ]


def test_invalid_input_preserves_last_good_mapping_and_publishes_wait_audit(tmp_path):
    mapping_path = tmp_path / "nifty50_mapping_latest.json"
    sentinel = {"status": "PASS", "source_checksum": "LAST_KNOWN_GOOD"}
    mapping_path.write_text(json.dumps(sentinel), encoding="utf-8")
    inputs = load_inputs()
    inputs[4]["status"] = "WAIT"

    mapping, audit = publish_final_artifacts(*inputs, output_dir=tmp_path)

    assert mapping is None
    assert json.loads(mapping_path.read_text(encoding="utf-8")) == sentinel
    assert audit["final_status"] == "DATA_NOT_READY"
    assert audit["decision"] == "WAIT"
    assert audit["error_code"] == "COMPONENT_NOT_READY"
    assert audit["production_write"] is False
    assert json.loads(
        (tmp_path / "phase3_mapping_audit_latest.json").read_text(encoding="utf-8")
    ) == audit
