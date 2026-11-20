"""
alert_scoring.py — Prioritise rule alerts so analysts review the worst first.

Approach (unsupervised, no labels needed at scoring time):
    1. Build one feature row per account from transactions + rule hits +
       graph metrics (fan-in/out, PageRank, betweenness, cycle membership).
    2. Fit an IsolationForest on those features. Accounts that look unlike
       the bulk of the population get a high anomaly score.
    3. Combine: risk_score (0-100) = 0.6 * anomaly + 0.4 * rule_evidence,
       where rule_evidence counts how many independent rules fired
       (structuring, profile deviation, fan-in/out, cycle).

The ground-truth labels in the synthetic data are used ONLY for evaluation
(precision@k, recall of planted accounts) in run_pipeline.py — never to
train the scorer. That mirrors a real compliance setting, where confirmed
laundering cases are rare and most alerts are unlabelled.

Usage:
    from src.alert_scoring import build_features, score_alerts
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "n_tx", "total_volume_mxn", "cash_share", "cash_deposit_n",
    "max_cash_deposit_mxn", "fan_in_distinct", "fan_out_distinct",
    "pass_through_ratio", "pagerank", "betweenness",
    "in_cycle", "n_rules_fired",
]


def build_features(transactions: pd.DataFrame, rule_hits: pd.DataFrame,
                   fan: pd.DataFrame, centrality_df: pd.DataFrame,
                   cycles: pd.DataFrame) -> pd.DataFrame:
    """One row per account with behavioural, rule and graph features."""
    tx = transactions.copy()
    tx["is_cash_deposit"] = (tx["tx_type"] == "cash_deposit").astype(int)
    base = tx.groupby("account_id").agg(
        n_tx=("amount_mxn", "size"),
        total_volume_mxn=("amount_mxn", "sum"),
        cash_deposit_n=("is_cash_deposit", "sum"),
        max_cash_deposit_mxn=("amount_mxn", lambda s: 0.0),  # replaced below
    ).reset_index()
    # Max cash deposit (0 if the account never deposited cash).
    cash = tx[tx["tx_type"] == "cash_deposit"].groupby("account_id")["amount_mxn"].max()
    base["max_cash_deposit_mxn"] = base["account_id"].map(cash).fillna(0.0)
    cash_vol = tx[tx["channel"] == "ventanilla"].groupby("account_id")["amount_mxn"].sum()
    base["cash_share"] = base["account_id"].map(cash_vol).fillna(0.0) / base["total_volume_mxn"].replace(0, np.nan)
    base["cash_share"] = base["cash_share"].fillna(0.0).clip(0, 1)

    # Rule hits: how many distinct rules fired per account.
    if rule_hits is not None and len(rule_hits):
        rules = (rule_hits.groupby("account_id")["rule"]
                 .nunique().rename("n_rules_fired").reset_index())
    else:
        rules = pd.DataFrame(columns=["account_id", "n_rules_fired"])
    df = base.merge(rules, on="account_id", how="left")
    df["n_rules_fired"] = df["n_rules_fired"].fillna(0).astype(int)

    df = df.merge(fan[["account_id", "fan_in_distinct", "fan_out_distinct",
                        "pass_through_ratio"]], on="account_id", how="left")
    df = df.merge(centrality_df[["account_id", "pagerank", "betweenness"]],
                  on="account_id", how="left")
    cycle_accounts = set()
    for accounts in cycles["accounts"].dropna():
        cycle_accounts.update(str(accounts).split(","))
    df["in_cycle"] = df["account_id"].isin(cycle_accounts).astype(int)
    df = df.fillna({"fan_in_distinct": 0, "fan_out_distinct": 0,
                    "pass_through_ratio": 0.0, "pagerank": 0.0, "betweenness": 0.0})
    return df


def score_alerts(features: pd.DataFrame, contamination: float = 0.05,
                 random_state: int = 42) -> pd.DataFrame:
    """IsolationForest anomaly score + rule evidence -> risk_score 0-100."""
    X = features[FEATURES].astype(float).copy()
    X = X.replace([np.inf, -np.inf], 0).fillna(0)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model = IsolationForest(contamination=contamination, random_state=random_state,
                            n_estimators=200)
    model.fit(Xs)
    # decision_function: higher = more normal. Invert and rescale to 0-100.
    raw = -model.decision_function(Xs)
    anomaly = 100 * (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)
    rule_evidence = 100 * features["n_rules_fired"].clip(0, 4) / 4
    out = features.copy()
    out["anomaly_score"] = np.round(anomaly, 1)
    out["rule_evidence"] = np.round(rule_evidence, 1)
    out["risk_score"] = np.round(0.6 * anomaly + 0.4 * rule_evidence, 1)
    out["is_anomaly"] = model.predict(Xs) == -1
    out = out.sort_values("risk_score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def evaluate(ranked: pd.DataFrame, ground_truth: pd.DataFrame,
             top_k: int = 50) -> dict:
    """Illustrative evaluation against planted labels (synthetic data only)."""
    planted = set(ground_truth["account_id"])
    top = set(ranked.head(top_k)["account_id"])
    flagged = set(ranked[ranked["n_rules_fired"] > 0]["account_id"])
    def _pr(found: set, total_flagged: int) -> dict:
        tp = len(found & planted)
        return {
            "true_positives": tp,
            "flagged": total_flagged,
            "precision": round(tp / total_flagged, 3) if total_flagged else 0.0,
            "recall_planted": round(tp / len(planted), 3) if planted else 0.0,
        }
    return {
        "planted_accounts": len(planted),
        f"precision_recall_top_{top_k}": _pr(top, top_k),
        "precision_recall_all_rule_alerts": _pr(flagged, len(flagged)),
    }
