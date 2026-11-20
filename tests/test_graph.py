"""Tests for the graph analysis (src/graph_analysis.py)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph_analysis import build_graph, detect_cycles, fan_in_out


def _spei_tx(sender: str, receiver: str, amount: float = 10000.0) -> dict:
    return {
        "tx_id": "TX-1", "timestamp": pd.Timestamp("2026-02-01 10:00:00"),
        "account_id": sender, "counterparty_id": receiver, "channel": "SPEI",
        "direction": "out", "tx_type": "spei_out", "amount_mxn": amount,
        "currency": "MXN",
    }


def _spei_in_tx(receiver: str, sender: str, amount: float = 10000.0) -> dict:
    row = _spei_tx(sender, receiver, amount)
    row.update({"account_id": receiver, "counterparty_id": sender,
                "direction": "in", "tx_type": "spei_in"})
    return row


def test_cycle_detected():
    """A -> B -> C -> A must be found as a closed cycle."""
    rows = []
    for a, b in [("A", "B"), ("B", "C"), ("C", "A")]:
        rows.append(_spei_tx(a, b))
        rows.append(_spei_in_tx(b, a))
    graph = build_graph(pd.DataFrame(rows))
    cycles = detect_cycles(graph)
    assert len(cycles) >= 1
    assert set(cycles.iloc[0]["accounts"].split(",")) == {"A", "B", "C"}


def test_no_cycle_in_chain():
    """A -> B -> C (no return edge) must produce no cycle."""
    rows = []
    for a, b in [("A", "B"), ("B", "C")]:
        rows.append(_spei_tx(a, b))
        rows.append(_spei_in_tx(b, a))
    graph = build_graph(pd.DataFrame(rows))
    cycles = detect_cycles(graph)
    assert len(cycles) == 0


def test_fan_in_counts_distinct_senders():
    """An account receiving from 12 distinct senders has fan_in_distinct == 12."""
    rows = [_spei_in_tx("MULE", f"S{i:02d}") for i in range(12)]
    rows += [_spei_tx("MULE", "OUT1", 5000.0), _spei_tx("MULE", "OUT2", 5000.0)]
    fan = fan_in_out(pd.DataFrame(rows))
    row = fan[fan["account_id"] == "MULE"].iloc[0]
    assert row["fan_in_distinct"] == 12
    assert row["fan_out_distinct"] == 2
