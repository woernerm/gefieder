"""@temporal_join — join two change histories on the union of their timestamps.

One ASOF join follows one history and misses the other's changes; this follows both,
exposing ``tck`` (every timestamp at which either side may have changed, per item),
``lhs`` (the left row in effect at that tick) and ``rhs`` (the right row in effect there,
looked up through that left row).

Typical usage example:

    SELECT tck.issue_id, tck.changed_at, lhs.state, rhs.safety_class
    FROM @temporal_join(
      lhs_ts := bronze_project_a.issue_history.changed_at,
      rhs_ts := bronze_project_a.component_history.changed_at,
      key    := issue_id,
      on     := (rhs.component_id = lhs.component_id)
    )

Each lookup takes one row per tick, so both sources must be unique on (key, timestamp)
and on (join columns, timestamp). The tick list over-collects, so every column of both
*sources*, timestamps aside, is compared with the tick before and only a change survives;
a SELECT folding two columns into one can still show identical rows, which the example's
audits catch.
"""

from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from sqlmesh.core.macros import MacroEvaluator, RuntimeStage, macro
from sqlmesh.utils import columns_to_types_all_known
from sqlmesh.utils.errors import SQLMeshError

TICKS, LEFT, RIGHT = "tck", "lhs", "rhs"
"""The aliases a calling model selects from.

Fixed rather than configurable: a name that changed with the call site would only make
the models harder to read.
"""

DUCKDB_GATEWAY = "duckdb"
"""The gateway whose engine has an ASOF JOIN, named as config.py names it.

A model that asks for it gets the join; every other gateway gets the same lookup written
out by hand.
"""


def _table_and_column(argument: str, value: exp.Expression) -> tuple[exp.Table, exp.Identifier]:
    """Split a ``schema.table.column`` reference into its table and column.

    Args:
        argument: Name of the macro argument, for the error message.
        value: The reference to split.

    Returns:
        The table and the column identifier.

    Raises:
        SQLMeshError: The value is not a qualified column.
    """
    if not isinstance(value, exp.Column) or not value.args.get("table"):
        raise SQLMeshError(
            f"@temporal_join: '{argument}' must be a qualified column such as "
            f"schema.table.column, got '{value.sql()}'."
        )
    args = value.args
    table = exp.Table(this=args["table"], db=args.get("db"), catalog=args.get("catalog"))
    return table, value.this


def _value_columns(
    evaluator: MacroEvaluator, table: exp.Table, timestamp: exp.Identifier
) -> list[str]:
    """Every column of a table except its timestamp — what "unchanged" is judged on.

    Args:
        evaluator: The macro evaluator, for the upstream schemas and the dialect.
        table: The table to read the columns of.
        timestamp: The timestamp column to leave out.

    Returns:
        The column names, or an empty list while SQLMesh is still loading and has not
        read the upstream schemas.

    Raises:
        SQLMeshError: The columns are unknown outside the loading stage.
    """
    columns_to_types = evaluator.columns_to_types(table)
    if not columns_to_types_all_known(columns_to_types):
        if evaluator.runtime_stage != RuntimeStage.LOADING.value:
            raise SQLMeshError(f"@temporal_join: the columns of '{table.sql()}' are unknown.")
        # Asking for the schemas is what keeps this render out of the cache, so the real
        # columns arrive before anything is executed.
        return []
    excluded = normalize_identifiers(timestamp, dialect=evaluator.dialect).name
    return [c for c in columns_to_types if c != excluded]


@macro()
def temporal_join(
    evaluator: MacroEvaluator,
    lhs_ts: exp.Expression,
    rhs_ts: exp.Expression,
    key: exp.Expression,
    on: exp.Expression,
) -> exp.Expression:
    """Build the FROM clause joining two change histories; see the module docstring.

    Args:
        evaluator: The macro evaluator, which also settles the gateway and dialect.
        lhs_ts: Qualified timestamp column of the left table, the spine.
        rhs_ts: Qualified timestamp column of the right table.
        key: Unqualified column of the left table identifying the item.
        on: The predicate joining the right table to the left one.

    Returns:
        The FROM clause, exposing the tck, lhs and rhs aliases.

    Raises:
        SQLMeshError: An argument is malformed, or a model on the duckdb gateway did not
            declare ``dialect duckdb``.
    """
    lhs_table, lhs_column = _table_and_column("lhs_ts", lhs_ts)
    rhs_table, rhs_column = _table_and_column("rhs_ts", rhs_ts)
    if not isinstance(key, exp.Column) or key.args.get("table"):
        raise SQLMeshError(
            f"@temporal_join: 'key' must be an unqualified column of the left table, "
            f"got '{key.sql()}'."
        )

    # SQLMesh scopes a model's variables to the gateway its MODEL block names, so this is
    # the engine that will run the query, settled once at load so every render agrees.
    dialect = evaluator.dialect
    asof = evaluator.gateway == DUCKDB_GATEWAY
    if asof and dialect != "duckdb":
        raise SQLMeshError(
            f"@temporal_join: a model on the '{DUCKDB_GATEWAY}' gateway must declare "
            "`dialect duckdb`. ASOF JOIN is DuckDB grammar, and any other dialect reads "
            "the word as a table alias instead of rejecting it."
        )

    def name(identifier: str | exp.Identifier) -> str:
        # Quoted throughout, because a source column may be named like a keyword
        # ("offset"); normalized first so quoting cannot change what it means.
        identifier = normalize_identifiers(exp.to_identifier(identifier), dialect=dialect)
        identifier.set("quoted", True)
        return identifier.sql(dialect)

    def column(alias: str, identifier: str | exp.Identifier) -> str:
        return f"{alias}.{name(identifier)}"

    lhs_source, rhs_source = lhs_table.sql(dialect), rhs_table.sql(dialect)
    predicate = on.unnest().sql(dialect)
    key_name, ts_name = name(key.this), name(lhs_column)
    lhs_key, lhs_time = column(LEFT, key.this), column(LEFT, lhs_column)
    tck_key, tck_time = column(TICKS, key.this), column(TICKS, lhs_column)
    rhs_time = column(RIGHT, rhs_column)

    # A right row's timestamp is a tick for every item that reaches it, so both sides are
    # keyed by the left table. UNION, not UNION ALL: both tables may change at once.
    #
    # Which right rows an item reaches follows from the distinct join keys its versions
    # carry, so the second branch joins the distinct pairs. Joining the version rows would
    # multiply the histories together (68k rows per item, for two 260-version histories)
    # for a tick list the UNION deduplicates back down anyway.
    reachable = ", ".join(
        dict.fromkeys(
            [key_name]
            + [name(c.this) for c in on.find_all(exp.Column) if c.table == LEFT]
        )
    )
    ticks = (
        f"SELECT {lhs_key} AS {key_name}, {lhs_time} AS {ts_name} FROM {lhs_source} AS {LEFT} "
        f"UNION "
        f"SELECT {lhs_key} AS {key_name}, {rhs_time} AS {ts_name} "
        f"FROM (SELECT DISTINCT {reachable} FROM {lhs_source}) AS {LEFT} "
        f"JOIN {rhs_source} AS {RIGHT} ON {predicate}"
    )

    # Both forms say the same thing — the latest row at or before the tick — and the right
    # one hangs off the left row rather than off the tick either way, so following the
    # left row to a different right row is part of the history in both.
    #
    # PostgreSQL has no ASOF JOIN and pg_duckdb cannot lend it one, parsing the statement
    # long before DuckDB sees it. LATERAL ... ORDER BY ... LIMIT 1 is the same lookup
    # written out, and an index on (key, timestamp) serves it directly.
    if asof:
        lookups = (
            f"ASOF LEFT JOIN {lhs_source} AS {LEFT} "
            f"ON {lhs_key} = {tck_key} AND {lhs_time} <= {tck_time} "
            f"ASOF LEFT JOIN {rhs_source} AS {RIGHT} "
            f"ON {predicate} AND {rhs_time} <= {tck_time}"
        )
    else:
        lookups = (
            f"LEFT JOIN LATERAL (SELECT {LEFT}.* FROM {lhs_source} AS {LEFT} "
            f"WHERE {lhs_key} = {tck_key} AND {lhs_time} <= {tck_time} "
            f"ORDER BY {lhs_time} DESC LIMIT 1) AS {LEFT} ON TRUE "
            f"LEFT JOIN LATERAL (SELECT {RIGHT}.* FROM {rhs_source} AS {RIGHT} "
            f"WHERE {predicate} AND {rhs_time} <= {tck_time} "
            f"ORDER BY {rhs_time} DESC LIMIT 1) AS {RIGHT} ON TRUE"
        )

    # DISTINCT ON (key, every column) would say this in one line, but it deduplicates
    # globally rather than tick by tick: an issue reopened into a state it held before
    # would keep only the earlier row, the reopening gone and the issue closed ever after.
    # Hence the comparison with the previous tick, and the LAG that is NULL only for the
    # first. WHERE runs before the window functions, so left-less ticks are already gone.
    values = [
        *(column(LEFT, c) for c in _value_columns(evaluator, lhs_table, lhs_column)),
        *(column(RIGHT, c) for c in _value_columns(evaluator, rhs_table, rhs_column)),
    ]
    compared = [f"{v} IS DISTINCT FROM LAG({v}) OVER w" for v in values]
    changed = " OR ".join([f"LAG({lhs_time}) OVER w IS NULL", *compared])
    changes = (
        f"SELECT {key_name}, {ts_name} FROM ("
        f"SELECT {tck_key} AS {key_name}, {tck_time} AS {ts_name}, ({changed}) AS __changed "
        f"FROM ({ticks}) AS {TICKS} {lookups} "
        f"WHERE NOT {lhs_time} IS NULL "
        f"WINDOW w AS (PARTITION BY {tck_key} ORDER BY {tck_time})"
        f") AS __ticks WHERE __changed"
    )

    # The lookups run again over the surviving ticks rather than carrying their rows out
    # of the query above, so `tck` exposes the item and its timeline and nothing else.
    # sqlglot parses statements, not FROM clauses, hence the throwaway SELECT.
    joined = f"(({changes}) AS {TICKS} {lookups})"
    return evaluator.parse_one(f"SELECT 1 FROM {joined}").find(exp.From).this
