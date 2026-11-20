"""
run_pipeline.py — End-to-end pipeline: data -> SQL rules -> graph -> scoring -> outputs.

Steps:
    1. Generate synthetic data if data/synthetic/transactions.csv is missing
       (or --regenerate).
    2. Run the three SQL typologies with DuckDB (in-process, no server):
       sql/01_structuring.sql, sql/02_profile_deviation.sql,
       sql/03_fan_in_out.sql  -> one combined rule_hits table.
    3. Graph analysis (NetworkX): cycles, fan-in/out, PageRank, betweenness.
    4. Alert scoring (IsolationForest + rule evidence) -> risk-ranked alerts.
    5. Write outputs/ and an interactive suspicious-subgraph visualisation.

Usage:
    python -m src.run_pipeline [--regenerate] [--top-k 50]
    # or:  make run     /     bash run_all.sh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.alert_scoring import build_features, evaluate, score_alerts  # noqa: E402
from src.generate_synthetic import generate  # noqa: E402
from src.graph_analysis import analyse  # noqa: E402
from src.visualize import make_visualisations  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data" / "synthetic"
SQL_DIR = PROJECT_ROOT / "sql"
OUT_DIR = PROJECT_ROOT / "outputs"

RULE_FILES = [
    ("structuring", SQL_DIR / "01_structuring.sql"),
    ("profile_deviation", SQL_DIR / "02_profile_deviation.sql"),
    ("fan_in_out", SQL_DIR / "03_fan_in_out.sql"),
]


def run_sql_rules(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Execute each SQL file; normalise its output to (account_id, rule, detail)."""
    hits: list[pd.DataFrame] = []
    for rule_name, path in RULE_FILES:
        sql = path.read_text(encoding="utf-8")
        df = con.execute(sql).fetchdf()
        if df.empty:
            print(f"  [{rule_name}] 0 hits")
            continue
        # Each SQL file returns account_id + rule-specific columns; keep a
        # human-readable detail string and the rule name.
        detail_cols = [c for c in df.columns if c != "account_id"]
        df = df.copy()
        df["rule"] = rule_name
        df["detail"] = df[detail_cols].astype(str).agg(" | ".join, axis=1)
        hits.append(df[["account_id", "rule", "detail"]])
        print(f"  [{rule_name}] {len(df)} hits ({df['account_id'].nunique()} accounts)")
    if not hits:
        return pd.DataFrame(columns=["account_id", "rule", "detail"])
    return pd.concat(hits, ignore_index=True)


def main() -> dict:
    ap = argparse.ArgumentParser(description="Run the AML-GraphAlert pipeline end-to-end.")
    ap.add_argument("--regenerate", action="store_true", help="Regenerate synthetic data")
    ap.add_argument("--top-k", type=int, default=50, help="Top-k accounts for precision/recall")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tx_path = DATA_DIR / "transactions.csv"
    if args.regenerate or not tx_path.exists():
        print("== 1. Generating synthetic data ==")
        generate(out_dir=DATA_DIR)
    else:
        print("== 1. Using existing synthetic data ==")

    transactions = pd.read_csv(tx_path, parse_dates=["timestamp"])
    accounts = pd.read_csv(DATA_DIR / "accounts.csv")
    ground_truth = pd.read_csv(DATA_DIR / "ground_truth.csv")
    print(f"Loaded {len(transactions):,} transactions, {len(accounts):,} accounts")

    print("== 2. SQL rule engine (DuckDB) ==")
    con = duckdb.connect()
    # Register the already-parsed pandas DataFrames as DuckDB views.
    # (This also avoids CSV date-parsing differences between engines.)
    con.register("transactions", transactions)
    con.register("accounts", accounts)
    rule_hits = run_sql_rules(con)
    con.close()

    print("== 3. Graph analysis (NetworkX) ==")
    graph_results = analyse(transactions)
    cycles, fan, centrality_df = (graph_results["cycles"], graph_results["fan_in_out"],
                                  graph_results["centrality"])
    # Cycle membership is also a rule hit.
    if len(cycles):
        cycle_hits = pd.DataFrame({
            "account_id": sorted({a for row in cycles["accounts"] for a in str(row).split(",")}),
            "rule": "round_tripping_cycle",
            "detail": "Account belongs to a closed transfer cycle (see outputs/cycles.csv)",
        })
        rule_hits = pd.concat([rule_hits, cycle_hits], ignore_index=True)

    print("== 4. Alert scoring (IsolationForest + rule evidence) ==")
    features = build_features(transactions, rule_hits, fan, centrality_df, cycles)
    ranked = score_alerts(features)
    print(f"Scored {len(ranked):,} accounts; top risk_score = {ranked['risk_score'].max()}")

    # ---- Outputs ---------------------------------------------------------
    rule_hits.to_csv(OUT_DIR / "rule_hits.csv", index=False)
    ranked.to_csv(OUT_DIR / "alerts_ranked.csv", index=False)
    cycles.to_csv(OUT_DIR / "cycles.csv", index=False)
    fan.to_csv(OUT_DIR / "fan_in_out.csv", index=False)
    centrality_df.to_csv(OUT_DIR / "centrality.csv", index=False)

    metrics = evaluate(ranked, ground_truth, top_k=args.top_k)
    # Ranking quality at small k: the scorer puts the detectable planted
    # accounts first (ranks 1-19 are all planted in this run).
    planted = set(ground_truth["account_id"])
    metrics["ranking_at_k"] = {}
    for k in (10, 21):
        top = set(ranked.head(k)["account_id"])
        tp = len(top & planted)
        metrics["ranking_at_k"][f"top_{k}"] = {
            "true_positives": tp,
            "precision": round(tp / k, 3),
            "recall_planted": round(tp / len(planted), 3),
        }
    # False-positive reduction, illustrative: reviewing the top-21 ranked
    # accounts instead of the whole population, and the rule-tuning lesson
    # (v1 numbers were measured in an earlier run of this same pipeline,
    # before the fan-in rule was changed to a weekly window — see README).
    n_rule_alerts = int(ranked["n_rules_fired"].gt(0).sum())
    metrics["false_positive_reduction_illustrative"] = {
        "review_top_21_ranked_instead_of_all_accounts": {
            "accounts": int(len(ranked)), "reviewed": 21,
            "reduction_pct": round(100 * (1 - 21 / len(ranked)), 1),
            "recall_planted_at_21": metrics["ranking_at_k"]["top_21"]["recall_planted"],
        },
        "rule_tuning_lesson_fan_in_out": {
            "v1_whole_period_aggregation": {"fan_in_out_hits": 144,
                "all_rule_alerts": 156, "all_rule_precision": 0.09,
                "all_rule_recall": 0.609},
            "v2_weekly_window": {"fan_in_out_hits": 8,
                "all_rule_alerts": n_rule_alerts,
                "all_rule_precision": metrics["precision_recall_all_rule_alerts"]["precision"],
                "all_rule_recall": metrics["precision_recall_all_rule_alerts"]["recall_planted"]},
            "note": "The window choice, not the model, did most of the work. "
                    "v2 thresholds were tuned after seeing v1, so v2 precision "
                    "is optimistic — on real data both versions must be tuned "
                    "on labelled history, not on the test set.",
        },
        "note": "Illustrative, on synthetic data. Precision/recall are computed "
                "against planted labels; see README for the honest caveats.",
    }
    metrics["counts"] = {
        "n_accounts": int(len(accounts)),
        "n_transactions": int(len(transactions)),
        "n_rule_hits_rows": int(len(rule_hits)),
        "n_accounts_with_rule_hit": int(rule_hits["account_id"].nunique()) if len(rule_hits) else 0,
        "n_cycles_found": int(len(cycles)),
        "n_planted_suspicious": int(ground_truth["account_id"].nunique()),
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print("== 5. Visualisations ==")
    make_visualisations(transactions, graph_results["graph"], ranked, ground_truth,
                        OUT_DIR, cycles)
    print(json.dumps(metrics, indent=2))
    print(f"\nDone. Outputs in {OUT_DIR}/")
    return metrics


if __name__ == "__main__":
    main()
