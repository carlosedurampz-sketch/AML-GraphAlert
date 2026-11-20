-- ============================================================================
-- 02_profile_deviation.sql — Abrupt deviation from the customer's own profile
-- ----------------------------------------------------------------------------
-- Typology: an account whose recent activity is suddenly far above its own
-- history — e.g. a low-income account that starts moving large SPEI
-- volumes. The comparison is against the *customer's own* baseline, not
-- against a global average, so a genuinely high-volume business is not
-- flagged just for being large.
--
-- Method (step by step):
--   1. daily_totals: total amount moved per account per calendar day.
--   2. baseline: for each account, the mean and standard deviation of its
--      daily totals in the "history" period — everything except the last
--      7 days of the dataset. Accounts with fewer than 10 baseline days
--      are excluded (no stable profile yet — a common false-positive
--      source, so we say so explicitly instead of flagging them).
--   3. recent: total and daily average over the last 7 days.
--   4. Flag when BOTH hold:
--        a) recent daily average > baseline mean + 3 * baseline std
--           (a z-score above 3 — a standard outlier cut), OR the recent
--           daily average is more than 5x the baseline mean (covers
--           accounts whose baseline std is ~0 because they were dormant);
--        b) recent total > MXN 20,000 (a materiality floor, so a tiny
--           account doubling from MXN 100 to MXN 200 does not create
--           an alert a human has to review).
--
-- Expected output columns: account_id, baseline_days, baseline_daily_mean_mxn,
--   baseline_daily_std_mxn, recent_7d_total_mxn, recent_daily_avg_mxn,
--   z_score, ratio_vs_baseline
-- Runs on: DuckDB (pipeline), PostgreSQL. Tested with DuckDB.
-- ============================================================================

WITH daily_totals AS (
    -- Step 1: one row per account per day.
    SELECT
        account_id,
        DATE_TRUNC('day', timestamp) AS day,
        SUM(amount_mxn) AS daily_total_mxn
    FROM transactions
    GROUP BY account_id, DATE_TRUNC('day', timestamp)
),
bounds AS (
    -- The last 7 days of the data are the "recent" window.
    SELECT MAX(day) AS max_day FROM daily_totals
),
baseline AS (
    -- Step 2: mean / std of daily totals, excluding the recent window.
    SELECT
        d.account_id,
        COUNT(*) AS baseline_days,
        AVG(d.daily_total_mxn) AS baseline_daily_mean_mxn,
        STDDEV(d.daily_total_mxn) AS baseline_daily_std_mxn
    FROM daily_totals d
    CROSS JOIN bounds b
    WHERE d.day <= b.max_day - INTERVAL 7 DAY
    GROUP BY d.account_id
    HAVING COUNT(*) >= 10          -- need a stable profile
       AND AVG(d.daily_total_mxn) > 0
),
recent AS (
    -- Step 3: activity in the last 7 days.
    SELECT
        d.account_id,
        SUM(d.daily_total_mxn) AS recent_7d_total_mxn,
        AVG(d.daily_total_mxn) AS recent_daily_avg_mxn
    FROM daily_totals d
    CROSS JOIN bounds b
    WHERE d.day > b.max_day - INTERVAL 7 DAY
    GROUP BY d.account_id
)
SELECT
    r.account_id,
    b.baseline_days,
    ROUND(b.baseline_daily_mean_mxn, 2) AS baseline_daily_mean_mxn,
    ROUND(COALESCE(b.baseline_daily_std_mxn, 0), 2) AS baseline_daily_std_mxn,
    ROUND(r.recent_7d_total_mxn, 2) AS recent_7d_total_mxn,
    ROUND(r.recent_daily_avg_mxn, 2) AS recent_daily_avg_mxn,
    ROUND(
        (r.recent_daily_avg_mxn - b.baseline_daily_mean_mxn)
        / NULLIF(b.baseline_daily_std_mxn, 0), 2
    ) AS z_score,
    ROUND(r.recent_daily_avg_mxn / NULLIF(b.baseline_daily_mean_mxn, 0), 2)
        AS ratio_vs_baseline
FROM recent r
JOIN baseline b ON b.account_id = r.account_id
WHERE r.recent_7d_total_mxn > 20000   -- materiality floor
  AND (
        -- z-score > 3 (needs a non-zero std), or 5x the baseline mean
        (b.baseline_daily_std_mxn > 0
         AND r.recent_daily_avg_mxn > b.baseline_daily_mean_mxn
                                       + 3 * b.baseline_daily_std_mxn)
        OR r.recent_daily_avg_mxn > 5 * b.baseline_daily_mean_mxn
      )
ORDER BY ratio_vs_baseline DESC;
