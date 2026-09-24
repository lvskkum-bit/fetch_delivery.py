import pytest

from phase3_mapping_contract import (
    MappingContractError,
    apply_weight_freshness,
    choose_primary_index,
    classify_index,
    extract_classification,
    resolve_memberships,
    validate_classification_50,
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
