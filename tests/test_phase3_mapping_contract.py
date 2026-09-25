import pytest

from phase3_mapping_contract import (
    MappingContractError,
    apply_weight_freshness,
    choose_primary_index,
    classify_index,
    extract_factsheet_weight_evidence,
    extract_classification,
    choose_primary_index_from_weight_evidence,
    resolve_memberships,
    validate_classification_50,
    validate_memberships_50,
    validate_primary_weights_50,
)


def source_row(index=0):
    return {
        "symbol": f"SYM{index:02d}",
        "company_name": f"Company {index}",
        "macro_sector": "Financial Services",
        "series": "EQ",
        "isin": f"INE{index:09d}",
    }


def quote_payload(index=0):
    return {
        "info": {"symbol": f"SYM{index:02d}"},
        "industryInfo": {
            "sector": "Financial Services",
            "industry": "Financial Services",
            "basicIndustry": "Private Sector Bank",
        },
    }


def valid_rows():
    return [extract_classification(source_row(i), quote_payload(i)) for i in range(50)]


def test_extracts_official_four_tier_classification():
    result = extract_classification(source_row(), quote_payload())
    assert result == {
        "symbol": "SYM00",
        "macro_sector": "Financial Services",
        "sector": "Financial Services",
        "industry": "Financial Services",
        "basic_industry": "Private Sector Bank",
    }


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("macro_sector", "CLASSIFICATION_MACRO_SECTOR"),
        ("sector", "CLASSIFICATION_SECTOR"),
        ("industry", "CLASSIFICATION_INDUSTRY"),
        ("basic_industry", "CLASSIFICATION_BASIC_INDUSTRY"),
    ],
)
def test_blank_classification_level_is_rejected(field, code):
    rows = valid_rows()
    rows[0][field] = ""
    with pytest.raises(MappingContractError) as caught:
        validate_classification_50(rows, {f"SYM{i:02d}" for i in range(50)})
    assert caught.value.code == code


def test_symbol_mismatch_is_rejected():
    with pytest.raises(MappingContractError) as caught:
        extract_classification(source_row(), quote_payload(1))
    assert caught.value.code == "CLASSIFICATION_SYMBOL_MISMATCH"


@pytest.mark.parametrize("count", [49, 51])
def test_classification_requires_exactly_50_rows(count):
    rows = valid_rows()
    if count == 49:
        rows = rows[:-1]
    else:
        rows.append(
            extract_classification(source_row(50), quote_payload(50))
        )
    with pytest.raises(MappingContractError) as caught:
        validate_classification_50(rows, {f"SYM{i:02d}" for i in range(50)})
    assert caught.value.code == "CLASSIFICATION_COUNT_50"


def test_duplicate_classification_symbol_is_rejected():
    rows = valid_rows()
    rows[-1] = dict(rows[-1], symbol="SYM00")
    with pytest.raises(MappingContractError) as caught:
        validate_classification_50(rows, {f"SYM{i:02d}" for i in range(50)})
    assert caught.value.code == "CLASSIFICATION_SYMBOL_UNIQUE"


def test_html_or_error_payload_is_rejected():
    with pytest.raises(MappingContractError) as caught:
        extract_classification(source_row(), "<html>blocked</html>")
    assert caught.value.code == "CLASSIFICATION_RESPONSE_TYPE"


RULES = {
    "eligible_types": ["SECTORAL", "THEMATIC"],
    "excluded_types": ["BROAD_MARKET", "STRATEGY", "DEBT", "COMMODITY"],
    "excluded_names": [
        "NIFTY SHARIAH 25",
        "NIFTY50 SHARIAH",
        "NIFTY500 SHARIAH",
        "NIFTY100 ESG",
        "NIFTY100 ENHANCED ESG",
        "NIFTY100 ESG SECTOR LEADERS",
    ],
    "type_priority": {"SECTORAL": 2, "THEMATIC": 1},
}


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"name": "NIFTY AUTO", "index_type": "SECTORAL"}, "ELIGIBLE_SECTORAL"),
        ({"name": "NIFTY INDIA DEFENCE", "index_type": "THEMATIC"}, "ELIGIBLE_THEMATIC"),
        ({"name": "NIFTY 50", "index_type": "BROAD_MARKET"}, "EXCLUDED"),
        ({"name": "NIFTY ALPHA 50", "index_type": "STRATEGY"}, "EXCLUDED"),
        ({"name": "NIFTY SDL", "index_type": "DEBT"}, "EXCLUDED"),
        ({"name": "NIFTY100 ESG", "index_type": "THEMATIC"}, "EXCLUDED"),
        ({"name": "NIFTY50 SHARIAH", "index_type": "THEMATIC"}, "EXCLUDED"),
    ],
)
def test_index_eligibility_is_explicit(row, expected):
    assert classify_index(row, RULES) == expected


def test_memberships_are_intersection_of_active_eligible_constituents():
    active = [
        {"name": "NIFTY AUTO", "index_type": "SECTORAL"},
        {"name": "NIFTY INDIA DEFENCE", "index_type": "THEMATIC"},
        {"name": "NIFTY 50", "index_type": "BROAD_MARKET"},
    ]
    sets = {
        "NIFTY AUTO": {"M&M", "MARUTI"},
        "NIFTY INDIA DEFENCE": {"BEL"},
        "NIFTY 50": {"M&M", "MARUTI", "BEL"},
    }
    assert resolve_memberships("M&M", active, sets, RULES) == ["NIFTY AUTO"]


def test_empty_eligible_membership_requires_review():
    with pytest.raises(MappingContractError) as caught:
        resolve_memberships(
            "XYZ",
            [{"name": "NIFTY 50", "index_type": "BROAD_MARKET"}],
            {"NIFTY 50": {"XYZ"}},
            RULES,
        )
    assert caught.value.code == "MEMBERSHIP_REVIEW_REQUIRED"


def test_membership_audit_requires_exactly_50_unique_covered_symbols():
    rows = [
        {"symbol": f"SYM{i:02d}", "all_applicable_indices": ["NIFTY TEST"]}
        for i in range(50)
    ]
    assert validate_memberships_50(
        rows, {f"SYM{i:02d}" for i in range(50)}
    ) == {"status": "PASS", "count": 50}


def test_membership_audit_rejects_blank_applicable_indices():
    rows = [
        {"symbol": f"SYM{i:02d}", "all_applicable_indices": ["NIFTY TEST"]}
        for i in range(50)
    ]
    rows[7]["all_applicable_indices"] = []
    with pytest.raises(MappingContractError) as caught:
        validate_memberships_50(rows, {f"SYM{i:02d}" for i in range(50)})
    assert caught.value.code == "MEMBERSHIP_REVIEW_REQUIRED"


def test_primary_uses_highest_weight_and_ignores_nifty50():
    result = choose_primary_index(
        ["NIFTY 50", "NIFTY BANK", "NIFTY FINANCIAL SERVICES"],
        {
            "NIFTY 50": {"HDFCBANK": 12.0},
            "NIFTY BANK": {"HDFCBANK": 28.0},
            "NIFTY FINANCIAL SERVICES": {"HDFCBANK": 32.0},
        },
        {
            "NIFTY 50": {"index_type": "BROAD_MARKET", "specificity_rank": 0},
            "NIFTY BANK": {"index_type": "SECTORAL", "specificity_rank": 1},
            "NIFTY FINANCIAL SERVICES": {"index_type": "SECTORAL", "specificity_rank": 2},
        },
        "HDFCBANK",
        RULES,
    )
    assert result["primary_sector_index"] == "NIFTY FINANCIAL SERVICES"
    assert result["primary_weight"] == 32.0


def test_primary_tie_prefers_sectoral_then_specificity():
    result = choose_primary_index(
        ["THEME", "SECTOR", "SPECIFIC"],
        {name: {"ABC": 5.0} for name in ["THEME", "SECTOR", "SPECIFIC"]},
        {
            "THEME": {"index_type": "THEMATIC", "specificity_rank": 9},
            "SECTOR": {"index_type": "SECTORAL", "specificity_rank": 1},
            "SPECIFIC": {"index_type": "SECTORAL", "specificity_rank": 2},
        },
        "ABC",
        RULES,
    )
    assert result["primary_sector_index"] == "SPECIFIC"


def test_existing_stock_keeps_last_known_good_when_weight_stale():
    result = apply_weight_freshness(
        current={"weight_as_of_month": "2026-08", "weights": {}},
        last_known_good={"primary_sector_index": "NIFTY AUTO", "primary_weight": 18.5},
        is_new=False,
        expected_month="2026-09",
    )
    assert result["verification_status"] == "WEIGHT_SOURCE_STALE"
    assert result["primary_sector_index"] == "NIFTY AUTO"


def test_new_stock_with_stale_weight_is_wait():
    result = apply_weight_freshness(
        current={"weight_as_of_month": "2026-08", "weights": {}},
        last_known_good=None,
        is_new=True,
        expected_month="2026-09",
    )
    assert result["verification_status"] == "REVIEW_REQUIRED + WAIT"
    assert result["primary_sector_index"] == ""


def test_extracts_august_factsheet_weights_and_top_ten_floor():
    text = """
    August 31, 2026
    Top constituents by weightage
    Company’s Name                         Weight(%)
    HDFC Bank Ltd.                              17.02
    ICICI Bank Ltd.                             14.86
    Axis Bank Ltd.                               9.20
    Bank of Baroda                               3.48
    ## Based on Price Return Index.
    """
    result = extract_factsheet_weight_evidence(
        text,
        {"HDFCBANK": "HDFC Bank Ltd.", "AXISBANK": "Axis Bank Ltd."},
    )
    assert result == {
        "as_of_date": "2026-08-31",
        "verified_weights": {"HDFCBANK": 17.02, "AXISBANK": 9.2},
        "unlisted_weight_upper_bound": 3.48,
    }


def test_extracts_weight_when_sector_table_shares_the_same_line():
    text = """
    August 31, 2026
    Top constituents by weightage
    Construction 10.49                   Reliance Industries Ltd. 9.91
    Power 9.99                           Bharti Airtel Ltd. 9.68
    NTPC Ltd. 2.73
    ## Based on Price Return Index.
    """
    result = extract_factsheet_weight_evidence(
        text,
        {"RELIANCE": "Reliance Industries Ltd.", "BHARTIARTL": "Bharti Airtel Ltd."},
    )
    assert result["verified_weights"] == {"RELIANCE": 9.91, "BHARTIARTL": 9.68}
    assert result["unlisted_weight_upper_bound"] == 2.73


def test_primary_is_verified_when_exact_winner_beats_every_unknown_upper_bound():
    result = choose_primary_index_from_weight_evidence(
        symbol="ABC",
        memberships=["Nifty Sector", "Nifty Theme", "Nifty Other"],
        evidence={
            "Nifty Sector": {"verified_weights": {"ABC": 12.5}, "unlisted_weight_upper_bound": 4.0},
            "Nifty Theme": {"verified_weights": {}, "unlisted_weight_upper_bound": 8.0},
            "Nifty Other": {"verified_weights": {"ABC": 7.0}, "unlisted_weight_upper_bound": 3.0},
        },
    )
    assert result["primary_sector_index"] == "Nifty Sector"
    assert result["primary_weight"] == 12.5
    assert result["verification_status"] == "VERIFIED"


def test_primary_waits_when_unknown_upper_bound_can_equal_or_beat_winner():
    with pytest.raises(MappingContractError) as caught:
        choose_primary_index_from_weight_evidence(
            symbol="ABC",
            memberships=["Nifty Sector", "Nifty Theme"],
            evidence={
                "Nifty Sector": {"verified_weights": {"ABC": 8.0}, "unlisted_weight_upper_bound": 4.0},
                "Nifty Theme": {"verified_weights": {}, "unlisted_weight_upper_bound": 8.0},
            },
        )
    assert caught.value.code == "WEIGHT_REVIEW_REQUIRED"


def test_primary_weight_audit_requires_50_unique_verified_rows():
    rows = [
        {
            "symbol": f"SYM{i:02d}",
            "primary_sector_index": "Nifty Test",
            "primary_weight": 1.0,
            "verification_status": "VERIFIED",
        }
        for i in range(50)
    ]
    assert validate_primary_weights_50(
        rows, {f"SYM{i:02d}" for i in range(50)}
    ) == {"status": "PASS", "count": 50}


def test_primary_weight_audit_rejects_wait_or_blank_rows():
    rows = [
        {
            "symbol": f"SYM{i:02d}",
            "primary_sector_index": "Nifty Test",
            "primary_weight": 1.0,
            "verification_status": "VERIFIED",
        }
        for i in range(50)
    ]
    rows[3]["verification_status"] = "REVIEW_REQUIRED + WAIT"
    with pytest.raises(MappingContractError) as caught:
        validate_primary_weights_50(rows, {f"SYM{i:02d}" for i in range(50)})
    assert caught.value.code == "PRIMARY_WEIGHT_NOT_VERIFIED"
