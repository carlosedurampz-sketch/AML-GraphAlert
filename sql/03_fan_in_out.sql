-- ============================================================================
-- 03_fan_in_out.sql — Fan-in / fan-out (cuentas mula / pass-through)
-- ----------------------------------------------------------------------------
-- Typology: a "mule" account receives money from many distinct senders
-- (fan-in) and forwards almost all of it onwards to a few receivers
-- (fan-out) within a short time. The account keeps little of what it
-- receives — its role is to move money, not to spend or save it.
--
-- Why weekly? Aggregating fan-in over the whole 90-day dataset is a
-- classic false-positive trap: over three months almost any active
-- account accumulates 10+ distinct senders. A mule is distinctive
-- because the fan-in is *concentrated in time* — many senders in one
-- week — so this query aggregates per account per calendar week
-- (DATE_TRUNC('week', timestamp)). In the synthetic data a normal
-- account averages ~1.6 distinct senders per week; a mule has 14.
--
-- Method (step by step):
--   1. weekly_in / weekly_out: per account per week, distinct
--      counterparties, number of transfers and total amount, split by
--      direction. Only SPEI transfers have a counterparty, so cash /
--      ATM / card rows are excluded.
--   2. profile: join both sides for the same account-week;
--      pass_through_ratio = MIN(received, sent) / MAX(received, sent).
--      A ratio near 1 means "almost everything that came in went out
--      again in the same week".
--   3. Flag account-weeks with:
--        fan-in  >= 8 distinct senders in the week,
--        total received >= MXN 50,000 in the week,
--        pass-through ratio >= 0.60.
--      Requiring the three conditions together is deliberate: a small
--      business paying many suppliers can have high fan-out, but rarely
--      combines high weekly fan-in with a high pass-through ratio.
--
-- Expected output columns: account_id, week_start, fan_in_distinct,
--   fan_in_n, total_received_mxn, fan_out_distinct, fan_out_n,
--   total_sent_mxn, pass_through_ratio, net_mxn
-- Runs on: DuckDB (pipeline), PostgreSQL. Tested with DuckDB.
-- ============================================================================

WITH weekly_in AS (
    -- Step 1a: SPEI money received, per account per week.
    SELECT
        account_id,
        DATE_TRUNC('week', timestamp) AS week_start,
        COUNT(DISTINCT counterparty_id) AS fan_in_distinct,
        COUNT(*) AS fan_in_n,
        SUM(amount_mxn) AS total_received_mxn
    FROM transactions
    WHERE channel = 'SPEI'
      AND direction = 'in'
      AND counterparty_id IS NOT NULL
      AND counterparty_id <> ''
    GROUP BY account_id, DATE_TRUNC('week', timestamp)
),
weekly_out AS (
    -- Step 1b: SPEI money sent, per account per week.
    SELECT
        account_id,
        DATE_TRUNC('week', timestamp) AS week_start,
        COUNT(DISTINCT counterparty_id) AS fan_out_distinct,
        COUNT(*) AS fan_out_n,
        SUM(amount_mxn) AS total_sent_mxn
    FROM transactions
    WHERE channel = 'SPEI'
      AND direction = 'out'
      AND counterparty_id IS NOT NULL
      AND counterparty_id <> ''
    GROUP BY account_id, DATE_TRUNC('week', timestamp)
),
profile AS (
    -- Step 2: combine both directions for the same account-week.
    SELECT
        COALESCE(i.account_id, o.account_id) AS account_id,
        COALESCE(i.week_start, o.week_start) AS week_start,
        COALESCE(i.fan_in_distinct, 0)  AS fan_in_distinct,
        COALESCE(i.fan_in_n, 0)         AS fan_in_n,
        COALESCE(i.total_received_mxn, 0) AS total_received_mxn,
        COALESCE(o.fan_out_distinct, 0) AS fan_out_distinct,
        COALESCE(o.fan_out_n, 0)        AS fan_out_n,
        COALESCE(o.total_sent_mxn, 0)   AS total_sent_mxn,
        CASE
            WHEN GREATEST(COALESCE(i.total_received_mxn, 0),
                          COALESCE(o.total_sent_mxn, 0)) = 0 THEN 0.0
            ELSE LEAST(COALESCE(i.total_received_mxn, 0),
                       COALESCE(o.total_sent_mxn, 0))
                 / GREATEST(COALESCE(i.total_received_mxn, 0),
                            COALESCE(o.total_sent_mxn, 0))
        END AS pass_through_ratio,
        COALESCE(i.total_received_mxn, 0) - COALESCE(o.total_sent_mxn, 0) AS net_mxn
    FROM weekly_in i
    FULL OUTER JOIN weekly_out o
        ON o.account_id = i.account_id
       AND o.week_start = i.week_start
)
SELECT
    account_id,
    week_start,
    fan_in_distinct,
    fan_in_n,
    ROUND(total_received_mxn, 2) AS total_received_mxn,
    fan_out_distinct,
    fan_out_n,
    ROUND(total_sent_mxn, 2) AS total_sent_mxn,
    ROUND(pass_through_ratio, 3) AS pass_through_ratio,
    ROUND(net_mxn, 2) AS net_mxn
FROM profile
WHERE total_received_mxn >= 50000      -- materiality floor (per week)
  AND pass_through_ratio >= 0.60       -- forwards most of what came in
  AND fan_in_distinct >= 8             -- many distinct senders, one week
ORDER BY total_received_mxn DESC, fan_in_distinct DESC;
