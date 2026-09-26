import pytest

from phase3_mapping_contract import MappingContractError
from build_instrument_mapping import resolve_instrument_mapping


def constituents(count=50):
    return [
        {
            "symbol": f"SYM{i:02d}",
            "isin": f"INE{i:09d}",
            "company_name": f"Company {i}",
        }
        for i in range(count)
    ]


def instrument_master(count=50):
    return [
        {
            "segment": "NSE_EQ",
            "exchange": "NSE",
            "instrument_type": "EQ",
            "instrument_key": f"NSE_EQ|INE{i:09d}",
            "isin": f"INE{i:09d}",
            "trading_symbol": f"SYM{i:02d}",
            "exchange_token": str(1000 + i),
        }
        for i in range(count)
    ]


def test_resolves_exactly_50_nse_equity_instruments_by_isin_and_symbol():
    rows = resolve_instrument_mapping(
        constituents(), instrument_master(), "2026-09-25"
    )
    assert len(rows) == 50
    assert len({row["symbol"] for row in rows}) == 50
    assert len({row["instrument_key"] for row in rows}) == 50
    assert rows[0] == {
        "symbol": "SYM00",
        "isin": "INE000000000",
        "instrument_key": "NSE_EQ|INE000000000",
        "exchange_token": "1000",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "as_of_date": "2026-09-25",
        "verification_status": "VERIFIED",
    }


def test_rejects_symbol_mismatch_even_when_isin_matches():
    master = instrument_master()
    master[0]["trading_symbol"] = "WRONG"
    with pytest.raises(MappingContractError) as caught:
        resolve_instrument_mapping(constituents(), master, "2026-09-25")
    assert caught.value.code == "INSTRUMENT_SYMBOL_MISMATCH"


def test_rejects_duplicate_eligible_instrument_for_same_isin():
    master = instrument_master()
    master.append(dict(master[0], exchange_token="9999"))
    with pytest.raises(MappingContractError) as caught:
        resolve_instrument_mapping(constituents(), master, "2026-09-25")
    assert caught.value.code == "INSTRUMENT_DUPLICATE_MATCH"


def test_rejects_missing_instrument_and_non_eq_substitutes():
    master = instrument_master()
    master[0]["instrument_type"] = "BE"
    with pytest.raises(MappingContractError) as caught:
        resolve_instrument_mapping(constituents(), master, "2026-09-25")
    assert caught.value.code == "INSTRUMENT_MISSING"


def test_requires_50_unique_constituents():
    with pytest.raises(MappingContractError) as caught:
        resolve_instrument_mapping(
            constituents(49), instrument_master(49), "2026-09-25"
        )
    assert caught.value.code == "INSTRUMENT_CONSTITUENT_COUNT_50"
