-- ============================================================================
-- 01_structuring.sql — Pitufeo / Structuring
-- ----------------------------------------------------------------------------
-- Typology: a customer splits a larger cash amount into several deposits
-- just below a reporting threshold, so no single deposit triggers a report.
-- Here the illustrative threshold is MXN 15,000 (a modelling choice for
-- this portfolio — NOT a statement of Mexican law; see README_SQL.md).
-- A deposit counts as "just below threshold" when it is between 85% and
-- 100% of the threshold: MXN 12,750 – 14,999.
--
-- What this query does (step by step — I am learning SQL, so each step
-- is commented):
--   1. deposits: keep only cash deposits in the near-threshold band.
--   2. windowed: for each deposit, count / sum the near-threshold deposits
--      of the same account in the preceding 24 hours and in the preceding
--      7 days. This uses a window function with a RANGE frame on the
--      timestamp, so the window slides with each transaction instead of
--      being fixed to calendar days.
--   3. Flag accounts with >= 3 near-threshold deposits in 24 h (or >= 4
--      in 7 days) whose combined amount exceeds the threshold — i.e. the
--      split only makes sense as evasion if the total would have been
--      reportable as one deposit.
--
-- Expected output columns: account_id, first_deposit, last_deposit,
--   n_deposits_24h, total_24h_mxn, n_deposits_7d, total_7d_mxn,
--   max_single_deposit_mxn
-- Runs on: DuckDB (used by the pipeline), PostgreSQL, BigQuery (with
--   minor INTERVAL syntax changes). Tested with DuckDB.
-- ============================================================================

WITH deposits AS (
    -- Step 1: cash deposits just below the threshold.
    SELECT
        account_id,
        timestamp,
        amount_mxn
    FROM transactions
    WHERE tx_type = 'cash_deposit'
      AND amount_mxn >= 0.85 * 15000   -- MXN 12,750
      AND amount_mxn <  15000          -- just below the threshold
),
windowed AS (
    -- Step 2: sliding counts / sums per account.
    -- RANGE ... PRECEDING looks back in *time*, not in a fixed number
    -- of rows — that is the difference between ROWS and RANGE frames.
    SELECT
        account_id,
        timestamp,
        amount_mxn,
        COUNT(*) OVER (
            PARTITION BY account_id
            ORDER BY timestamp
            RANGE BETWEEN INTERVAL 24 HOUR PRECEDING AND CURRENT ROW
        ) AS n_24h,
        SUM(amount_mxn) OVER (
            PARTITION BY account_id
            ORDER BY timestamp
            RANGE BETWEEN INTERVAL 24 HOUR PRECEDING AND CURRENT ROW
        ) AS total_24h_mxn,
        COUNT(*) OVER (
            PARTITION BY account_id
            ORDER BY timestamp
            RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND CURRENT ROW
        ) AS n_7d,
        SUM(amount_mxn) OVER (
            PARTITION BY account_id
            ORDER BY timestamp
            RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND CURRENT ROW
        ) AS total_7d_mxn
    FROM deposits
)
SELECT
    account_id,
    MIN(timestamp) AS first_deposit,
    MAX(timestamp) AS last_deposit,
    MAX(n_24h)     AS n_deposits_24h,
    MAX(total_24h_mxn) AS total_24h_mxn,
    MAX(n_7d)      AS n_deposits_7d,
    MAX(total_7d_mxn)  AS total_7d_mxn,
    MAX(amount_mxn)    AS max_single_deposit_mxn
FROM windowed
GROUP BY account_id
-- Step 3: the evasion pattern — several sub-threshold deposits whose
-- combined value crosses the threshold in a short window.
HAVING (MAX(n_24h) >= 3 AND MAX(total_24h_mxn) >= 15000)
    OR (MAX(n_7d)  >= 4 AND MAX(total_7d_mxn)  >= 15000)
ORDER BY total_7d_mxn DESC;
