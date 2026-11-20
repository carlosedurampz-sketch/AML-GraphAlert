"""
generate_synthetic.py — Synthetic transactional data (SPEI + cash), Mexico-flavoured.

Why synthetic?
    Real AML data is confidential and cannot be shared. This generator creates a
    realistic, labelled toy dataset so the SQL rules, the graph analysis and the
    ML scorer can be run end-to-end without any download. All amounts, names
    and account numbers are invented. Results on this data are ILLUSTRATIVE —
    they show that the pipeline works, not how it would perform on real data.

Planted typologies (ground truth, in data/synthetic/ground_truth.csv):
    * STRUCTURING — 6 accounts: many cash deposits of MXN 13,500–14,900
      (just below the illustrative MXN 15,000 reporting threshold) in 24 h–7 d.
    * MULE RING (fan-in / fan-out) — 8 accounts in 2 rings: each receives
      SPEI transfers from many distinct senders, then forwards the money
      onwards within hours (pass-through, low retained balance).
    * ROUND-TRIPPING — 1 cycle of 5 accounts: A -> B -> C -> D -> E -> A,
      similar amounts, repeated several times.
    * Normal accounts — ~800 accounts with payroll-like income, SPEI
      payments, cash withdrawals and occasional cash deposits.

Usage:
    python -m src.generate_synthetic --n-accounts 800 --n-days 90 --seed 42
    python src/generate_synthetic.py            # same, with defaults
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "synthetic"

# Illustrative threshold used across the project (see sql/README_SQL.md).
# It is a modelling choice for this portfolio, NOT a statement of Mexican law.
CASH_THRESHOLD_MXN = 15_000.0
START_DATE = "2026-01-01"
N_DAYS = 90

FIRST_NAMES = [
    "María", "José", "Juan", "Ana", "Luis", "Carmen", "Miguel", "Laura",
    "Carlos", "Sofía", "Diego", "Valeria", "Fernando", "Lucía", "Andrés",
    "Paola", "Ricardo", "Daniela", "Jorge", "Alejandra",
]
LAST_NAMES = [
    "Hernández", "García", "Martínez", "López", "González", "Pérez",
    "Sánchez", "Ramírez", "Torres", "Flores", "Rivera", "Gómez",
    "Díaz", "Morales", "Reyes", "Cruz", "Ortiz", "Chávez",
]
CITIES = ["CDMX", "Guadalajara", "Monterrey", "Puebla", "Tlaxcala", "Toluca", "Querétaro", "Mérida"]


def _rng(seed: int) -> tuple[random.Random, np.random.Generator]:
    return random.Random(seed), np.random.default_rng(seed)


def generate_accounts(n_normal: int, rng: random.Random) -> pd.DataFrame:
    """Normal (non-suspicious) accounts."""
    rows = []
    for i in range(n_normal):
        rows.append(
            {
                "account_id": f"CTA-{100000 + i:06d}",
                "customer_name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}",
                "city": rng.choice(CITIES),
                "account_type": rng.choices(["nómina", "ahorro", "cheques"], weights=[0.55, 0.30, 0.15])[0],
                # Monthly income drives the account's normal transaction scale.
                "monthly_income_mxn": round(float(rng.choice([8000, 12000, 18000, 25000, 35000, 50000])), 2),
                "opened_date": "2024-06-15",
                "is_suspicious": False,
                "typology": "",
            }
        )
    return pd.DataFrame(rows)


def _normal_transactions(accounts: pd.DataFrame, start: pd.Timestamp, n_days: int,
                         rng_np: np.random.Generator) -> list[dict]:
    """Payroll in, SPEI out, cash withdrawals, occasional cash deposits."""
    txs: list[dict] = []
    tx_id = 1
    account_ids = accounts["account_id"].tolist()
    incomes = dict(zip(accounts["account_id"], accounts["monthly_income_mxn"]))
    for day in range(n_days):
        date = start + pd.Timedelta(days=day)
        # Each account is active on ~35% of days.
        active = rng_np.choice(account_ids, size=int(len(account_ids) * 0.35), replace=False)
        for acc in active:
            income = incomes[acc]
            n_tx = int(rng_np.integers(1, 4))
            for _ in range(n_tx):
                kind = rng_np.choice(
                    ["spei_out", "spei_in", "cash_withdrawal", "cash_deposit", "card_payment"],
                    p=[0.35, 0.20, 0.20, 0.05, 0.20],
                )
                if kind == "spei_out":
                    amount = float(rng_np.lognormal(np.log(income * 0.08), 0.6))
                    counterparty = str(rng_np.choice(account_ids))
                    channel, direction = "SPEI", "out"
                elif kind == "spei_in":
                    amount = float(rng_np.lognormal(np.log(income * 0.10), 0.6))
                    counterparty = str(rng_np.choice(account_ids))
                    channel, direction = "SPEI", "in"
                elif kind == "cash_withdrawal":
                    amount = float(rng_np.uniform(500, min(8000, income * 0.4)))
                    counterparty, channel, direction = "", "ATM", "out"
                elif kind == "cash_deposit":
                    amount = float(rng_np.uniform(500, 9000))
                    counterparty, channel, direction = "", "ventanilla", "in"
                else:
                    amount = float(rng_np.uniform(100, 3000))
                    counterparty, channel, direction = "", "tarjeta", "out"
                ts = date + pd.Timedelta(hours=int(rng_np.integers(7, 20)),
                                          minutes=int(rng_np.integers(0, 60)))
                txs.append(
                    {
                        "tx_id": f"TX-{tx_id:07d}",
                        "timestamp": ts,
                        "account_id": acc,
                        "counterparty_id": counterparty,
                        "channel": channel,
                        "direction": direction,
                        "tx_type": kind,
                        "amount_mxn": round(min(amount, 60000), 2),
                        "currency": "MXN",
                        "is_suspicious": False,
                        "typology": "",
                    }
                )
                tx_id += 1
    return txs, tx_id


def _inject_structuring(txs: list[dict], tx_id: int, start: pd.Timestamp,
                        rng_np: np.random.Generator) -> tuple[list[dict], pd.DataFrame, int]:
    """6 accounts, each: 4-6 cash deposits of 13,500-14,900 MXN within 48 h."""
    accounts, gt = [], []
    for k in range(6):
        acc = f"CTA-90000{k}"
        accounts.append(
            {
                "account_id": acc, "customer_name": f"Cliente Estructuración {k + 1}",
                "city": "CDMX", "account_type": "ahorro",
                "monthly_income_mxn": 9000.0, "opened_date": "2025-11-01",
                "is_suspicious": True, "typology": "structuring",
            }
        )
        gt.append({"account_id": acc, "typology": "structuring",
                   "description": "Cash deposits just below MXN 15,000 in 24h-7d windows"})
        base = start + pd.Timedelta(days=int(rng_np.integers(10, 70)))
        for j in range(int(rng_np.integers(4, 7))):
            txs.append(
                {
                    "tx_id": f"TX-{tx_id:07d}",
                    "timestamp": base + pd.Timedelta(hours=int(rng_np.integers(0, 48))),
                    "account_id": acc, "counterparty_id": "", "channel": "ventanilla",
                    "direction": "in", "tx_type": "cash_deposit",
                    "amount_mxn": round(float(rng_np.uniform(13_500, 14_900)), 2),
                    "currency": "MXN", "is_suspicious": True, "typology": "structuring",
                }
            )
            tx_id += 1
        # A few normal-looking txs so the account is not trivially isolated.
        for j in range(3):
            txs.append(
                {
                    "tx_id": f"TX-{tx_id:07d}",
                    "timestamp": base + pd.Timedelta(days=int(rng_np.integers(0, 10))),
                    "account_id": acc, "counterparty_id": "", "channel": "ATM",
                    "direction": "out", "tx_type": "cash_withdrawal",
                    "amount_mxn": 1000.0, "currency": "MXN",
                    "is_suspicious": False, "typology": "",
                }
            )
            tx_id += 1
    return txs, pd.DataFrame(accounts), tx_id


def _inject_mules(txs: list[dict], tx_id: int, start: pd.Timestamp,
                  normal_ids: list[str], rng_np: np.random.Generator) -> tuple[list[dict], pd.DataFrame, int]:
    """2 rings of 4 mule accounts: many distinct senders -> mule -> few receivers, fast pass-through."""
    accounts, gt = [], []
    for ring in range(2):
        for k in range(4):
            acc = f"CTA-910{ring}{k}"
            accounts.append(
                {
                    "account_id": acc, "customer_name": f"Cuenta Mula R{ring + 1}-{k + 1}",
                    "city": "Guadalajara", "account_type": "ahorro",
                    "monthly_income_mxn": 7000.0, "opened_date": "2025-12-01",
                    "is_suspicious": True, "typology": "mule_fan_in_out",
                }
            )
            gt.append({"account_id": acc, "typology": "mule_fan_in_out",
                       "description": f"Fan-in from many senders, fan-out onwards (ring {ring + 1})"})
            base = start + pd.Timedelta(days=int(rng_np.integers(15, 60)))
            senders = list(rng_np.choice(normal_ids, size=14, replace=False))
            for s in senders:
                txs.append(
                    {
                        "tx_id": f"TX-{tx_id:07d}",
                        "timestamp": base + pd.Timedelta(hours=int(rng_np.integers(0, 24))),
                        "account_id": acc, "counterparty_id": str(s), "channel": "SPEI",
                        "direction": "in", "tx_type": "spei_in",
                        "amount_mxn": round(float(rng_np.uniform(4000, 12000)), 2),
                        "currency": "MXN", "is_suspicious": True, "typology": "mule_fan_in_out",
                    }
                )
                tx_id += 1
            # Forward ~90% onwards within a few hours, to 2 downstream accounts.
            for j in range(10):
                txs.append(
                    {
                        "tx_id": f"TX-{tx_id:07d}",
                        "timestamp": base + pd.Timedelta(hours=int(rng_np.integers(2, 30))),
                        "account_id": acc, "counterparty_id": f"CTA-920{ring}{j % 2}",
                        "channel": "SPEI", "direction": "out", "tx_type": "spei_out",
                        "amount_mxn": round(float(rng_np.uniform(5000, 11000)), 2),
                        "currency": "MXN", "is_suspicious": True, "typology": "mule_fan_in_out",
                    }
                )
                tx_id += 1
    # Downstream collector accounts (also suspicious, fan-in only).
    for ring in range(2):
        for k in range(2):
            acc = f"CTA-920{ring}{k}"
            accounts.append(
                {
                    "account_id": acc, "customer_name": f"Cuenta Colectora R{ring + 1}-{k + 1}",
                    "city": "Monterrey", "account_type": "cheques",
                    "monthly_income_mxn": 8000.0, "opened_date": "2025-12-01",
                    "is_suspicious": True, "typology": "mule_fan_in_out",
                }
            )
            gt.append({"account_id": acc, "typology": "mule_fan_in_out",
                       "description": "Collector receiving pass-through funds from mules"})
    return txs, pd.DataFrame(accounts), tx_id


def _inject_cycle(txs: list[dict], tx_id: int, start: pd.Timestamp,
                  rng_np: np.random.Generator) -> tuple[list[dict], pd.DataFrame, int]:
    """Round-tripping: 5 accounts in a closed cycle, repeated 4 times."""
    cycle = [f"CTA-9300{i}" for i in range(5)]
    accounts = pd.DataFrame([
        {"account_id": a, "customer_name": f"Cuenta Ciclo {i + 1}", "city": "CDMX",
         "account_type": "cheques", "monthly_income_mxn": 10000.0,
         "opened_date": "2025-10-01", "is_suspicious": True, "typology": "round_tripping"}
        for i, a in enumerate(cycle)
    ])
    gt = pd.DataFrame([
        {"account_id": a, "typology": "round_tripping",
         "description": "Closed transfer cycle A->B->C->D->E->A, repeated"}
        for a in cycle
    ])
    for rep in range(4):
        base = start + pd.Timedelta(days=int(rng_np.integers(20, 75)), hours=rep * 2)
        amount = round(float(rng_np.uniform(45000, 55000)), 2)
        for i, acc in enumerate(cycle):
            nxt = cycle[(i + 1) % len(cycle)]
            txs.append(
                {
                    "tx_id": f"TX-{tx_id:07d}",
                    "timestamp": base + pd.Timedelta(hours=i),
                    "account_id": acc, "counterparty_id": nxt, "channel": "SPEI",
                    "direction": "out", "tx_type": "spei_out",
                    "amount_mxn": amount, "currency": "MXN",
                    "is_suspicious": True, "typology": "round_tripping",
                }
            )
            tx_id += 1
    return txs, accounts, tx_id, gt


def generate(n_accounts: int = 800, n_days: int = N_DAYS, seed: int = 42,
             out_dir: Path = DATA_DIR) -> dict[str, Path]:
    rng, rng_np = _rng(seed)
    start = pd.Timestamp(START_DATE)
    normal = generate_accounts(n_accounts, rng)
    txs, tx_id = _normal_transactions(normal, start, n_days, rng_np)
    txs, struct_accts, tx_id = _inject_structuring(txs, tx_id, start, rng_np)
    txs, mule_accts, tx_id = _inject_mules(txs, tx_id, start, normal["account_id"].tolist(), rng_np)
    txs, cycle_accts, tx_id, cycle_gt = _inject_cycle(txs, tx_id, start, rng_np)

    accounts = pd.concat([normal, struct_accts, mule_accts, cycle_accts], ignore_index=True)
    transactions = pd.DataFrame(txs).sort_values("timestamp").reset_index(drop=True)
    # Re-number tx_id after sorting so the file is chronological.
    transactions["tx_id"] = [f"TX-{i + 1:07d}" for i in range(len(transactions))]

    gt_rows = []
    for _, r in pd.concat([struct_accts, mule_accts]).iterrows():
        gt_rows.append({"account_id": r["account_id"], "typology": r["typology"],
                        "description": "Planted typology (see generate_synthetic.py)"})
    gt_rows.extend(cycle_gt.to_dict("records"))
    ground_truth = pd.DataFrame(gt_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "accounts": out_dir / "accounts.csv",
        "transactions": out_dir / "transactions.csv",
        "ground_truth": out_dir / "ground_truth.csv",
    }
    accounts.to_csv(paths["accounts"], index=False)
    transactions.to_csv(paths["transactions"], index=False)
    ground_truth.to_csv(paths["ground_truth"], index=False)
    print(f"Accounts:      {len(accounts):,} ({ground_truth['account_id'].nunique()} suspicious planted)")
    print(f"Transactions:  {len(transactions):,}")
    print(f"Date range:    {transactions['timestamp'].min()} → {transactions['timestamp'].max()}")
    print(f"Suspicious tx: {int(transactions['is_suspicious'].sum()):,}")
    for p in paths.values():
        print(f"  wrote {p}")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic AML transactions (SPEI + cash).")
    ap.add_argument("--n-accounts", type=int, default=800)
    ap.add_argument("--n-days", type=int, default=N_DAYS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()
    generate(args.n_accounts, args.n_days, args.seed, args.out_dir)


if __name__ == "__main__":
    main()
