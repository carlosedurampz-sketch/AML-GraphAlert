# SQL rule engine — documented typologies

All rules run against a single table/view, `transactions`, with this schema
(see `data/synthetic/transactions.csv` for an example):

| column | type | meaning |
|---|---|---|
| `tx_id` | TEXT | unique transaction id |
| `timestamp` | TIMESTAMP | when it happened |
| `account_id` | TEXT | the account being scored |
| `counterparty_id` | TEXT | other party for SPEI, empty otherwise |
| `channel` | TEXT | `SPEI`, `ventanilla` (cash), `ATM`, `tarjeta` |
| `direction` | TEXT | `in` / `out` |
| `tx_type` | TEXT | `spei_in`, `spei_out`, `cash_deposit`, `cash_withdrawal`, `card_payment` |
| `amount_mxn` | DOUBLE | amount in Mexican pesos |

The pipeline (`src/run_pipeline.py`) loads the CSVs into DuckDB views and
executes each file in order. Each file is self-contained and returns
`account_id` plus rule-specific columns; the pipeline normalises those
into `outputs/rule_hits.csv` (`account_id, rule, detail`).

## Rules

### 01_structuring.sql — Pitufeo / structuring
Multiple cash deposits just below a threshold (here MXN 15,000,
illustrative) in 24 h–7 d windows. Key technique: **window functions with
a `RANGE BETWEEN INTERVAL … PRECEDING` frame**, so the window slides in
*time*, not in a fixed number of rows. Flag: ≥3 near-threshold deposits
in 24 h, or ≥4 in 7 days, with a combined amount ≥ the threshold.

### 02_profile_deviation.sql — Abrupt deviation from own profile
Compares the last 7 days against the account's **own** baseline (mean and
standard deviation of daily totals, excluding the last 7 days). Flag:
z-score > 3 or >5× the baseline mean, **and** recent 7-day total
> MXN 20,000 (materiality floor). Accounts with <10 baseline days are
excluded — no stable profile yet, and flagging them would be a classic
false-positive source.

### 03_fan_in_out.sql — Fan-in / fan-out (cuentas mula)
Many distinct SPEI senders → one account → few receivers, **in the same
week**, forwarding most of what arrived
(`pass_through_ratio = min(in, out) / max(in, out) ≥ 0.60`). Flag:
fan-in ≥ 8 distinct senders in one week, with total received
≥ MXN 50,000 that week. The weekly window matters: over 90 days almost
any active account accumulates 10+ distinct senders, so aggregating over
the whole period was a false-positive trap in an earlier version of this
rule (144 hits, mostly normal accounts). Requiring several conditions
together is deliberate: a small business paying many suppliers can have
high fan-out, but rarely combines high weekly fan-in with a high
pass-through ratio.

A fourth typology — **round-tripping cycles** — is not a SQL rule: closed
cycles are a graph property, so they are detected in
`src/graph_analysis.py` (`networkx.simple_cycles` on strongly connected
components) and added to the alert list as `round_tripping_cycle`.

## Thresholds — read this before reusing the numbers

- The MXN 15,000 cash threshold, the 85% band, the z-score of 3, and the
  MXN 20,000 / 50,000 floors are **modelling choices for this portfolio
  project**, chosen to make the typologies visible in synthetic data.
- They are **not** a statement of Mexican law or of any bank's policy
  (Ley Federal para la Prevención e Identificación de Operaciones con
  Recursos de Procedencia Ilícita — LFPIORPI — and CNBV/UIF rules set
  their own thresholds and reporting duties). In production these values
  come from the compliance area and are tuned on real alert history.
- If you adapt this to real data, tune the thresholds on labelled
  history and re-measure precision/recall — do not copy the numbers.

## Running the SQL alone

```bash
# DuckDB CLI (after generating the data)
duckdb -c "CREATE VIEW transactions AS SELECT * FROM read_csv_auto('data/synthetic/transactions.csv', parse_dates=['timestamp']);" \
       -c ".read sql/01_structuring.sql"
```

Or in Python: `duckdb.connect().execute(open('sql/01_structuring.sql').read())`
after creating the `transactions` view — exactly what `run_pipeline.py` does.
