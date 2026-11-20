"""Tests for the structuring rule (sql/01_structuring.sql).

The SQL is executed for real with DuckDB against a small in-memory
transactions table — the test checks the *query*, not a Python re-write.
"""
from pathlib import Path

import duckdb
import pandas as pd
import pytest

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def _run_sql(transactions: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect()
    con.register("transactions", transactions)
    sql = (SQL_DIR / "01_structuring.sql").read_text(encoding="utf-8")
    result = con.execute(sql).fetchdf()
    con.close()
    return result


def _tx(account: str, amounts: list[float],
        start: str = "2026-02-01 10:00:00") -> pd.DataFrame:
    base = pd.Timestamp(start)
    rows = [{
        "tx_id": f"TX-{i}", "timestamp": base + pd.Timedelta(hours=2 * i),
        "account_id": account, "counterparty_id": "", "channel": "ventanilla",
        "direction": "in", "tx_type": "cash_deposit",
        "amount_mxn": a, "currency": "MXN",
    } for i, a in enumerate(amounts)]
    return pd.DataFrame(rows)


def test_structuring_detected():
    """4 deposits of ~14,500 MXN within 24 h must be flagged."""
    tx = _tx("CTA-TEST01", [14500, 14200, 14800, 13900])
    result = _run_sql(tx)
    assert "CTA-TEST01" in set(result["account_id"])
    row = result[result["account_id"] == "CTA-TEST01"].iloc[0]
    assert row["n_deposits_24h"] == 4
    assert row["total_24h_mxn"] == pytest.approx(57400.0)


def test_single_large_deposit_not_flagged():
    """One deposit just below the threshold is not structuring."""
    tx = _tx("CTA-TEST02", [14900])
    result = _run_sql(tx)
    assert "CTA-TEST02" not in set(result["account_id"])


def test_small_deposits_not_flagged():
    """Deposits well below the band (e.g. 5,000 MXN) must not be flagged,
    even if there are several of them."""
    tx = _tx("CTA-TEST03", [5000, 5000, 5000, 5000])
    result = _run_sql(tx)
    assert "CTA-TEST03" not in set(result["account_id"])
