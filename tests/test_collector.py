"""Tests for the collector rule (src/graph_analysis.detect_collectors).

A collector receives funds from mule accounts that were already flagged
by the fan-in/out rule. The rule fires at >= 2 distinct flagged mule
senders (see detect_collectors docstring for the rationale).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph_analysis import detect_collectors


def _spei_out_tx(sender: str, receiver: str, amount: float = 8000.0) -> dict:
    return {
        "tx_id": "TX-1", "timestamp": pd.Timestamp("2026-02-01 10:00:00"),
        "account_id": sender, "counterparty_id": receiver, "channel": "SPEI",
        "direction": "out", "tx_type": "spei_out", "amount_mxn": amount,
        "currency": "MXN",
    }


def _spei_in_tx(receiver: str, sender: str, amount: float = 8000.0) -> dict:
    row = _spei_out_tx(sender, receiver, amount)
    row.update({"account_id": receiver, "counterparty_id": sender,
                "direction": "in", "tx_type": "spei_in"})
    return row


def test_collector_detected_from_two_flagged_mules() -> None:
    """Receiving from 2 distinct flagged mules flags the account."""
    rows = [
        _spei_out_tx("MULE1", "COLLECTOR"),
        _spei_out_tx("MULE2", "COLLECTOR"),
        # Same pair written as the receiver-side ("in") row must also count.
        _spei_in_tx("COLLECTOR", "MULE1", 5000.0),
    ]
    collectors = detect_collectors(pd.DataFrame(rows), {"MULE1", "MULE2"})
    assert "COLLECTOR" in set(collectors["account_id"])
    row = collectors[collectors["account_id"] == "COLLECTOR"].iloc[0]
    assert row["n_flagged_mule_senders"] == 2
    assert set(row["flagged_mule_senders"].split(",")) == {"MULE1", "MULE2"}
    assert row["total_received_from_flagged_mules_mxn"] == 21000.0


def test_single_flagged_mule_sender_not_a_collector() -> None:
    """One flagged mule sender is not enough — it can be a coincidence."""
    rows = [_spei_out_tx("MULE1", "RECEIVER") for _ in range(5)]
    collectors = detect_collectors(pd.DataFrame(rows), {"MULE1", "MULE2"})
    assert "RECEIVER" not in set(collectors["account_id"])


def test_unflagged_senders_do_not_count() -> None:
    """Senders that were never flagged as mules do not trigger the rule."""
    rows = [_spei_out_tx(f"SENDER{i}", "RECEIVER") for i in range(4)]
    collectors = detect_collectors(pd.DataFrame(rows), {"MULE1", "MULE2"})
    assert len(collectors) == 0


def test_flagged_mule_itself_is_not_a_collector() -> None:
    """A flagged mule receiving from another flagged mule is not a collector."""
    rows = [_spei_out_tx("MULE1", "MULE2"), _spei_out_tx("MULE2", "MULE1")]
    collectors = detect_collectors(pd.DataFrame(rows), {"MULE1", "MULE2"})
    assert len(collectors) == 0


def test_empty_mule_set_returns_empty() -> None:
    """With no flagged mules there is nothing to detect."""
    rows = [_spei_out_tx("A", "B")]
    collectors = detect_collectors(pd.DataFrame(rows), set())
    assert len(collectors) == 0
