"""
graph_analysis.py — Directed transfer graph: cycles, fan-in/fan-out, centrality.

The graph is built only from SPEI transfers (counterparty_id present).
Nodes = accounts, directed edge A -> B = money sent from A to B,
weight = total amount, n = number of transfers.

Functions are pure (they take a DataFrame and return DataFrames / a graph)
so they can be unit-tested without files — see tests/test_graph.py.
"""
from __future__ import annotations

import networkx as nx
import pandas as pd


def build_graph(transactions: pd.DataFrame) -> nx.DiGraph:
    """Aggregate SPEI transfers into a weighted directed graph."""
    spei = transactions[
        (transactions["channel"] == "SPEI")
        & (transactions["counterparty_id"].astype(str).str.strip() != "")
        & (transactions["counterparty_id"].notna())
    ].copy()
    # Keep only outgoing transfers to avoid double-counting the same
    # movement from both sides (each SPEI appears once as "out" here,
    # because the generator writes one row per account-side event).
    out = spei[spei["direction"] == "out"]
    if out.empty:
        out = spei  # fallback: use whatever SPEI rows exist
    agg = (
        out.groupby(["account_id", "counterparty_id"], as_index=False)
        .agg(total_amount_mxn=("amount_mxn", "sum"),
             n_transfers=("amount_mxn", "size"))
    )
    g = nx.DiGraph()
    for _, r in agg.iterrows():
        g.add_edge(r["account_id"], r["counterparty_id"],
                   weight=float(r["total_amount_mxn"]), n=int(r["n_transfers"]))
    # Keep isolated accounts as nodes so centrality covers the whole population.
    for acc in transactions["account_id"].unique():
        if acc not in g:
            g.add_node(acc)
    return g


def detect_cycles(graph: nx.DiGraph, max_cycle_len: int = 6,
                  max_cycles: int = 200) -> pd.DataFrame:
    """Closed transfer cycles (round-tripping), length 3..max_cycle_len.

    Why not plain ``nx.simple_cycles`` on the whole graph? Random SPEI
    traffic between ~800 accounts forms one giant strongly connected
    component, and enumerating *all* simple cycles there is exponential —
    the pipeline hung on it. So:
      * small graphs (<= 120 nodes): use ``simple_cycles`` directly;
      * large graphs: bounded depth-first search from each node, following
        only the strongest outgoing edges (top 12 by total amount — a
        round-tripping edge is normally a large, repeated transfer) and
        requiring the start node to be the smallest in the cycle, which
        de-duplicates rotations. Search is capped in steps and in cycles.
    """
    if len(graph) == 0:
        return pd.DataFrame(columns=["cycle", "cycle_length", "accounts"])
    if len(graph) <= 120:
        rows = []
        for cycle in nx.simple_cycles(graph):
            if len(cycle) > max_cycle_len or len(cycle) < 3:
                continue
            rows.append({"cycle": " -> ".join(cycle + [cycle[0]]),
                         "cycle_length": len(cycle), "accounts": ",".join(cycle)})
            if len(rows) >= max_cycles:
                break
        return pd.DataFrame(rows, columns=["cycle", "cycle_length", "accounts"])

    # Large graph: bounded DFS, strongest edges first.
    # Materiality filter: only aggregated edges >= MXN 10,000. Random
    # low-value traffic creates countless spurious short cycles; a
    # round-tripping ring moves large, repeated amounts, so its edges
    # survive the filter while most noise does not. (Documented heuristic,
    # not a universal rule — see README Limitations.)
    adj: dict[str, list[str]] = {}
    for node in graph.nodes:
        neighbours = sorted(
            (v for v in graph.successors(node)
             if graph[node][v].get("weight", 0) >= 10_000),
            key=lambda v: graph[node][v].get("weight", 0),
            reverse=True)[:12]
        adj[node] = neighbours
    rows, seen, steps = [], set(), [0]
    max_steps = 2_000_000

    def dfs(start: str, current: str, path: list[str]) -> None:
        if len(rows) >= max_cycles or steps[0] >= max_steps:
            return
        for nxt in adj.get(current, []):
            steps[0] += 1
            if steps[0] >= max_steps:
                return
            if nxt == start and len(path) >= 3:
                key = tuple(sorted(path))
                if key not in seen:
                    seen.add(key)
                    rows.append({"cycle": " -> ".join(path + [start]),
                                 "cycle_length": len(path),
                                 "accounts": ",".join(path)})
                continue
            # Only nodes larger than start (canonical rotation) and not
            # already in the path; depth limited to max_cycle_len.
            if nxt > start and nxt not in path and len(path) < max_cycle_len:
                dfs(start, nxt, path + [nxt])

    for start in sorted(graph.nodes):
        dfs(start, start, [start])
        if len(rows) >= max_cycles or steps[0] >= max_steps:
            break
    return pd.DataFrame(rows, columns=["cycle", "cycle_length", "accounts"])


def fan_in_out(transactions: pd.DataFrame, window_days: int = 7) -> pd.DataFrame:
    """Per-account fan-in / fan-out over the whole period.

    fan_in  = distinct senders, total received, n received
    fan_out = distinct receivers, total sent, n sent
    pass_through_ratio = min(in, out) / max(in, out): ~1 means the account
    forwards almost everything it receives (mule-like behaviour).
    """
    spei = transactions[transactions["channel"] == "SPEI"].copy()
    out = spei[spei["direction"] == "out"]
    inn = spei[spei["direction"] == "in"]
    out_agg = out.groupby("account_id").agg(
        fan_out_distinct=("counterparty_id", "nunique"),
        fan_out_n=("amount_mxn", "size"),
        total_sent_mxn=("amount_mxn", "sum")).reset_index()
    in_agg = inn.groupby("account_id").agg(
        fan_in_distinct=("counterparty_id", "nunique"),
        fan_in_n=("amount_mxn", "size"),
        total_received_mxn=("amount_mxn", "sum")).reset_index()
    df = pd.merge(in_agg, out_agg, on="account_id", how="outer").fillna(0)
    df["pass_through_ratio"] = df.apply(
        lambda r: (min(r["total_received_mxn"], r["total_sent_mxn"])
                   / max(r["total_received_mxn"], r["total_sent_mxn"]))
        if max(r["total_received_mxn"], r["total_sent_mxn"]) > 0 else 0.0, axis=1)
    df["net_mxn"] = df["total_received_mxn"] - df["total_sent_mxn"]
    return df.sort_values("fan_in_distinct", ascending=False)


def centrality(graph: nx.DiGraph) -> pd.DataFrame:
    """PageRank + betweenness (sampled, for speed) per account."""
    if len(graph) == 0:
        return pd.DataFrame(columns=["account_id", "pagerank", "betweenness"])
    pr = nx.pagerank(graph, weight="weight")
    # Betweenness on the full graph is O(n*m); sample 100 nodes as pivots.
    k = min(100, len(graph))
    bc = nx.betweenness_centrality(graph, k=k, weight="weight", seed=42) if len(graph) > 2 else {n: 0.0 for n in graph}
    df = pd.DataFrame({
        "account_id": list(pr.keys()),
        "pagerank": list(pr.values()),
        "betweenness": [bc.get(n, 0.0) for n in pr.keys()],
    })
    return df.sort_values("pagerank", ascending=False)


def analyse(transactions: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Run all graph analyses; returns dict of DataFrames."""
    graph = build_graph(transactions)
    cycles = detect_cycles(graph)
    fan = fan_in_out(transactions)
    cent = centrality(graph)
    print(f"Graph: {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges")
    print(f"Cycles (len<=6): {len(cycles)}")
    print(f"Max fan-in: {int(fan['fan_in_distinct'].max()) if len(fan) else 0} distinct senders")
    return {"graph": graph, "cycles": cycles, "fan_in_out": fan, "centrality": cent}
