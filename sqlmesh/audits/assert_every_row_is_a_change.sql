-- Every row of a history must be a change. "How long did the issue sit at safety class D"
-- is answered by the distance between rows, so a row that changed nothing invents a period
-- boundary that never happened.
--
-- The promise @temporal_join makes, checked on the finished table in its own terms:
-- `payload` lists the columns of *this* model, so the audit fails whether the macro let a
-- repeat through or the model's SELECT folded two rows into one. The first row of each item
-- is exempt, having no predecessor to differ from.
--
--   audits (assert_every_row_is_a_change(
--     key := issue_id, ts := valid_from, payload := (state, effort)
--   ))
AUDIT (
  name assert_every_row_is_a_change
);

SELECT *
FROM (
  SELECT
    @key AS __key,
    @ts AS __ts,
    LAG(@ts) OVER w AS __previous,
    @REDUCE(
      @EACH(@payload, c -> c IS DISTINCT FROM LAG(c) OVER w),
      (a, b) -> a OR b
    ) AS __changed
  FROM @this_model
  WINDOW w AS (PARTITION BY @key ORDER BY @ts)
) AS periods
WHERE NOT __changed AND NOT __previous IS NULL
