"""Phase 4 producer parity: no stale symbols, partial publish or invented dates."""
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fetch_delivery


def current_roster(*, status="PASS", omit=None, fetched_at=None):
    symbols = ["BSE"] + [f"SYM{i:02d}" for i in range(49)]
    if omit:
        symbols.remove(omit)
    return {
        "source": "NSE Indices — NIFTY 50 Index Constituent",
        "source_url": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "status": status,
        "count": len(symbols),
        "symbols": symbols,
        "constituents": [
            {"symbol": s, "company_name": s, "industry": "Test",
             "series": "EQ", "isin": "INE000000000"} for s in symbols
        ],
        "fetched_at_utc": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def candle_csv(symbols, *, omit=None):
    body = ["SYMBOL,SERIES,DELIV_QTY,DELIV_PER"]
    for i, s in enumerate(symbols):
        if s != omit:
            body.append(f"{s},EQ,{200+i},60.0")
    return "\n".join(body) + "\n"


class ProducerParityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.previous)
        self.addCleanup(self.tmp.cleanup)
        self.roster = current_roster()
        self.session_days = (date(2026, 10, 9), date(2026, 10, 8))

    def write_roster(self, roster=None):
        Path("nifty50_latest.json").write_text(
            json.dumps(self.roster if roster is None else roster), encoding="utf8"
        )

    def sessions(self, *, omit=None):
        first, second = self.session_days
        symbols = self.roster["symbols"]
        return (first, candle_csv(symbols, omit=omit)), (second, candle_csv(symbols))

    def test_exact_live_roster_contains_bse_not_wipro(self):
        self.write_roster()
        with patch.object(fetch_delivery, "find_latest_two_sessions",
                          return_value=self.sessions()):
            fetch_delivery.main()
        data = json.loads(Path("delivery_latest.json").read_text())
        symbols = [row["symbol"] for row in data["rows"]]
        self.assertEqual(50, len(symbols))
        self.assertIn("BSE", symbols)
        self.assertNotIn("WIPRO", symbols)
        self.assertEqual(self.roster["symbols"].__len__(), data["pass"])
        self.assertEqual(self.session_days[0].isoformat(), data["currentTradingDate"])
        self.assertRegex(data["constituent_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(data["constituent_symbols"], sorted(self.roster["symbols"]))

    def test_missing_bse_fails_before_mutating_cache(self):
        self.write_roster()
        Path("delivery_latest.json").write_text("ORIGINAL", encoding="utf8")
        with patch.object(fetch_delivery, "find_latest_two_sessions",
                          return_value=self.sessions(omit="BSE")):
            with self.assertRaises(RuntimeError):
                fetch_delivery.main()
        self.assertEqual("ORIGINAL", Path("delivery_latest.json").read_text())

    def test_missing_or_invalid_roster_fails_closed(self):
        Path("delivery_latest.json").write_text("ORIGINAL", encoding="utf8")
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            fetch_delivery.main()
        self.assertEqual("ORIGINAL", Path("delivery_latest.json").read_text())
        self.write_roster(current_roster(status="WAIT"))
        with self.assertRaises(RuntimeError):
            fetch_delivery.main()
        self.assertEqual("ORIGINAL", Path("delivery_latest.json").read_text())

    def test_incomplete_membership_and_duplicate_rows_fail_closed(self):
        bad = current_roster(omit="SYM48")
        self.write_roster(bad)
        with self.assertRaises(RuntimeError):
            fetch_delivery.main()
        bad = current_roster()
        bad["symbols"][2] = bad["symbols"][1]
        self.write_roster(bad)
        with self.assertRaises(RuntimeError):
            fetch_delivery.main()
        self.assertFalse(Path("delivery_latest.json").exists())

    def test_membership_fetch_age_is_bounded(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.write_roster(current_roster(fetched_at=old))
        with self.assertRaises(RuntimeError):
            fetch_delivery.main()
        self.assertFalse(Path("delivery_latest.json").exists())

    def test_duplicate_equity_rows_are_rejected(self):
        self.write_roster()
        (day1, text1), (day2, text2) = self.sessions()
        text1 += "BSE,EQ,300,65\n"
        with patch.object(fetch_delivery, "find_latest_two_sessions",
                          return_value=((day1, text1), (day2, text2))):
            with self.assertRaises(RuntimeError):
                fetch_delivery.main()
        self.assertFalse(Path("delivery_latest.json").exists())

    def test_zero_previous_quantity_does_not_publish_unusable_ratio(self):
        self.write_roster()
        (day1, text1), (day2, text2) = self.sessions()
        text2 = text2.replace("BSE,EQ,200,60.0", "BSE,EQ,0,60.0")
        with patch.object(fetch_delivery, "find_latest_two_sessions",
                          return_value=((day1, text1), (day2, text2))):
            with self.assertRaises(RuntimeError):
                fetch_delivery.main()
        self.assertFalse(Path("delivery_latest.json").exists())

    def test_embedded_date_must_match_requested_nse_file_date(self):
        good = "SYMBOL, SERIES, DATE1, DELIV_QTY\nBSE,EQ,09-Oct-2026,200\n"
        # The guard needs >=50 actual source rows, not just an NSE-looking header.
        real = ("SYMBOL, SERIES, DATE1, DELIV_QTY\n" +
                "".join(f"{s},EQ,09-Oct-2026,200\n" for s in self.roster["symbols"]))
        self.assertFalse(fetch_delivery.p4_valid_bhavdata_embedded_date_(
            good, date(2026, 10, 9)))
        self.assertTrue(fetch_delivery.p4_valid_bhavdata_embedded_date_(
            real, date(2026, 10, 9)))
        self.assertFalse(fetch_delivery.p4_valid_bhavdata_embedded_date_(
            real, date(2026, 10, 11)))

    def test_sunday_nse_200_is_never_a_regular_trading_session(self):
        with patch.object(fetch_delivery.requests, "get") as get:
            self.assertIsNone(fetch_delivery.fetch_day(date(2026, 10, 11)))
            get.assert_not_called()

    def test_wrong_date_mirror_never_qualifies_on_weekday(self):
        fake = ("SYMBOL, SERIES, DATE1, DELIV_QTY\n" +
                "".join(f"{s},EQ,09-Oct-2026,200\n" for s in self.roster["symbols"]))
        response = type("Response", (), {
            "status_code": 200, "text": fake, "content": fake.encode("utf8")
        })()
        with patch.object(fetch_delivery.requests, "get", return_value=response):
            self.assertIsNone(fetch_delivery.fetch_day(date(2026, 10, 8)))

    def test_workflow_first_fetches_roster_then_delivery_then_commit(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/delivery.yml").read_text()
        self.assertLess(workflow.index("python fetch_nifty50_constituents.py"),
                        workflow.index("python fetch_delivery.py"))
        self.assertLess(workflow.index("python fetch_delivery.py"),
                        workflow.index("git add delivery_latest.json nifty50_latest.json"))


if __name__ == "__main__":
    unittest.main()
