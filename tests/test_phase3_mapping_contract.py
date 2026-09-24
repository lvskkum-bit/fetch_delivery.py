import pytest

from phase3_mapping_contract import (
    MappingContractError,
    extract_classification,
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

