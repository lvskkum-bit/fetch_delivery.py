import copy
import json
from pathlib import Path

import pytest

import phase3_mapping_contract as source_contract
from build_final_mapping import build_final_mapping, publish_final_artifacts
from phase3_mapping_contract import (
    MappingContractError,
    SourceContractError,
    canonical_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
APPROVED_CALENDAR_EVIDENCE = {
    "verified": True,
    "expected_completed_trading_date": "2026-09-25",
    "evidence_ref": "approved-calendar-evidence-2026-09-25",
    "evidence_sha256": "a" * 64,
}


@pytest.fixture(autouse=True)
def trusted_calendar_resolver(monkeypatch):
    monkeypatch.setattr(
        source_contract,
        "_resolve_approved_calendar_evidence",
        lambda: dict(APPROVED_CALENDAR_EVIDENCE),
    )


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


def aligned_test_inputs():
    inputs = load_inputs()
    inputs[0]["status"] = "PASS"
    inputs[0]["source_date_provenance"].update(
        {
            "expected_completed_trading_date": inputs[0]["source_as_of_date"],
            "expected_date_method": "EXTERNAL_APPROVED_MARKET_CALENDAR",
            "expected_date_evidence_ref": "approved-calendar-evidence-2026-09-25",
            "expected_date_evidence_sha256": "a" * 64,
        }
    )
    inputs[0]["source_checksum"] = canonical_sha256(inputs[0])
    source_date = inputs[0]["source_as_of_date"]
    inputs[1]["source_checksum"] = canonical_sha256(inputs[1])
    inputs[2]["source_as_of_date"] = source_date
    inputs[2]["source_checksum"] = canonical_sha256(inputs[2])
    inputs[4]["constituent_source_as_of_date"] = source_date
    inputs[4]["source_checksum"] = canonical_sha256(inputs[4])
    return inputs


def build_test_mapping(inputs, **kwargs):
    return build_final_mapping(
        *inputs,
        **kwargs,
    )


def test_combines_all_locked_inputs_into_50_of_50_mapping():
    mapping, audit = build_test_mapping(aligned_test_inputs())
    assert mapping["schema_version"] == "nifty50-mapping-v2"
    assert mapping["status"] == "PASS"
    assert mapping["verification_as_of_date"] == "2026-09-25"
    assert mapping["weight_as_of_date"] == "2026-08-31"
    assert len(mapping["rows"]) == 50
    assert len({row["symbol"] for row in mapping["rows"]}) == 50
    assert len({row["isin"] for row in mapping["rows"]}) == 50
    assert mapping["count"] == 50
    assert mapping["production_write"] is False
    assert mapping["source_checksum"] == canonical_sha256(mapping)
    assert set(mapping["component_checksums"]) == {
        "nifty50",
        "classification",
        "memberships",
        "primary_weights",
        "instruments",
    }
    assert all(
        row["row_checksum"]
        == canonical_sha256(
            {key: value for key, value in row.items() if key != "row_checksum"},
            excluded_keys=(),
        )
        for row in mapping["rows"]
    )
    assert all(row["primary_sector_index"] in row["all_membership_indices"] for row in mapping["rows"])
    assert all(row["instrument_key"] == f'NSE_EQ|{row["isin"]}' for row in mapping["rows"])
    assert audit["final_status"] == "PASS"
    assert audit["decision"] == "READY"
    assert audit["gate_counts"]["classifications"] == "50/50"
    assert audit["gate_counts"]["memberships"] == "50/50"
    assert audit["gate_counts"]["primary_weights"] == "50/50"
    assert audit["gate_counts"]["instruments"] == "50/50"
    assert audit["gate_counts"]["duplicates"] == 0
    assert audit["gate_counts"]["partial_rows"] == 0
    assert audit["unchanged_count"] == 0


def test_monthly_weight_date_is_not_rejected_as_daily_mismatch():
    mapping, _ = build_test_mapping(aligned_test_inputs())
    assert {row["weight_as_of_date"] for row in mapping["rows"]} == {"2026-08-31"}
    assert {row["verification_status"] for row in mapping["rows"]} == {"VERIFIED"}


def test_final_mapping_normalizes_integral_floats_for_cross_runtime_checksum():
    mapping, _ = build_test_mapping(aligned_test_inputs())

    def integral_floats(value):
        if isinstance(value, dict):
            return [item for child in value.values() for item in integral_floats(child)]
        if isinstance(value, list):
            return [item for child in value for item in integral_floats(child)]
        return [value] if isinstance(value, float) and value.is_integer() else []

    assert integral_floats(mapping) == []


@pytest.mark.parametrize("input_index", [1, 2, 3, 4])
def test_non_pass_component_fails_closed(input_index):
    inputs = aligned_test_inputs()
    inputs[input_index]["status"] = "WAIT"
    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)
    assert caught.value.code == "COMPONENT_NOT_READY"


def test_symbol_or_isin_mismatch_fails_closed():
    inputs = aligned_test_inputs()
    inputs[1]["rows"][0]["isin"] = "BROKEN"
    inputs[1]["source_checksum"] = canonical_sha256(inputs[1])
    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)
    assert caught.value.code == "ROW_IDENTITY_MISMATCH"


def test_primary_index_must_be_an_applicable_index():
    inputs = aligned_test_inputs()
    inputs[3]["rows"][0]["primary_sector_index"] = "Not Applicable"
    inputs[3]["source_checksum"] = canonical_sha256(inputs[3])
    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)
    assert caught.value.code == "PRIMARY_MEMBERSHIP_MISMATCH"


def test_daily_verification_date_mismatch_fails_closed():
    inputs = aligned_test_inputs()
    inputs[4]["source_as_of_date"] = "2026-09-24"
    inputs[4]["source_checksum"] = canonical_sha256(inputs[4])
    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)
    assert caught.value.code == "DAILY_VERIFICATION_DATE_MISMATCH"


def test_stale_constituent_source_cannot_produce_ready_mapping():
    inputs = aligned_test_inputs()
    inputs[0]["source_as_of_date"] = "2026-09-24"
    inputs[0]["source_checksum"] = canonical_sha256(inputs[0])

    with pytest.raises(SourceContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "SOURCE_DATE_PROVENANCE"


@pytest.mark.parametrize(
    ("input_index", "field"),
    [
        (2, "source_as_of_date"),
        (4, "constituent_source_as_of_date"),
    ],
)
def test_daily_components_must_reference_current_constituent_source(
    input_index, field
):
    inputs = aligned_test_inputs()
    inputs[input_index][field] = "2026-09-24"
    if input_index == 4:
        inputs[input_index]["source_checksum"] = canonical_sha256(inputs[input_index])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "DAILY_VERIFICATION_DATE_MISMATCH"


@pytest.mark.parametrize("input_index", [1, 2])
def test_daily_component_requires_valid_canonical_checksum(input_index):
    inputs = aligned_test_inputs()
    inputs[input_index].pop("source_checksum")

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "COMPONENT_CHECKSUM_MISSING"


@pytest.mark.parametrize("input_index", [1, 2])
def test_daily_component_checksum_mutation_fails_closed(input_index):
    inputs = aligned_test_inputs()
    inputs[input_index]["source_checksum"] = "0" * 64

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "SOURCE_CHECKSUM_MISMATCH"


def test_instrument_row_must_match_daily_verification_date():
    inputs = aligned_test_inputs()
    inputs[4]["rows"][0]["as_of_date"] = "2026-09-24"
    inputs[4]["source_checksum"] = canonical_sha256(inputs[4])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "DAILY_VERIFICATION_DATE_MISMATCH"


@pytest.mark.parametrize("input_index", [1, 2, 3, 4])
def test_component_symbol_set_mismatch_fails_closed(input_index):
    inputs = aligned_test_inputs()
    inputs[input_index]["rows"].pop()
    if input_index in (1, 2, 3, 4):
        inputs[input_index]["source_checksum"] = canonical_sha256(inputs[input_index])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "COMPONENT_50_GATE"


def test_duplicate_component_symbols_fail_closed():
    inputs = aligned_test_inputs()
    inputs[1]["rows"][-1]["symbol"] = inputs[1]["rows"][0]["symbol"]
    inputs[1]["source_checksum"] = canonical_sha256(inputs[1])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "COMPONENT_50_GATE"


def test_partial_classification_row_fails_closed():
    inputs = aligned_test_inputs()
    inputs[1]["rows"][0]["sector"] = ""
    inputs[1]["source_checksum"] = canonical_sha256(inputs[1])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "PARTIAL_ROW"


def test_missing_primary_weight_fails_closed():
    inputs = aligned_test_inputs()
    inputs[3]["rows"][0]["primary_weight"] = None
    inputs[3]["source_checksum"] = canonical_sha256(inputs[3])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "PARTIAL_ROW"


def test_invalid_instrument_key_fails_closed():
    inputs = aligned_test_inputs()
    inputs[4]["rows"][0]["instrument_key"] = "NSE_EQ|BROKEN"
    inputs[4]["source_checksum"] = canonical_sha256(inputs[4])

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "INSTRUMENT_KEY_MISMATCH"


def test_stale_component_checksum_fails_closed():
    inputs = aligned_test_inputs()
    inputs[3]["rows"][0]["primary_weight"] += 1

    with pytest.raises(MappingContractError) as caught:
        build_test_mapping(inputs)

    assert caught.value.code == "SOURCE_CHECKSUM_MISMATCH"


def test_added_removed_reconciliation_is_reported():
    inputs = aligned_test_inputs()
    baseline = {row["symbol"]: row for row in build_test_mapping(inputs)[0]["rows"]}
    baseline["PREVIOUS_ONLY"] = baseline.pop("ADANIENT")
    _, audit = build_test_mapping(inputs, last_known_good=baseline)
    assert audit["transition_status"] == "ATOMIC_TRANSITION_READY"
    assert audit["added"] == ["ADANIENT"]
    assert audit["removed"] == ["PREVIOUS_ONLY"]
    assert audit["unchanged_count"] == 49


def test_publishes_exact_final_json_artifact_names(tmp_path):
    mapping, audit = publish_final_artifacts(
        *aligned_test_inputs(),
        output_dir=tmp_path,
    )
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
    inputs = aligned_test_inputs()
    inputs[4]["status"] = "WAIT"

    mapping, audit = publish_final_artifacts(
        *inputs,
        output_dir=tmp_path,
    )

    assert mapping is None
    assert json.loads(mapping_path.read_text(encoding="utf-8")) == sentinel
    assert audit["final_status"] == "DATA_NOT_READY"
    assert audit["decision"] == "WAIT"
    assert audit["error_code"] == "COMPONENT_NOT_READY"
    assert audit["production_write"] is False
    assert json.loads(
        (tmp_path / "phase3_mapping_audit_latest.json").read_text(encoding="utf-8")
    ) == audit


def test_current_stale_chain_publishes_wait_without_replacing_mapping(tmp_path):
    mapping_path = tmp_path / "nifty50_mapping_latest.json"
    sentinel = {"status": "PASS", "source_checksum": "LAST_KNOWN_GOOD"}
    mapping_path.write_text(json.dumps(sentinel), encoding="utf-8")

    mapping, audit = publish_final_artifacts(*load_inputs(), output_dir=tmp_path)

    assert mapping is None
    assert audit["final_status"] == "DATA_NOT_READY"
    assert audit["decision"] == "WAIT"
    assert audit["error_code"] == "SOURCE_STATUS"
    assert audit["constituent_source_as_of_date"] == "2026-09-25"
    assert audit["verification_as_of_date"] == "2026-09-25"
    assert audit["weight_as_of_date"] == "2026-08-31"
    assert audit["gate_counts"]["constituents"] == "50/50"
    assert audit["gate_counts"]["classifications"] == "50/50"
    assert audit["gate_counts"]["memberships"] == "50/50"
    assert audit["gate_counts"]["primary_weights"] == "50/50"
    assert audit["gate_counts"]["instruments"] == "50/50"
    assert audit["production_write"] is False
    assert json.loads(mapping_path.read_text(encoding="utf-8")) == sentinel


def test_invalid_source_contract_publishes_wait_audit(tmp_path):
    inputs = aligned_test_inputs()
    inputs[0]["source_as_of_date"] = "2026-09-24"
    inputs[0]["source_checksum"] = canonical_sha256(inputs[0])

    mapping, audit = publish_final_artifacts(
        *inputs,
        output_dir=tmp_path,
    )

    assert mapping is None
    assert audit["decision"] == "WAIT"
    assert audit["error_code"] == "SOURCE_DATE_PROVENANCE"
    assert audit["production_write"] is False


def test_malformed_source_publishes_wait_audit_without_crashing(tmp_path):
    inputs = aligned_test_inputs()
    inputs[0]["symbols"] = None

    mapping, audit = publish_final_artifacts(
        *inputs,
        output_dir=tmp_path,
    )

    assert mapping is None
    assert audit["decision"] == "WAIT"
    assert audit["error_code"] == "SYMBOLS_50"
